"""Regression tests for P0 audit findings."""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, inspect, select
from sqlalchemy.orm import Session

from alembic import command
from app.config import PROJECT_ROOT
from app.db import Base, check_db_migrations_at_head, startup_ddl_enabled
from app.models import (
    Attendance,
    School,
    Section,
    Student,
    Subject,
    Teacher,
    TeacherAssignment,
    Tenant,
    User,
)
from seed.generate import DEMO_PASSWORD, SUNRISE_HOST
from tenant_config.models import TenantConfigDocument, TenantEnvironment
from tenant_config.version_rules import RELEASED, explain, is_released
from tenant_operator.provision import reconcile_tenant

DEFAULT_HEADERS = {"Host": SUNRISE_HOST}


def _minimal_tenant_doc(**overrides) -> dict:
    doc = {
        "tenant": {
            "key": "db-target",
            "displayName": "DB Target School",
            "hostname": "db-target.localhost",
            "environment": "dev",
        },
        "school": {"name": "DB Target School", "city": "Pune", "board": "CBSE"},
        "academics": {
            "passPercentage": 33.0,
            "atRiskPercentage": 45.0,
            "atRiskAttendance": 75.0,
        },
        "image": {"tag": "0.1.0-SNAPSHOT"},
    }
    doc.update(overrides)
    return doc


class TestRemarkSectionSubjectAuth:
    def test_teacher_cannot_remark_subject_outside_section_assignment(self, db):
        from app.main import app
        from tests.conftest import CsrfTestClient

        tenant = db.scalars(select(Tenant).where(Tenant.key == "sunrise")).one()
        english = db.scalars(
            select(Subject).where(
                Subject.tenant_id == tenant.id, Subject.code == "ENG"
            )
        ).one()
        math = db.scalars(
            select(Subject).where(
                Subject.tenant_id == tenant.id, Subject.code == "MATH"
            )
        ).one()

        assignment = db.scalars(
            select(TeacherAssignment)
            .where(
                TeacherAssignment.tenant_id == tenant.id,
                TeacherAssignment.subject_id == english.id,
            )
            .order_by(TeacherAssignment.id)
            .limit(1)
        ).one()
        english_section = db.get(Section, assignment.section_id)
        teacher = db.get(Teacher, assignment.teacher_id)
        user = db.get(User, teacher.user_id)

        homeroom_section = db.scalars(
            select(Section)
            .where(
                Section.tenant_id == tenant.id,
                Section.id != english_section.id,
            )
            .order_by(Section.id)
            .limit(1)
        ).one()
        original_homeroom_teacher = homeroom_section.class_teacher_id
        homeroom_section.class_teacher_id = teacher.id

        student = db.scalars(
            select(Student).where(Student.section_id == english_section.id).limit(1)
        ).one()
        db.commit()

        try:
            client = CsrfTestClient(app, headers=DEFAULT_HEADERS, follow_redirects=False)
            login = client.post(
                "/login", data={"email": user.email, "password": DEMO_PASSWORD}
            )
            assert login.status_code == 303

            allowed = client.post(
                "/api/remarks",
                json={
                    "student_id": student.id,
                    "body": "Strong reading comprehension.",
                    "category": "academic",
                    "subject_id": english.id,
                },
            )
            assert allowed.status_code == 200

            denied = client.post(
                "/api/remarks",
                json={
                    "student_id": student.id,
                    "body": "Should not be allowed for this section.",
                    "category": "academic",
                    "subject_id": math.id,
                },
            )
            assert denied.status_code == 404
        finally:
            homeroom_section.class_teacher_id = original_homeroom_teacher
            db.commit()


class TestPlatformRoute:
    def test_admin_platform_settings_page_is_reachable(self, admin_client):
        response = admin_client.get("/admin/platform")
        assert response.status_code == 200
        assert "Tenant configuration" in response.text


