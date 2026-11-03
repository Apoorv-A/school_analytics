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


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Fetch an ERP CSV bundle via a connector and apply it to a tenant."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sync_parser = sub.add_parser(
        "sync",
        help="Run one connector sync cycle (local, SFTP, and REST)",
    )
    sync_parser.add_argument("--tenant-id", required=True)
    sync_parser.add_argument(
        "--kind",
        required=True,
        choices=[k.value for k in ConnectorKind],
        help="Connector transport",
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
        help="Secret ref for SFTP host (optional :port); resolves SA_SECRET_<REF>",
    )
    sync_parser.add_argument(
        "--sftp-path-secret-ref",
        default="",
        help="Secret ref for SFTP remote directory (absolute path)",
    )
    sync_parser.add_argument(
        "--sftp-user-secret-ref",
        default="",
        help="Secret ref for SFTP username",
    )
    sync_parser.add_argument(
        "--sftp-password-secret-ref",
        default="",
        help="Secret ref for SFTP password (omit when using private key)",
    )
    sync_parser.add_argument(
        "--sftp-private-key-secret-ref",
        default="",
        help="Secret ref for SFTP private key PEM (optional alternative to password)",
    )
    sync_parser.add_argument(
        "--rest-base-url-secret-ref",
        "--rest-export-url-secret-ref",
        dest="rest_base_url_secret_ref",
        default="",
        help="Secret ref for REST export URL (https in production); SA_SECRET_<REF>",
    )
    sync_parser.add_argument(
        "--rest-bearer-token-secret-ref",
        default="",
        help="Secret ref for REST bearer token (omit when using basic auth)",
    )
    sync_parser.add_argument(
        "--rest-basic-user-secret-ref",
        default="",
        help="Secret ref for REST basic auth username",
    )
    sync_parser.add_argument(
        "--rest-basic-password-secret-ref",
        default="",
        help="Secret ref for REST basic auth password",
    )
    sync_parser.add_argument(
        "--force",
        action="store_true",
        help="Apply even when the bundle fingerprint matches the last successful sync",
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    if args.command != "sync":
        return 1

    try:
        kind = ConnectorKind(args.kind)
        connector = build_connector(
            kind,
            local_dir=args.dir,
            sftp_host_secret_ref=args.sftp_host_secret_ref,
            sftp_path_secret_ref=args.sftp_path_secret_ref,
            sftp_user_secret_ref=args.sftp_user_secret_ref,
            sftp_password_secret_ref=args.sftp_password_secret_ref,
            sftp_private_key_secret_ref=args.sftp_private_key_secret_ref,
            rest_base_url_secret_ref=args.rest_base_url_secret_ref,
            rest_bearer_token_secret_ref=args.rest_bearer_token_secret_ref,
            rest_basic_user_secret_ref=args.rest_basic_user_secret_ref,
            rest_basic_password_secret_ref=args.rest_basic_password_secret_ref,
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
        result = run_connector_sync(db, tenant_id, connector, force_apply=args.force)
        print(json.dumps(result.report, indent=2, default=str))
        if result.applied or result.skipped:
            return 0
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
