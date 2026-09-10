"""Add nullable external_id and source_system for ERP correlation."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "003_external_correlation"
down_revision = "002_rls_policies"
branch_labels = None
depends_on = None

_TABLES = ("users", "students", "assessments", "scores", "attendance")


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    for table in _TABLES:
        if not inspector.has_table(table):
            continue
        existing_cols = {col["name"] for col in inspector.get_columns(table)}
        if "external_id" not in existing_cols:
            op.add_column(table, sa.Column("external_id", sa.String(64), nullable=True))
        if "source_system" not in existing_cols:
            op.add_column(table, sa.Column("source_system", sa.String(40), nullable=True))
        index_name = f"ix_{table}_external_id"
        existing_indexes = {idx["name"] for idx in inspector.get_indexes(table)}
        if index_name not in existing_indexes:
            refreshed_cols = {col["name"] for col in sa.inspect(bind).get_columns(table)}
            if "external_id" in refreshed_cols:
                op.create_index(index_name, table, ["external_id"])


def downgrade() -> None:
    for table in reversed(_TABLES):
        op.drop_index(f"ix_{table}_external_id", table_name=table)
        op.drop_column(table, "source_system")
        op.drop_column(table, "external_id")
