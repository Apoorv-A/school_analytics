"""Add intervention_cases and intervention_contacts for at-risk follow-up."""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "006_intervention_cases"
down_revision = "005_remarks_external"
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
    if inspector.has_table("intervention_cases"):
        return

    op.create_table(
        "intervention_cases",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.Uuid(),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "student_id",
            sa.Integer(),
            sa.ForeignKey("students.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("signal_source", sa.String(80), nullable=False),
        sa.Column("reason_summary", sa.Text(), nullable=False),
        sa.Column("priority", sa.String(6), nullable=False),
        sa.Column("status", sa.String(11), nullable=False, server_default="open"),
        sa.Column(
            "owner_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("due_date", sa.Date(), nullable=True),
        sa.Column("notes_summary", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_by_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index("ix_intervention_cases_tenant_id", "intervention_cases", ["tenant_id"])
    op.create_index(
        "ix_intervention_cases_tenant_status", "intervention_cases", ["tenant_id", "status"]
    )
    op.create_index(
        "ix_intervention_cases_student", "intervention_cases", ["tenant_id", "student_id"]
    )
    op.create_index("ix_intervention_cases_created_at", "intervention_cases", ["created_at"])
    op.create_index(
        "ix_intervention_cases_owner_user_id", "intervention_cases", ["owner_user_id"]
    )
    op.create_index(
        "ix_intervention_cases_student_id", "intervention_cases", ["student_id"]
    )

    op.create_table(
        "intervention_contacts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.Uuid(),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "case_id",
            sa.Integer(),
            sa.ForeignKey("intervention_cases.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("contact_type", sa.String(9), nullable=False),
        sa.Column("notes", sa.Text(), nullable=False),
        sa.Column("contacted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_by_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_intervention_contacts_tenant_id", "intervention_contacts", ["tenant_id"])
    op.create_index("ix_intervention_contacts_case_id", "intervention_contacts", ["case_id"])

    if bind.dialect.name == "postgresql":
        _apply_rls("intervention_cases")
        _apply_rls("intervention_contacts")


def downgrade() -> None:
    op.drop_index("ix_intervention_contacts_case_id", table_name="intervention_contacts")
    op.drop_index("ix_intervention_contacts_tenant_id", table_name="intervention_contacts")
    op.drop_table("intervention_contacts")
    op.drop_index("ix_intervention_cases_student_id", table_name="intervention_cases")
    op.drop_index("ix_intervention_cases_owner_user_id", table_name="intervention_cases")
    op.drop_index("ix_intervention_cases_created_at", table_name="intervention_cases")
    op.drop_index("ix_intervention_cases_student", table_name="intervention_cases")
    op.drop_index("ix_intervention_cases_tenant_status", table_name="intervention_cases")
    op.drop_index("ix_intervention_cases_tenant_id", table_name="intervention_cases")
    op.drop_table("intervention_cases")
