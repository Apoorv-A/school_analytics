"""Add intervention_events append-only timeline."""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "012_intervention_events"
down_revision = "011_user_mfa_recovery_codes"
branch_labels = None
depends_on = None


def _apply_rls(table: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(f"DROP POLICY IF EXISTS tenant_isolation ON {table}")
    op.execute(
        f"""
        CREATE POLICY tenant_isolation ON {table}
        USING (tenant_id::text = current_setting('app.tenant_id', true))
        WITH CHECK (tenant_id::text = current_setting('app.tenant_id', true))
        """
    )


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if inspector.has_table("intervention_events"):
        return

    op.create_table(
        "intervention_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.Uuid(),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "intervention_case_id",
            sa.Integer(),
            sa.ForeignKey("intervention_cases.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("event_type", sa.String(24), nullable=False),
        sa.Column(
            "actor_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("metadata", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_intervention_events_tenant_id", "intervention_events", ["tenant_id"]
    )
    op.create_index(
        "ix_intervention_events_intervention_case_id",
        "intervention_events",
        ["intervention_case_id"],
    )
    op.create_index(
        "ix_intervention_events_event_type", "intervention_events", ["event_type"]
    )
    op.create_index(
        "ix_intervention_events_created_at", "intervention_events", ["created_at"]
    )
    op.create_index(
        "ix_intervention_events_case_created",
        "intervention_events",
        ["tenant_id", "intervention_case_id", "created_at"],
    )

    if bind.dialect.name == "postgresql":
        _apply_rls("intervention_events")


def downgrade() -> None:
    op.drop_index("ix_intervention_events_case_created", table_name="intervention_events")
    op.drop_index("ix_intervention_events_created_at", table_name="intervention_events")
    op.drop_index("ix_intervention_events_event_type", table_name="intervention_events")
    op.drop_index(
        "ix_intervention_events_intervention_case_id", table_name="intervention_events"
    )
    op.drop_index("ix_intervention_events_tenant_id", table_name="intervention_events")
    op.drop_table("intervention_events")