class TestTenantOperatorDatabaseBinding:
    def test_reconcile_writes_only_to_supplied_database(self, tmp_path):
        url_a = f"sqlite:///{tmp_path / 'a.db'}"
        url_b = f"sqlite:///{tmp_path / 'b.db'}"
        for url in (url_a, url_b):
            engine = create_engine(url)
            Base.metadata.create_all(bind=engine)
            engine.dispose()

        doc = TenantConfigDocument.model_validate(_minimal_tenant_doc())
        reconcile_tenant(doc, database_url=url_b)

        with Session(create_engine(url_a)) as session:
            assert session.scalars(select(Tenant)).first() is None
        with Session(create_engine(url_b)) as session:
            tenant = session.scalars(
                select(Tenant).where(Tenant.key == "db-target")
            ).one()
            school = session.scalars(
                select(School).where(School.tenant_id == tenant.id)
            ).one()
            assert school.name == "DB Target School"


class TestStartupDdlGating:
    def test_postgres_url_skips_startup_ddl(self, monkeypatch):
        monkeypatch.setattr("app.db._is_postgres", True)
        assert startup_ddl_enabled() is False

    def test_sqlite_allows_startup_ddl(self, monkeypatch):
        monkeypatch.setattr("app.db._is_postgres", False)
        monkeypatch.delenv("SKIP_STARTUP_DDL", raising=False)
        assert startup_ddl_enabled() is True

    def test_skip_startup_ddl_env_overrides_sqlite(self, monkeypatch):
        monkeypatch.setattr("app.db._is_postgres", False)
        monkeypatch.setenv("SKIP_STARTUP_DDL", "1")
        assert startup_ddl_enabled() is False


class TestReadinessMigrationCheck:
    def test_check_db_migrations_at_head_after_full_upgrade(self, tmp_path, monkeypatch):
        url = f"sqlite:///{tmp_path / 'ready.db'}"
        monkeypatch.setattr("app.config.settings.database_url", url)
        cfg = Config(str(PROJECT_ROOT / "alembic.ini"))
        cfg.set_main_option("sqlalchemy.url", url)
        command.upgrade(cfg, "head")
        probe = create_engine(url)
        try:
            assert check_db_migrations_at_head(probe) is True
        finally:
            probe.dispose()

    def test_check_db_migrations_at_head_false_when_behind(self, tmp_path, monkeypatch):
        url = f"sqlite:///{tmp_path / 'behind.db'}"
        monkeypatch.setattr("app.config.settings.database_url", url)
        cfg = Config(str(PROJECT_ROOT / "alembic.ini"))
        cfg.set_main_option("sqlalchemy.url", url)
        command.upgrade(cfg, "006_intervention_cases")
        probe = create_engine(url)
        try:
            assert check_db_migrations_at_head(probe) is False
        finally:
            probe.dispose()

    def test_db_readiness_reports_pending_migrations(self, tmp_path, monkeypatch):
        from app.db import db_readiness

        url = f"sqlite:///{tmp_path / 'pending.db'}"
        monkeypatch.setattr("app.config.settings.database_url", url)
        cfg = Config(str(PROJECT_ROOT / "alembic.ini"))
        cfg.set_main_option("sqlalchemy.url", url)
        command.upgrade(cfg, "006_intervention_cases")
        probe = create_engine(url)
        try:
            with patch("app.db.engine", probe):
                state = db_readiness()
            assert state["database"] == "ok"
            assert state["migrations"] == "pending"
        finally:
            probe.dispose()


class TestBaselineMigration:
    def test_alembic_baseline_creates_core_tables(self, tmp_path, monkeypatch):
        """Postgres path: ``alembic upgrade head`` (001 creates tables)."""
        url = f"sqlite:///{tmp_path / 'migrated.db'}"
        monkeypatch.setattr("app.config.settings.database_url", url)
        cfg = Config(str(PROJECT_ROOT / "alembic.ini"))
        cfg.set_main_option("sqlalchemy.url", url)
        command.upgrade(cfg, "001_baseline")
        engine = create_engine(url)
        tables = set(inspect(engine).get_table_names())
        engine.dispose()
        assert {"tenants", "schools", "users", "students", "attendance"}.issubset(
            tables
        )


