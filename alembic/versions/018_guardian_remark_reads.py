"""F07: per-guardian read state for published teacher remarks."""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "018_guardian_remark_reads"
down_revision = "017_remark_visibility"
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
    if inspector.has_table("guardian_remark_reads"):
        return
    op.create_table(
        "guardian_remark_reads",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.Uuid(),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "guardian_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "remark_id",
            sa.Integer(),
            sa.ForeignKey("remarks.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "read_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "guardian_user_id",
            "remark_id",
            name="uq_guardian_remark_reads_guardian_remark",
        ),
    )
    op.create_index(
        "ix_guardian_remark_reads_tenant_id",
        "guardian_remark_reads",
        ["tenant_id"],
    )
    op.create_index(
        "ix_guardian_remark_reads_guardian_user_id",
        "guardian_remark_reads",
        ["guardian_user_id"],
    )
    op.create_index(
        "ix_guardian_remark_reads_remark_id",
        "guardian_remark_reads",
        ["remark_id"],
    )

    if bind.dialect.name == "postgresql":
        _apply_rls("guardian_remark_reads")


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("guardian_remark_reads"):
        return
    op.drop_index("ix_guardian_remark_reads_remark_id", "guardian_remark_reads")
    op.drop_index(
        "ix_guardian_remark_reads_guardian_user_id", "guardian_remark_reads"
    )
    op.drop_index("ix_guardian_remark_reads_tenant_id", "guardian_remark_reads")
    op.drop_table("guardian_remark_reads")
