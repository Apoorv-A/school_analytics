"""Phase 2 acceptance tests."""

from __future__ import annotations

from sqlalchemy import select

from app.import_.history import record_import_run
from app.models import ImportRunStatus, Role, User


class TestImportRunAudit:
    def test_record_and_list_runs(self, db):
        from app.models import Tenant

        tenant = db.scalars(select(Tenant).where(Tenant.key == "sunrise")).one()
        admin = db.scalars(
            select(User).where(User.role == Role.ADMIN, User.tenant_id == tenant.id).limit(1)
        ).one()
        record_import_run(
            db,
            tenant_id=tenant.id,
            status=ImportRunStatus.VALIDATED,
            source="upload",
            report={"summary": {"scores.csv": 12}, "errors": []},
            user_id=admin.id,
        )
        db.commit()

        from app.import_.history import recent_import_runs

        runs = recent_import_runs(db, tenant.id)
        assert len(runs) >= 1
        assert runs[0].status == ImportRunStatus.VALIDATED
        assert runs[0].source == "upload"


class TestStudentSearchBrowse:
    def test_empty_query_returns_browse_list(self, admin_client, db):
        from sqlalchemy import select

        from app.models import Student

        count = db.scalars(select(Student.id)).all()
        if not count:
            return
        response = admin_client.get("/api/students/search", params={"q": ""})
        assert response.status_code == 200
        assert len(response.json()["results"]) >= 1

    def test_single_char_prefix(self, admin_client, db):
        from sqlalchemy import select

        from app.models import Student

        student = db.scalars(select(Student).limit(1)).one()
        prefix = student.full_name[0].lower()
        response = admin_client.get("/api/students/search", params={"q": prefix})
        assert response.status_code == 200
        ids = {row["id"] for row in response.json()["results"]}
        assert student.id in ids
