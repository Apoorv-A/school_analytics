"""Parental consent records for DPDP Act pilot (append-only audit trail)."""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "014_parental_consent"
down_revision = "013_action_tasks"
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
    if inspector.has_table("parental_consent_records"):
        return

    op.create_table(
        "parental_consent_records",
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
        sa.Column(
            "guardian_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("purpose", sa.String(40), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("privacy_notice_version", sa.String(32), nullable=False),
        sa.Column("dpa_reference", sa.String(80), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "recorded_by_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "recorded_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_parental_consent_records_tenant_id",
        "parental_consent_records",
        ["tenant_id"],
    )
    op.create_index(
        "ix_parental_consent_records_student_id",
        "parental_consent_records",
        ["student_id"],
    )
    op.create_index(
        "ix_parental_consent_records_guardian_user_id",
        "parental_consent_records",
        ["guardian_user_id"],
    )
    op.create_index(
        "ix_parental_consent_records_student_purpose",
        "parental_consent_records",
        ["tenant_id", "student_id", "purpose", "recorded_at"],
    )

    if bind.dialect.name == "postgresql":
        _apply_rls("parental_consent_records")


def downgrade() -> None:
    op.drop_index(
        "ix_parental_consent_records_student_purpose",
        table_name="parental_consent_records",
    )
    op.drop_index(
        "ix_parental_consent_records_guardian_user_id",
        table_name="parental_consent_records",
    )
    op.drop_index(
        "ix_parental_consent_records_student_id",
        table_name="parental_consent_records",
    )
    op.drop_index(
        "ix_parental_consent_records_tenant_id",
        table_name="parental_consent_records",
    )
    op.drop_table("parental_consent_records")