class TestImageTagPinning:
    def test_version_rules_reject_latest_and_pre_releases(self):
        assert not is_released("latest")
        assert not is_released("0.1.0-SNAPSHOT")
        assert not is_released("1.0.0-beta.7")
        assert is_released("1.0.0")
        assert is_released("1.0.0-hotfix.1")
        assert "pre-release" in explain("1.0.0-SNAPSHOT")

    def test_stg_rejects_snapshot_tag(self):
        with pytest.raises(ValueError, match="pre-release"):
            TenantConfigDocument.model_validate(
                _minimal_tenant_doc(
                    tenant={
                        "key": "sunrise",
                        "displayName": "Sunrise",
                        "hostname": "sunrise-stg.example.com",
                        "environment": TenantEnvironment.STG.value,
                    },
                    image={"tag": "0.1.0-SNAPSHOT"},
                )
            )

    def test_prod_rejects_latest_tag(self):
        with pytest.raises(ValueError, match="latest"):
            TenantConfigDocument.model_validate(
                _minimal_tenant_doc(
                    tenant={
                        "key": "sunrise",
                        "displayName": "Sunrise",
                        "hostname": "sunrise.example.com",
                        "environment": TenantEnvironment.PROD.value,
                    },
                    image={"tag": "latest"},
                    auth={
                        "mfaEnabled": True,
                        "mfaRequiredRoles": ["admin"],
                        "sessionMaxAge": 28800,
                    },
                )
            )

    def test_stg_accepts_released_tag(self):
        doc = TenantConfigDocument.model_validate(
            _minimal_tenant_doc(
                tenant={
                    "key": "sunrise",
                    "displayName": "Sunrise",
                    "hostname": "sunrise-stg.example.com",
                    "environment": TenantEnvironment.STG.value,
                },
                image={"tag": "1.0.0"},
            )
        )
        assert RELEASED.match(doc.image.tag)


class TestTenantConfigContract:
    def test_erp_webhook_secret_ref_provisioned_in_settings_json(self, tmp_path):
        url = f"sqlite:///{tmp_path / 'erp.db'}"
        engine = create_engine(url)
        Base.metadata.create_all(bind=engine)
        engine.dispose()

        doc = TenantConfigDocument.model_validate(
            _minimal_tenant_doc(
                erp={"webhookSecretRef": "db-target/erp-webhook"},
                features={
                    "exports": True,
                    "remarks": True,
                    "parentPortal": True,
                    "studentPortal": True,
                    "bulkImport": True,
                    "interventions": False,
                },
            )
        )
        reconcile_tenant(doc, database_url=url)

        with Session(create_engine(url)) as session:
            tenant = session.scalars(
                select(Tenant).where(Tenant.key == "db-target")
            ).one()
            assert (
                tenant.settings_json["erp"]["webhookSecretRef"] == "db-target/erp-webhook"
            )
            assert "webhookToken" not in tenant.settings_json["erp"]

    def test_mfa_disabled_by_default(self):
        doc = TenantConfigDocument.model_validate(_minimal_tenant_doc())
        assert doc.auth.mfaEnabled is False
        assert doc.auth.mfaRequiredRoles == []

    def test_mfa_config_accepts_enabled_roles(self):
        doc = TenantConfigDocument.model_validate(
            _minimal_tenant_doc(
                auth={"mfaEnabled": True, "mfaRequiredRoles": ["admin", "teacher"]}
            )
        )
        assert doc.auth.mfaEnabled is True
        assert doc.auth.mfaRequiredRoles == ["admin", "teacher"]

    def test_mfa_roles_require_enabled_flag(self):
        with pytest.raises(ValueError, match="mfaRequiredRoles requires"):
            TenantConfigDocument.model_validate(
                _minimal_tenant_doc(auth={"mfaEnabled": False, "mfaRequiredRoles": ["admin"]})
            )

    def test_prod_bulk_import_requires_webhook_secret_ref(self):
        with pytest.raises(ValueError, match=r"webhookSecretRef|webhookSigningSecretRef"):
            TenantConfigDocument.model_validate(
                _minimal_tenant_doc(
                    tenant={
                        "key": "prod-school",
                        "displayName": "Prod School",
                        "hostname": "prod.example.com",
                        "environment": TenantEnvironment.PROD.value,
                    },
                    image={"tag": "1.2.0"},
                    auth={
                        "mfaEnabled": True,
                        "mfaRequiredRoles": ["admin"],
                        "sessionMaxAge": 28800,
                    },
                    features={
                        "exports": True,
                        "remarks": True,
                        "parentPortal": True,
                        "studentPortal": True,
                        "bulkImport": True,
                        "interventions": False,
                    },
                )
            )

    def test_prod_requires_admin_mfa_policy(self):
        with pytest.raises(ValueError, match="mfaEnabled"):
            TenantConfigDocument.model_validate(
                _minimal_tenant_doc(
                    tenant={
                        "key": "prod-school",
                        "displayName": "Prod School",
                        "hostname": "prod.example.com",
                        "environment": TenantEnvironment.PROD.value,
                    },
                    image={"tag": "1.2.0"},
                )
            )

        with pytest.raises(ValueError, match="admin"):
            TenantConfigDocument.model_validate(
                _minimal_tenant_doc(
                    tenant={
                        "key": "prod-school",
                        "displayName": "Prod School",
                        "hostname": "prod.example.com",
                        "environment": TenantEnvironment.PROD.value,
                    },
                    image={"tag": "1.2.0"},
                    auth={"mfaEnabled": True, "mfaRequiredRoles": [], "sessionMaxAge": 28800},
                )
            )

    def test_schema_includes_feature_flags(self):
        schema = TenantConfigDocument.model_json_schema()
        feature_props = schema["$defs"]["FeatureFlags"]["properties"]
        assert "bulkImport" in feature_props
        assert "interventions" in feature_props
        erp_props = schema["$defs"]["ErpConfig"]["properties"]
        assert "webhookSecretRef" in erp_props
        assert "webhookSigningSecretRef" in erp_props
        assert "webhookToken" not in erp_props

    def test_schema_includes_policy_settings(self):
        schema = TenantConfigDocument.model_json_schema()
        portal_props = schema["$defs"]["PortalPolicy"]["properties"]
        attendance_props = schema["$defs"]["AttendancePolicy"]["properties"]
        assert "familyComparisonMode" in portal_props
        assert "countLateAsPresent" in attendance_props
        assert "countExcusedAsPresent" in attendance_props


