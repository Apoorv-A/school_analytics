"""Add import_runs audit table for CSV ingest history."""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "004_import_runs"
down_revision = "003_external_correlation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if inspector.has_table("import_runs"):
        return

    op.create_table(
        "import_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.Uuid(),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("source", sa.String(40), nullable=False),
        sa.Column("file_summary", sa.String(255), nullable=True),
        sa.Column("row_counts", sa.JSON(), nullable=True),
        sa.Column("error_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_by_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index("ix_import_runs_tenant_id", "import_runs", ["tenant_id"])
    op.create_index("ix_import_runs_created_at", "import_runs", ["created_at"])

    if bind.dialect.name == "postgresql":
        op.execute("ALTER TABLE import_runs ENABLE ROW LEVEL SECURITY")
        op.execute("ALTER TABLE import_runs FORCE ROW LEVEL SECURITY")
        op.execute("DROP POLICY IF EXISTS tenant_isolation ON import_runs")
        op.execute(
            """
            CREATE POLICY tenant_isolation ON import_runs
            USING (tenant_id::text = current_setting('app.tenant_id', true))
            WITH CHECK (tenant_id::text = current_setting('app.tenant_id', true))
            """
        )


def downgrade() -> None:
    op.drop_index("ix_import_runs_created_at", table_name="import_runs")
    op.drop_index("ix_import_runs_tenant_id", table_name="import_runs")
    op.drop_table("import_runs")
