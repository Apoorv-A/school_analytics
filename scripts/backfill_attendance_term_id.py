"""Backfill ``attendance.term_id`` for legacy rows where it is NULL.

Maps each row's ``on_date`` to the single academic term that contains that date
(same rule as CSV import). Rows on term boundaries shared by zero or multiple
terms are skipped and reported.

Usage::

    PYTHONPATH=. python scripts/backfill_attendance_term_id.py
    PYTHONPATH=. python scripts/backfill_attendance_term_id.py --dry-run
"""

from __future__ import annotations

import argparse
import sys

from sqlalchemy import select

from app.db import SessionLocal
from app.import_.apply import _resolve_term_for_date
from app.models import Attendance


def backfill(*, dry_run: bool) -> int:
    updated = 0
    skipped = 0
    with SessionLocal() as db:
        rows = db.scalars(
            select(Attendance).where(Attendance.term_id.is_(None)).order_by(Attendance.id)
        ).all()
        for row in rows:
            term = _resolve_term_for_date(db, row.tenant_id, row.on_date)
            if term is None:
                skipped += 1
                print(
                    f"skip attendance id={row.id} "
                    f"student_id={row.student_id} on_date={row.on_date}: "
                    "no unambiguous term",
                    file=sys.stderr,
                )
                continue
            if not dry_run:
                row.term_id = term.id
            updated += 1
        if not dry_run:
            db.commit()
    mode = "would update" if dry_run else "updated"
    print(f"{mode} {updated} attendance row(s); skipped {skipped}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Backfill attendance.term_id from on_date"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="report changes without writing to the database",
    )
    args = parser.parse_args(argv)
    return backfill(dry_run=args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
