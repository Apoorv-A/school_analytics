"""CLI: python -m app.import_.cli validate|apply."""

from __future__ import annotations

import argparse
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from app.db import SessionLocal  # noqa: E402
from app.import_.apply import apply_directory  # noqa: E402
from app.import_.validate import validate_directory  # noqa: E402
from app.models import Tenant  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="School analytics CSV import")
    sub = parser.add_subparsers(dest="command", required=True)

    validate_parser = sub.add_parser("validate", help="Dry-run validation")
    validate_parser.add_argument("--dir", required=True, type=Path)

    apply_parser = sub.add_parser("apply", help="Apply CSV bundle to a tenant")
    apply_parser.add_argument("--dir", required=True, type=Path)
    apply_parser.add_argument("--tenant-id", required=True)

    args = parser.parse_args()
    directory: Path = args.dir
    if not directory.is_dir():
        print(f"Directory not found: {directory}", file=sys.stderr)
        return 1

    if args.command == "validate":
        report = validate_directory(directory)
        print(report)
        return 0 if report["valid"] else 1

    tenant_id = uuid.UUID(args.tenant_id)
    with SessionLocal() as db:
        tenant = db.get(Tenant, tenant_id)
        if tenant is None:
            print("Unknown tenant", file=sys.stderr)
            return 1
        report = apply_directory(db, tenant_id, directory)
        print(report)
        return 0 if report.get("applied") else 1


if __name__ == "__main__":
    raise SystemExit(main())
