"""Add action_tasks for intervention case follow-up."""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "013_action_tasks"
down_revision = "012_intervention_events"
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
    if inspector.has_table("action_tasks"):
        return

    op.create_table(
        "action_tasks",
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
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "assigned_to_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "created_by_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("visibility", sa.String(14), nullable=False, server_default="staff"),
        sa.Column("status", sa.String(11), nullable=False, server_default="open"),
        sa.Column("priority", sa.String(6), nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completion_note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_action_tasks_tenant_id", "action_tasks", ["tenant_id"])
    op.create_index(
        "ix_action_tasks_intervention_case_id",
        "action_tasks",
        ["intervention_case_id"],
    )
    op.create_index(
        "ix_action_tasks_assigned_to_user_id", "action_tasks", ["assigned_to_user_id"]
    )
    op.create_index("ix_action_tasks_created_at", "action_tasks", ["created_at"])
    op.create_index(
        "ix_action_tasks_case_status",
        "action_tasks",
        ["tenant_id", "intervention_case_id", "status"],
    )

    if bind.dialect.name == "postgresql":
        _apply_rls("action_tasks")


def downgrade() -> None:
    op.drop_index("ix_action_tasks_case_status", table_name="action_tasks")
    op.drop_index("ix_action_tasks_created_at", table_name="action_tasks")
    op.drop_index("ix_action_tasks_assigned_to_user_id", table_name="action_tasks")
    op.drop_index("ix_action_tasks_intervention_case_id", table_name="action_tasks")
    op.drop_index("ix_action_tasks_tenant_id", table_name="action_tasks")
    op.drop_table("action_tasks")
