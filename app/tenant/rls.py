"""PostgreSQL row-level security session variable."""

from __future__ import annotations

import logging

from sqlalchemy import event, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.tenant.context import get_tenant_id

logger = logging.getLogger(__name__)

# Tables protected by tenant_id RLS in PostgreSQL (also used for backup scope).
RLS_TENANT_TABLES: tuple[str, ...] = (
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
    "import_runs",
    "import_sync_state",
    "import_mapping_queue",
    "intervention_cases",
    "intervention_contacts",
    "intervention_events",
    "action_tasks",
    "security_audit_events",
    "parental_consent_records",
)


def configure_rls(engine: Engine) -> None:
    if engine.dialect.name != "postgresql":
        return

    @event.listens_for(Session, "after_begin")
    def _set_tenant_on_transaction(session, transaction, connection) -> None:
        # Session listeners are process-global; guard so Postgres-only setup never
        # runs against SQLite connections (e.g. after postgres integration tests).
        if connection.dialect.name != "postgresql":
            return
        tenant_id = get_tenant_id()
        if tenant_id is None:
            return
        connection.execute(
            text("SELECT set_config('app.tenant_id', :tid, true)"),
            {"tid": str(tenant_id)},
        )


def apply_rls_policies(connection) -> None:
    """Create RLS policies when running migrations as owner."""
    if connection.dialect.name != "postgresql":
        return

    # Registry tables (`tenants`, `tenant_domains`) are intentionally excluded.
    # Hostname resolution and the tenant operator read them before any request
    # tenant context exists, so RLS on those tables would block bootstrap lookups.
    existing_tables = set(inspect(connection).get_table_names(schema="public"))
    for table in RLS_TENANT_TABLES:
        if table not in existing_tables:
            continue
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
