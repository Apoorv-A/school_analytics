"""Backup helper for School Analytics databases.

Creates either a full database backup or a tenant-scoped JSON export.

Usage::

    PYTHONPATH=. python scripts/backup_tenant_data.py --output ./backups/school.db.bak
    PYTHONPATH=. python scripts/backup_tenant_data.py \\
        --tenant-key sunrise --output ./backups/sunrise.json

Output paths are resolved and must not contain ``..`` components.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.config import PROJECT_ROOT, settings
from app.models import Tenant, TenantDomain
from app.tenant.rls import RLS_TENANT_TABLES

_DEFAULT_BACKUP_DIR = PROJECT_ROOT / "backups"


def _safe_output_path(raw: str) -> Path:
    """Resolve an output path; reject traversal segments."""
    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        candidate = (_DEFAULT_BACKUP_DIR / candidate).resolve()
    else:
        candidate = candidate.resolve()
    for part in candidate.parts:
        if part == "..":
            raise ValueError(f"Unsafe output path: {raw}")
    return candidate


def _engine_for_url(database_url: str) -> Engine:
    connect_args: dict = {}
    if database_url.startswith("sqlite"):
        connect_args["check_same_thread"] = False
    return create_engine(database_url, future=True, connect_args=connect_args)


def _is_sqlite(url: str) -> bool:
    return url.startswith("sqlite")


def _is_postgres(url: str) -> bool:
    return url.startswith("postgresql")


def _tenant_id_param(engine: Engine, tenant_id) -> str:
    """Bind tenant_id in the format the active dialect stores."""
    if engine.dialect.name == "sqlite":
        return tenant_id.hex
    return str(tenant_id)


def backup_sqlite_full(source_url: str, destination: Path) -> None:
    """Copy a SQLite database file (and WAL sidecars when present)."""
    parsed = urlparse(source_url.replace("sqlite:///", "file://", 1))
    source_path = Path(parsed.path)
    if not source_path.is_file():
        raise FileNotFoundError(f"SQLite database file not found: {source_path}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source_path, destination)
    for suffix in ("-wal", "-shm"):
        sidecar = Path(str(source_path) + suffix)
        if sidecar.is_file():
            shutil.copy2(sidecar, Path(str(destination) + suffix))


def _postgres_cli_url(database_url: str) -> str:
    """Strip SQLAlchemy driver suffix for ``pg_dump`` / ``psql``."""
    for prefix in ("postgresql+psycopg://", "postgresql+psycopg2://"):
        if database_url.startswith(prefix):
            return "postgresql://" + database_url[len(prefix) :]
    return database_url


def backup_postgres_full(database_url: str, destination: Path) -> None:
    """Run ``pg_dump`` against the configured database (custom format)."""
    pg_dump = shutil.which("pg_dump")
    if pg_dump is None:
        raise RuntimeError("pg_dump not found on PATH")
    destination.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [
            pg_dump,
            "--format=custom",
            "--no-owner",
            "--no-acl",
            "--dbname",
            _postgres_cli_url(database_url),
            "--file",
            str(destination),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"pg_dump failed (exit {result.returncode}): {result.stderr.strip()}"
        )


def _row_to_dict(row_mapping) -> dict:
    return {key: _json_safe(value) for key, value in row_mapping.items()}


def _json_safe(value):
    if hasattr(value, "hex") and hasattr(value, "version"):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _orm_to_dict(instance) -> dict:
    mapper = inspect(instance).mapper
    return {
        column.key: _json_safe(getattr(instance, column.key))
        for column in mapper.columns
    }


def export_tenant_json(
    engine: Engine, tenant_key: str, destination: Path
) -> dict[str, int]:
    """Export tenant registry rows and tenant-scoped table data to JSON."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    with Session(engine) as session:
        tenant = session.scalars(
            select(Tenant).where(Tenant.key == tenant_key)
        ).first()
        if tenant is None:
            raise LookupError(f"Unknown tenant key: {tenant_key}")
        tenant_id = tenant.id

        domains = session.scalars(
            select(TenantDomain).where(TenantDomain.tenant_id == tenant_id)
        ).all()
        tables: dict[str, list[dict]] = {
            "tenants": [_orm_to_dict(tenant)],
            "tenant_domains": [_orm_to_dict(row) for row in domains],
        }
        counts: dict[str, int] = {
            "tenants": 1,
            "tenant_domains": len(domains),
        }

        for table in RLS_TENANT_TABLES:
            # Table names come from the RLS allowlist, not user input.
            query = text(f"SELECT * FROM {table} WHERE tenant_id = :tid")
            rows = session.execute(
                query, {"tid": _tenant_id_param(engine, tenant_id)}
            ).mappings()
            tables[table] = [_row_to_dict(row) for row in rows]
            counts[table] = len(tables[table])

    payload = {
        "format": "school-analytics-tenant-export-v1",
        "exported_at": datetime.now(UTC).isoformat(),
        "tenant_key": tenant_key,
        "tenant_id": str(tenant_id),
        "tables": tables,
    }
    destination.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return counts


def run_backup(
    *,
    output: Path,
    tenant_key: str | None,
    database_url: str,
) -> dict[str, object]:
    if tenant_key:
        if not output.suffix:
            output = output.with_suffix(".json")
        engine = _engine_for_url(database_url)
        counts = export_tenant_json(engine, tenant_key, output)
        engine.dispose()
        return {"mode": "tenant", "path": str(output), "row_counts": counts}

    if _is_sqlite(database_url):
        backup_sqlite_full(database_url, output)
        return {"mode": "full", "path": str(output), "backend": "sqlite"}
    if _is_postgres(database_url):
        if output.suffix not in {".dump", ".backup"}:
            output = output.with_suffix(".dump")
        backup_postgres_full(database_url, output)
        return {"mode": "full", "path": str(output), "backend": "postgresql"}
    raise ValueError(f"Unsupported database URL scheme: {database_url.split(':', 1)[0]}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Backup School Analytics data")
    parser.add_argument(
        "--output",
        required=True,
        help="Destination file path (relative paths go under ./backups/)",
    )
    parser.add_argument(
        "--tenant-key",
        help="Export only this tenant as JSON (default: full database backup)",
    )
    parser.add_argument(
        "--database-url",
        default="",
        help="Override DATABASE_URL (default: application settings)",
    )
    args = parser.parse_args(argv)

    try:
        destination = _safe_output_path(args.output)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    database_url = args.database_url.strip() or settings.database_url
    try:
        summary = run_backup(
            output=destination,
            tenant_key=(args.tenant_key or "").strip() or None,
            database_url=database_url,
        )
    except (FileNotFoundError, LookupError, RuntimeError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
