"""Store last-applied ERP bundle fingerprint per tenant sync channel."""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "015_import_sync_state"
down_revision = "014_parental_consent"
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
    if inspector.has_table("import_sync_state"):
        return

    op.create_table(
        "import_sync_state",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.Uuid(),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("source", sa.String(40), nullable=False),
        sa.Column("bundle_fingerprint", sa.String(64), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "tenant_id",
            "source",
            name="uq_import_sync_state_tenant_source",
        ),
    )
    op.create_index(
        "ix_import_sync_state_tenant_id",
        "import_sync_state",
        ["tenant_id"],
    )

    if bind.dialect.name == "postgresql":
        _apply_rls("import_sync_state")


def downgrade() -> None:
    op.drop_index("ix_import_sync_state_tenant_id", table_name="import_sync_state")
    op.drop_table("import_sync_state")
