"""Post-restore validation for School Analytics databases.

Checks Alembic head, tenant hostname resolution, sample login, and (PostgreSQL)
RLS enforcement on tenant-scoped tables.

Usage::

    PYTHONPATH=. python scripts/validate_restore.py --tenant-key sunrise
    PYTHONPATH=. python scripts/validate_restore.py \\
        --tenant-key sunrise --hostname sunrise.localhost \\
        --sample-email admin@sunrise.edu --sample-password "$DEMO_PASSWORD"
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass

from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.config import settings
from app.db import check_db_migrations_at_head
from app.models import Role, Tenant, TenantDomain, User
from app.security import verify_password
from app.tenant.rls import RLS_TENANT_TABLES


@dataclass(frozen=True)
class ValidationResult:
    name: str
    ok: bool
    detail: str


def _engine_for_url(database_url: str) -> Engine:
    connect_args: dict = {}
    if database_url.startswith("sqlite"):
        connect_args["check_same_thread"] = False
    return create_engine(database_url, future=True, connect_args=connect_args)


def check_migrations_at_head(engine: Engine) -> ValidationResult:
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except Exception:
        return ValidationResult(
            "alembic_head", False, "database connection failed"
        )
    if check_db_migrations_at_head(engine):
        return ValidationResult("alembic_head", True, "revision matches Alembic head")
    return ValidationResult(
        "alembic_head", False, "database revision is not at Alembic head"
    )


def check_tenant_domains(
    session: Session, tenant_key: str, hostname: str | None
) -> ValidationResult:
    tenant = session.scalars(
        select(Tenant).where(Tenant.key == tenant_key)
    ).first()
    if tenant is None:
        return ValidationResult(
            "tenant_domains", False, f"tenant key not found: {tenant_key}"
        )
    if tenant.status != "active":
        return ValidationResult(
            "tenant_domains",
            False,
            f"tenant {tenant_key} status is {tenant.status}",
        )

    domains = session.scalars(
        select(TenantDomain).where(TenantDomain.tenant_id == tenant.id)
    ).all()
    if not domains:
        return ValidationResult(
            "tenant_domains", False, f"no hostnames registered for {tenant_key}"
        )

    if hostname:
        match = next((d for d in domains if d.hostname == hostname), None)
        if match is None:
            known = ", ".join(sorted(d.hostname for d in domains))
            return ValidationResult(
                "tenant_domains",
                False,
                f"hostname {hostname} not mapped; known: {known}",
            )
        resolved = match.hostname
    else:
        primary = next((d for d in domains if d.is_primary), domains[0])
        resolved = primary.hostname

    return ValidationResult(
        "tenant_domains",
        True,
        f"{resolved} resolves to tenant {tenant_key} ({tenant.id})",
    )


def check_sample_login(
    session: Session,
    tenant_key: str,
    sample_email: str | None,
    sample_password: str | None,
) -> ValidationResult:
    if not sample_password:
        return ValidationResult(
            "sample_login",
            False,
            "sample password not provided (use --sample-password or VALIDATE_RESTORE_PASSWORD)",
        )

    tenant = session.scalars(
        select(Tenant).where(Tenant.key == tenant_key)
    ).first()
    if tenant is None:
        return ValidationResult("sample_login", False, f"tenant key not found: {tenant_key}")

    if sample_email:
        user = session.scalars(
            select(User).where(
                User.tenant_id == tenant.id,
                User.email == sample_email.strip().lower(),
            )
        ).first()
        if user is None:
            return ValidationResult(
                "sample_login",
                False,
                f"user {sample_email} not found for tenant {tenant_key}",
            )
    else:
        user = session.scalars(
            select(User)
            .where(User.tenant_id == tenant.id, User.role == Role.ADMIN)
            .order_by(User.id)
            .limit(1)
        ).first()
        if user is None:
            return ValidationResult(
                "sample_login",
                False,
                f"no admin user found for tenant {tenant_key}",
            )
        sample_email = user.email

    if not verify_password(sample_password, user.password_hash):
        return ValidationResult(
            "sample_login",
            False,
            f"password verification failed for {sample_email}",
        )
    return ValidationResult(
        "sample_login", True, f"password verified for {sample_email}"
    )


def check_rls_enabled(engine: Engine) -> ValidationResult:
    if not str(engine.url).startswith("postgresql"):
        return ValidationResult(
            "rls_enabled", True, "skipped (SQLite has no RLS policies)"
        )

    inspector = inspect(engine)
    missing: list[str] = []
    not_forced: list[str] = []
    for table in RLS_TENANT_TABLES:
        if not inspector.has_table(table):
            missing.append(f"{table}:missing")
            continue
        with engine.connect() as conn:
            row = conn.execute(
                text(
                    """
                    SELECT c.relrowsecurity, c.relforcerowsecurity
                    FROM pg_class c
                    JOIN pg_namespace n ON n.oid = c.relnamespace
                    WHERE n.nspname = 'public' AND c.relname = :table
                    """
                ),
                {"table": table},
            ).one_or_none()
        if row is None:
            missing.append(f"{table}:missing")
            continue
        if not row.relrowsecurity:
            missing.append(table)
        if not row.relforcerowsecurity:
            not_forced.append(table)

    if missing or not_forced:
        detail_parts = []
        if missing:
            detail_parts.append(f"RLS off or missing: {', '.join(missing)}")
        if not_forced:
            detail_parts.append(f"FORCE RLS off: {', '.join(not_forced)}")
        return ValidationResult("rls_enabled", False, "; ".join(detail_parts))

    return ValidationResult(
        "rls_enabled",
        True,
        f"ROW LEVEL SECURITY enabled on {len(RLS_TENANT_TABLES)} tables",
    )


def run_validation(
    *,
    database_url: str,
    tenant_key: str,
    hostname: str | None = None,
    sample_email: str | None = None,
    sample_password: str | None = None,
) -> list[ValidationResult]:
    engine = _engine_for_url(database_url)
    results = [check_migrations_at_head(engine)]
    with Session(engine) as session:
        results.append(check_tenant_domains(session, tenant_key, hostname))
        results.append(
            check_sample_login(session, tenant_key, sample_email, sample_password)
        )
    results.append(check_rls_enabled(engine))
    engine.dispose()
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate a restored School Analytics database"
    )
    parser.add_argument("--tenant-key", required=True, help="Tenant key to validate")
    parser.add_argument("--hostname", help="Expected hostname (default: primary domain)")
    parser.add_argument("--sample-email", help="User email for login check")
    parser.add_argument(
        "--sample-password",
        default="",
        help="Password for login check (default: VALIDATE_RESTORE_PASSWORD env)",
    )
    parser.add_argument(
        "--database-url",
        default="",
        help="Override DATABASE_URL (default: application settings)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON on stdout",
    )
    args = parser.parse_args(argv)

    password = args.sample_password.strip()
    if not password:
        import os

        password = os.environ.get("VALIDATE_RESTORE_PASSWORD", "").strip()

    database_url = args.database_url.strip() or settings.database_url
    results = run_validation(
        database_url=database_url,
        tenant_key=args.tenant_key.strip(),
        hostname=(args.hostname or "").strip() or None,
        sample_email=(args.sample_email or "").strip() or None,
        sample_password=password or None,
    )

    if args.json:
        print(json.dumps([asdict(r) for r in results], indent=2))
    else:
        for result in results:
            status = "OK" if result.ok else "FAIL"
            print(f"[{status}] {result.name}: {result.detail}")

    return 0 if all(r.ok for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
