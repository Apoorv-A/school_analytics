"""Import conflict queue for ERP vs analytics-owned field disagreements."""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "016_import_conflicts"
down_revision = "015_import_sync_state"
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
    if inspector.has_table("import_conflicts"):
        return

    op.create_table(
        "import_conflicts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.Uuid(),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("entity_type", sa.String(16), nullable=False),
        sa.Column("entity_id", sa.Integer(), nullable=False),
        sa.Column("external_id", sa.String(64), nullable=True),
        sa.Column("source", sa.String(40), nullable=False),
        sa.Column("local_values", sa.JSON(), nullable=False),
        sa.Column("incoming_values", sa.JSON(), nullable=False),
        sa.Column("field_names", sa.JSON(), nullable=False),
        sa.Column(
            "status",
            sa.String(16),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("resolution", sa.JSON(), nullable=True),
        sa.Column(
            "resolved_by_user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "entity_type",
            "entity_id",
            name="uq_import_conflicts_entity",
        ),
    )
    op.create_index(
        "ix_import_conflicts_tenant_id",
        "import_conflicts",
        ["tenant_id"],
    )
    op.create_index(
        "ix_import_conflicts_tenant_status",
        "import_conflicts",
        ["tenant_id", "status"],
    )
    op.create_index(
        "ix_import_conflicts_created_at",
        "import_conflicts",
        ["created_at"],
    )

    if bind.dialect.name == "postgresql":
        _apply_rls("import_conflicts")


def downgrade() -> None:
    op.drop_index("ix_import_conflicts_created_at", table_name="import_conflicts")
    op.drop_index("ix_import_conflicts_tenant_status", table_name="import_conflicts")
    op.drop_index("ix_import_conflicts_tenant_id", table_name="import_conflicts")
    op.drop_table("import_conflicts")