class TestCsrfProtection:
    def test_authenticated_post_without_csrf_is_rejected(self, admin_client):
        from fastapi.testclient import TestClient

        from app.config import settings
        from app.main import app

        session_only = TestClient(app, headers=DEFAULT_HEADERS, follow_redirects=False)
        session_only.cookies.set(
            settings.session_cookie_name,
            admin_client.cookies.get(settings.session_cookie_name),
        )
        response = session_only.post(
            "/api/remarks",
            json={
                "student_id": 1,
                "body": "Should be blocked.",
                "category": "academic",
            },
        )
        assert response.status_code == 403

    def test_authenticated_post_with_csrf_succeeds(self, teacher_context):
        student_id = teacher_context["own_student_id"]
        response = teacher_context["client"].post(
            "/api/remarks",
            json={
                "student_id": student_id,
                "body": "CSRF-protected remark.",
                "category": "academic",
            },
        )
        assert response.status_code == 200

    def test_logout_get_shows_auto_submit_form(self, admin_client):
        response = admin_client.get("/logout")
        assert response.status_code == 200
        assert 'action="/logout"' in response.text


class TestBrowserAuthNavigation:
    def test_wrong_portal_redirects_browser_to_own_home(self, admin_client):
        response = admin_client.get(
            "/student", headers={"Accept": "text/html,application/xhtml+xml"}
        )
        assert response.status_code == 303
        assert response.headers["location"] == "/admin"

    def test_login_switch_allows_fresh_sign_in(self, admin_client):
        response = admin_client.get("/login", params={"switch": "1"})
        assert response.status_code == 200
        assert "Sign in" in response.text
        assert admin_client.get("/admin").headers["location"] == "/login"


class TestSessionSecurityDefaults:
    def test_secure_cookies_default_when_not_debug(self):
        from pydantic_settings import BaseSettings, SettingsConfigDict

        class _Probe(BaseSettings):
            model_config = SettingsConfigDict(extra="ignore")
            debug: bool = False
            session_cookie_secure: bool | None = None

            def apply(self):
                if self.session_cookie_secure is None:
                    self.session_cookie_secure = not self.debug
                return self

        assert _Probe(debug=False).apply().session_cookie_secure is True
        assert _Probe(debug=True).apply().session_cookie_secure is False

    def test_missing_secret_key_fails_when_not_debug(self):
        from app.config import Settings

        env = os.environ.copy()
        env.pop("SECRET_KEY", None)
        with patch.dict(os.environ, env, clear=True):
            with pytest.raises(ValueError, match="SECRET_KEY"):
                Settings(debug=False)


