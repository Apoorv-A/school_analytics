"""Add external_id and source_system to remarks for ERP upserts."""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "005_remarks_external"
down_revision = "004_import_runs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("remarks"):
        return
    existing_cols = {col["name"] for col in inspector.get_columns("remarks")}
    if "external_id" not in existing_cols:
        op.add_column("remarks", sa.Column("external_id", sa.String(64), nullable=True))
    if "source_system" not in existing_cols:
        op.add_column("remarks", sa.Column("source_system", sa.String(40), nullable=True))
    existing_indexes = {idx["name"] for idx in inspector.get_indexes("remarks")}
    if "ix_remarks_external_id" not in existing_indexes:
        refreshed_cols = {col["name"] for col in sa.inspect(bind).get_columns("remarks")}
        if "external_id" in refreshed_cols:
            op.create_index("ix_remarks_external_id", "remarks", ["external_id"])


def downgrade() -> None:
    op.drop_index("ix_remarks_external_id", table_name="remarks")
    op.drop_column("remarks", "source_system")
    op.drop_column("remarks", "external_id")
