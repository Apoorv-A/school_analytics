"""PostgreSQL row-level security session variable."""

from __future__ import annotations

import logging
import uuid

from sqlalchemy import event, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.config import settings
from app.tenant.context import get_tenant_id

logger = logging.getLogger(__name__)

_is_postgres = settings.database_url.startswith("postgresql")


def configure_rls(engine: Engine) -> None:
    if not _is_postgres:
        return

    @event.listens_for(Session, "after_begin")
    def _set_tenant_on_transaction(session, transaction, connection) -> None:
        tenant_id = get_tenant_id()
        if tenant_id is None:
            return
        connection.execute(
            text("SELECT set_config('app.tenant_id', :tid, true)"),
            {"tid": str(tenant_id)},
        )


def apply_rls_policies(connection) -> None:
    """Create RLS policies when running migrations as owner."""
    if not _is_postgres:
        return

    # Registry tables (`tenants`, `tenant_domains`) are intentionally excluded.
    # Hostname resolution and the tenant operator read them before any request
    # tenant context exists, so RLS on those tables would block bootstrap lookups.
    tenant_tables = (
        "schools",
        "academic_years",
        "terms",
        "grades",
        "sections",
        "subjects",
        "users",
        "teachers",
        "students",
        "teacher_assignments",
        "assessments",
        "scores",
        "attendance",
        "remarks",
    )
    for table in tenant_tables:
        connection.execute(text(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY"))
        connection.execute(text(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY"))
        connection.execute(
            text(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
        )
        connection.execute(
            text(
                f"""
                CREATE POLICY tenant_isolation ON {table}
                USING (tenant_id::text = current_setting('app.tenant_id', true))
                WITH CHECK (tenant_id::text = current_setting('app.tenant_id', true))
                """
            )
        )
