"""Append-only security audit events for identity and access actions."""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "009_security_audit_events"
down_revision = "008_account_lifecycle"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if inspector.has_table("security_audit_events"):
        return

    op.create_table(
        "security_audit_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.Uuid(),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "actor_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("target_type", sa.String(64), nullable=False),
        sa.Column("target_id", sa.String(64), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=True),
        sa.Column("correlation_id", sa.String(128), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_security_audit_events_tenant_id",
        "security_audit_events",
        ["tenant_id"],
    )
    op.create_index(
        "ix_security_audit_events_created_at",
        "security_audit_events",
        ["created_at"],
    )
    op.create_index(
        "ix_security_audit_events_action",
        "security_audit_events",
        ["action"],
    )

    if bind.dialect.name == "postgresql":
        op.execute("ALTER TABLE security_audit_events ENABLE ROW LEVEL SECURITY")
        op.execute("ALTER TABLE security_audit_events FORCE ROW LEVEL SECURITY")
        op.execute("DROP POLICY IF EXISTS tenant_isolation ON security_audit_events")
        op.execute(
            """
            CREATE POLICY tenant_isolation ON security_audit_events
            USING (tenant_id::text = current_setting('app.tenant_id', true))
            WITH CHECK (tenant_id::text = current_setting('app.tenant_id', true))
            """
        )


def downgrade() -> None:
    op.drop_index("ix_security_audit_events_action", table_name="security_audit_events")
    op.drop_index(
        "ix_security_audit_events_created_at", table_name="security_audit_events"
    )
    op.drop_index(
        "ix_security_audit_events_tenant_id", table_name="security_audit_events"
    )
    op.drop_table("security_audit_events")
