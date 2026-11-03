"""ERP bundle fingerprint and connector delta skip."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import select

from app.import_.bundle_fingerprint import compute_bundle_fingerprint
from app.import_.connectors.local import LocalPathConnector
from app.import_.connectors.runner import run_connector_sync
from app.import_.sync_state import get_stored_fingerprint
from app.models import ImportRun, ImportRunStatus, Tenant


def _write_csv(path: Path, header: str, row: str) -> None:
    path.write_text(f"{header}\n{row}\n", encoding="utf-8")


@pytest.fixture
def delta_bundle_dir(tmp_path: Path) -> Path:
    _write_csv(
        tmp_path / "school_structure.csv",
        "grade_level,grade_name,section_name,subject_code,subject_name",
        "8,Delta Grade,DELTA,DELTASUB,Delta Subject",
    )
    return tmp_path


class TestBundleFingerprint:
    def test_stable_for_same_content(self, delta_bundle_dir: Path) -> None:
        first = compute_bundle_fingerprint(delta_bundle_dir)
        second = compute_bundle_fingerprint(delta_bundle_dir)
        assert first == second
        assert len(first) == 64

    def test_changes_when_csv_bytes_change(self, delta_bundle_dir: Path) -> None:
        before = compute_bundle_fingerprint(delta_bundle_dir)
        path = delta_bundle_dir / "school_structure.csv"
        path.write_text(path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        after = compute_bundle_fingerprint(delta_bundle_dir)
        assert before != after


class TestConnectorDeltaSkip:
    def test_second_sync_skips_unchanged_bundle(
        self, db, delta_bundle_dir: Path
    ) -> None:
        tenant = db.scalars(select(Tenant).where(Tenant.key == "sunrise")).one()
        connector = LocalPathConnector(delta_bundle_dir)

        first = run_connector_sync(db, tenant.id, connector)
        assert first.applied is True
        assert first.skipped is False
        stored = get_stored_fingerprint(db, tenant.id, "sync:local")
        assert stored is not None

        second = run_connector_sync(db, tenant.id, connector)
        assert second.applied is False
        assert second.skipped is True
        assert second.report.get("delta", {}).get("reason") == "bundle_unchanged"

        skipped_run = db.scalars(
            select(ImportRun)
            .where(
                ImportRun.tenant_id == tenant.id,
                ImportRun.source == "sync:local",
                ImportRun.status == ImportRunStatus.SKIPPED,
            )
            .order_by(ImportRun.created_at.desc())
        ).first()
        assert skipped_run is not None

    def test_changed_bundle_applies_again(self, db, delta_bundle_dir: Path) -> None:
        tenant = db.scalars(select(Tenant).where(Tenant.key == "sunrise")).one()
        connector = LocalPathConnector(delta_bundle_dir)
        run_connector_sync(db, tenant.id, connector)

        path = delta_bundle_dir / "school_structure.csv"
        text = path.read_text(encoding="utf-8")
        path.write_text(text.replace("Delta Grade", "Delta Grade v2"), encoding="utf-8")

        result = run_connector_sync(db, tenant.id, connector)
        assert result.applied is True
        assert result.skipped is False

    def test_force_reapplies_unchanged_bundle(self, db, delta_bundle_dir: Path) -> None:
        tenant = db.scalars(select(Tenant).where(Tenant.key == "sunrise")).one()
        connector = LocalPathConnector(delta_bundle_dir)
        run_connector_sync(db, tenant.id, connector)

        forced = run_connector_sync(db, tenant.id, connector, force_apply=True)
        assert forced.applied is True
        assert forced.skipped is False

    def test_failed_apply_does_not_store_fingerprint(
        self, db, tmp_path: Path
    ) -> None:
        from app.models import ImportSyncState

        tenant = db.scalars(select(Tenant).where(Tenant.key == "sunrise")).one()
        for row in db.scalars(
            select(ImportSyncState).where(
                ImportSyncState.tenant_id == tenant.id,
                ImportSyncState.source == "sync:local",
            )
        ):
            db.delete(row)
        db.commit()

        _write_csv(
            tmp_path / "school_structure.csv",
            "grade_level,grade_name,section_name,subject_code,subject_name",
            "not-a-number,Bad,ROW,BAD,Bad",
        )
        connector = LocalPathConnector(tmp_path)
        result = run_connector_sync(db, tenant.id, connector)
        assert result.applied is False
        assert get_stored_fingerprint(db, tenant.id, "sync:local") is None
