"""CLI: scheduled ERP connector sync (CronJob / operator entry point)."""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from app.db import SessionLocal  # noqa: E402
from app.import_.connectors.errors import ConnectorConfigError  # noqa: E402
from app.import_.connectors.factory import build_connector  # noqa: E402
from app.import_.connectors.runner import run_connector_sync  # noqa: E402
from app.import_.connectors.types import ConnectorKind  # noqa: E402
from app.models import Tenant  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Fetch an ERP CSV bundle via a connector and apply it to a tenant."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sync_parser = sub.add_parser(
        "sync",
        help="Run one connector sync cycle (local drop directory is supported today)",
    )
    sync_parser.add_argument("--tenant-id", required=True)
    sync_parser.add_argument(
        "--kind",
        required=True,
        choices=[k.value for k in ConnectorKind],
        help="Connector transport (sftp/rest are stubs)",
    )
    sync_parser.add_argument(
        "--dir",
        type=Path,
        default=None,
        help="Absolute path to CSV bundle directory (required for kind=local)",
    )
    sync_parser.add_argument(
        "--sftp-host-secret-ref",
        default="",
        help="Deployment secret ref for SFTP host (stub)",
    )
    sync_parser.add_argument(
        "--sftp-path-secret-ref",
        default="",
        help="Deployment secret ref for SFTP remote path (stub)",
    )
    sync_parser.add_argument(
        "--rest-base-url-secret-ref",
        default="",
        help="Deployment secret ref for REST base URL (stub)",
    )

    args = parser.parse_args()
    if args.command != "sync":
        return 1

    try:
        kind = ConnectorKind(args.kind)
        connector = build_connector(
            kind,
            local_dir=args.dir,
            sftp_host_secret_ref=args.sftp_host_secret_ref,
            sftp_path_secret_ref=args.sftp_path_secret_ref,
            rest_base_url_secret_ref=args.rest_base_url_secret_ref,
        )
    except ConnectorConfigError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    tenant_id = uuid.UUID(args.tenant_id)
    with SessionLocal() as db:
        tenant = db.get(Tenant, tenant_id)
        if tenant is None:
            print("Unknown tenant", file=sys.stderr)
            return 1
        result = run_connector_sync(db, tenant_id, connector)
        print(json.dumps(result.report, indent=2, default=str))
        return 0 if result.applied else 1


if __name__ == "__main__":
    raise SystemExit(main())