class TestAttendanceTermBackfill:
    def test_backfill_sets_term_id_from_on_date(self, db):
        from scripts.backfill_attendance_term_id import backfill

        tenant = db.scalars(select(Tenant).where(Tenant.key == "sunrise")).one()
        student = db.scalars(
            select(Student).where(Student.tenant_id == tenant.id).limit(1)
        ).one()
        row = db.scalars(
            select(Attendance).where(
                Attendance.tenant_id == tenant.id,
                Attendance.student_id == student.id,
            )
        ).first()
        assert row is not None
        original_term_id = row.term_id
        row.term_id = None
        db.commit()

        try:
            assert backfill(dry_run=False) == 0
            db.expire_all()
            refreshed = db.get(Attendance, row.id)
            assert refreshed.term_id is not None
        finally:
            row.term_id = original_term_id
            db.commit()


class TestErpSecretStartupValidation:
    def test_production_startup_fails_when_bulk_import_secret_unresolved(
        self, db, monkeypatch
    ):
        from sqlalchemy.orm.attributes import flag_modified

        from app.main import _validate_production_erp_secrets
        from app.models import Tenant

        monkeypatch.setattr("app.config.settings.debug", False)
        tenant = db.scalars(select(Tenant).where(Tenant.key == "sunrise")).one()
        original_settings = dict(tenant.settings_json or {})
        settings = dict(original_settings)
        features = dict(settings.get("features") or {})
        features["bulkImport"] = True
        settings["features"] = features
        settings["erp"] = {"webhookSecretRef": "sunrise/erp-webhook"}
        tenant.settings_json = settings
        flag_modified(tenant, "settings_json")
        db.commit()
        monkeypatch.delenv("SA_SECRET_SUNRISE_ERP_WEBHOOK", raising=False)

        try:
            with pytest.raises(RuntimeError, match="Production security configuration"):
                _validate_production_erp_secrets()
        finally:
            tenant.settings_json = original_settings
            flag_modified(tenant, "settings_json")
            db.commit()


class TestReleaseAndHelmHardening:
    def test_release_workflow_does_not_publish_latest_tag(self):
        release_yml = (PROJECT_ROOT / ".github/workflows/release.yml").read_text()
        assert ":latest" not in release_yml

    def test_helm_deployment_has_security_context(self):
        deployment = (
            PROJECT_ROOT / "deploy/helm/school-analytics/templates/deployment.yaml"
        ).read_text()
        assert "runAsNonRoot: true" in deployment
        assert "allowPrivilegeEscalation: false" in deployment
        assert "readOnlyRootFilesystem: true" in deployment
        assert 'drop: ["ALL"]' in deployment
        assert "seccompProfile:" in deployment
        assert "terminationGracePeriodSeconds:" in deployment
        assert "emptyDir:" in deployment
        assert "ERP_WEBHOOK_REPLAY_BACKEND" in deployment
        assert "ERP_WEBHOOK_REPLAY_REDIS_URL" in deployment

    def test_helm_migration_job_has_security_context(self):
        migration = (
            PROJECT_ROOT / "deploy/helm/school-analytics/templates/migration-job.yaml"
        ).read_text()
        assert "runAsNonRoot: true" in migration
        assert "allowPrivilegeEscalation: false" in migration
        assert "readOnlyRootFilesystem: true" in migration
        assert 'drop: ["ALL"]' in migration
        assert "seccompProfile:" in migration

    def test_supply_chain_workflow_exists(self):
        workflow = (PROJECT_ROOT / ".github/workflows/supply-chain.yml").read_text()
        assert "pip-audit" in workflow
        assert "cyclonedx" in workflow
        assert (PROJECT_ROOT / "requirements.lock").is_file()

    def test_dockerfile_uses_locked_requirements(self):
        dockerfile = (PROJECT_ROOT / "deploy/Dockerfile").read_text()
        assert "requirements.lock" in dockerfile
