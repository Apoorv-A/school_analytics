#!/usr/bin/env python3
"""Seed one pending import conflict on Sunrise for local admin UI demo.

Idempotent: leaves a single pending conflict for the demo score row
(``external_id=score-conflict-demo``). Safe for ``DATABASE_URL=sqlite:///./school.db``.

Usage::

    alembic upgrade head
    python -m seed.generate          # or existing Sunrise demo DB
    PYTHONPATH=. python scripts/seed_import_conflict_demo.py

Then open Admin → Data import at http://sunrise.localhost:8000/admin/import
"""

from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
import uuid
from datetime import date
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app.db import SessionLocal
from app.import_.apply import apply_directory
from app.import_.conflicts import ANALYTICS_SOURCE_SYSTEM
from app.models import (
    AcademicYear,
    Assessment,
    AssessmentType,
    Grade,
    ImportConflict,
    ImportConflictStatus,
    Score,
    Section,
    Student,
    Subject,
    Tenant,
    Term,
)

TENANT_KEY = "sunrise"
DEMO_ASSESSMENT_CODE = "DEMO-CONFLICT-UT"
DEMO_SCORE_EXTERNAL_ID = "score-conflict-demo"
ERP_MARKS_FIRST = 72.0
LOCAL_MARKS = 85.0
ERP_MARKS_INCOMING = 90.0


def _write_csv(path: Path, header: str, row: str) -> None:
    path.write_text(f"{header}\n{row}\n", encoding="utf-8")


def _student_grade_6a(db: Session) -> Student:
    return db.scalars(
        select(Student)
        .join(Section, Student.section_id == Section.id)
        .join(Grade, Section.grade_id == Grade.id)
        .where(Grade.level == 6, Section.name == "A")
        .limit(1)
    ).one()


def _ensure_bulk_import(db: Session, *, tenant_key: str = TENANT_KEY) -> None:
    tenant = db.scalars(select(Tenant).where(Tenant.key == tenant_key)).one()
    settings = dict(tenant.settings_json or {})
    features = dict(settings.get("features") or {})
    if not features.get("bulkImport"):
        features["bulkImport"] = True
        settings["features"] = features
        tenant.settings_json = settings
        flag_modified(tenant, "settings_json")
        db.commit()


def _ensure_demo_assessment(
    db: Session, tenant_id: uuid.UUID, student: Student
) -> Assessment:
    existing = db.scalars(
        select(Assessment).where(
            Assessment.tenant_id == tenant_id,
            Assessment.external_id == DEMO_ASSESSMENT_CODE,
        )
    ).first()
    if existing is not None:
        return existing

    section = db.get(Section, student.section_id)
    subject = db.scalars(
        select(Subject).where(Subject.tenant_id == tenant_id).limit(1)
    ).one()
    year = db.get(AcademicYear, section.academic_year_id)
    term = db.scalars(
        select(Term).where(Term.academic_year_id == year.id).limit(1)
    ).one()
    assessment = Assessment(
        tenant_id=tenant_id,
        name="Import conflict demo unit test",
        assessment_type=AssessmentType.UNIT_TEST,
        subject_id=subject.id,
        section_id=section.id,
        term_id=term.id,
        max_marks=100.0,
        conducted_on=date(2025, 8, 1),
        external_id=DEMO_ASSESSMENT_CODE,
    )
    db.add(assessment)
    db.commit()
    return assessment


def _pending_demo_conflict(db: Session, tenant_id: uuid.UUID) -> ImportConflict | None:
    score = db.scalars(
        select(Score).where(
            Score.tenant_id == tenant_id,
            Score.external_id == DEMO_SCORE_EXTERNAL_ID,
        )
    ).first()
    if score is None:
        return None
    return db.scalars(
        select(ImportConflict).where(
            ImportConflict.tenant_id == tenant_id,
            ImportConflict.entity_id == score.id,
            ImportConflict.status == ImportConflictStatus.PENDING,
        )
    ).first()


def _apply_demo_bundle(
    db: Session,
    tenant_id: uuid.UUID,
    directory: Path,
    student: Student,
    *,
    marks: float,
    source_system: str,
    import_source: str,
) -> dict:
    _write_csv(
        directory / "scores.csv",
        "admission_no,assessment_code,marks_obtained,is_absent,external_id,source_system",
        (
            f"{student.admission_no},{DEMO_ASSESSMENT_CODE},{marks},false,"
            f"{DEMO_SCORE_EXTERNAL_ID},{source_system}"
        ),
    )
    report = apply_directory(db, tenant_id, directory, source=import_source)
    db.commit()
    return report


def seed_conflict(*, tenant_key: str = TENANT_KEY) -> int:
    with SessionLocal() as db:
        tenant = db.scalars(select(Tenant).where(Tenant.key == tenant_key)).one()
        _ensure_bulk_import(db, tenant_key=tenant_key)
        pending = _pending_demo_conflict(db, tenant.id)
        if pending is not None:
            print(
                f"OK: pending import conflict id={pending.id} already exists "
                f"(score external_id={DEMO_SCORE_EXTERNAL_ID!r})."
            )
            return 0

        student = _student_grade_6a(db)
        _ensure_demo_assessment(db, tenant.id, student)

        with tempfile.TemporaryDirectory(prefix="conflict-demo-") as tmp:
            directory = Path(tmp)
            _apply_demo_bundle(
                db,
                tenant.id,
                directory,
                student,
                marks=ERP_MARKS_FIRST,
                source_system="erp-v1",
                import_source="webhook",
            )
            score = db.scalars(
                select(Score).where(
                    Score.tenant_id == tenant.id,
                    Score.external_id == DEMO_SCORE_EXTERNAL_ID,
                )
            ).one()
            score.marks_obtained = LOCAL_MARKS
            score.source_system = ANALYTICS_SOURCE_SYSTEM
            db.commit()

            shutil.rmtree(directory)
            directory.mkdir()
            report = _apply_demo_bundle(
                db,
                tenant.id,
                directory,
                student,
                marks=ERP_MARKS_INCOMING,
                source_system="erp-v2",
                import_source="webhook",
            )

        conflicts_queued = int(report.get("conflicts_queued") or 0)
        db.refresh(score)
        if conflicts_queued != 1 or score.marks_obtained != LOCAL_MARKS:
            print(
                "ERROR: expected conflicts_queued=1 and local marks preserved; "
                f"got conflicts_queued={conflicts_queued!r}, "
                f"marks_obtained={score.marks_obtained!r}, report={report!r}",
                file=sys.stderr,
            )
            return 1

        conflict = _pending_demo_conflict(db, tenant.id)
        if conflict is None:
            print("ERROR: apply reported a conflict but none is pending.", file=sys.stderr)
            return 1

        print(
            f"OK: queued import conflict id={conflict.id} "
            f"(local marks={LOCAL_MARKS}, ERP={ERP_MARKS_INCOMING}). "
            "Open /admin/import on sunrise host."
        )
        return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--tenant",
        default=TENANT_KEY,
        help=f"Tenant key (default: {TENANT_KEY})",
    )
    args = parser.parse_args(argv)
    return seed_conflict(tenant_key=args.tenant)


if __name__ == "__main__":
    raise SystemExit(main())
