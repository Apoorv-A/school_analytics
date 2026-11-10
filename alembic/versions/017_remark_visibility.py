"""Add draft vs published visibility on teacher remarks."""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "017_remark_visibility"
down_revision = "016_import_conflicts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("remarks"):
        return
    existing_cols = {col["name"] for col in inspector.get_columns("remarks")}
    if "visibility" in existing_cols:
        return

    visibility_col = sa.Column(
        "visibility",
        sa.String(10),
        nullable=False,
        server_default="published",
    )
    published_at_col = sa.Column(
        "published_at", sa.DateTime(timezone=True), nullable=True
    )
    published_by_col = sa.Column(
        "published_by_user_id",
        sa.Integer(),
        sa.ForeignKey(
            "users.id",
            name="fk_remarks_published_by_user_id",
            ondelete="SET NULL",
        ),
        nullable=True,
    )

    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("remarks") as batch_op:
            batch_op.add_column(visibility_col)
            batch_op.add_column(published_at_col)
            batch_op.add_column(published_by_col)
    else:
        op.add_column("remarks", visibility_col)
        op.add_column("remarks", published_at_col)
        op.add_column("remarks", published_by_col)

    op.execute(
        sa.text(
            "UPDATE remarks SET published_at = created_at "
            "WHERE published_at IS NULL AND visibility = 'published'"
        )
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("remarks"):
        return
    columns = {col["name"] for col in inspector.get_columns("remarks")}
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("remarks") as batch_op:
            for name in ("published_by_user_id", "published_at", "visibility"):
                if name in columns:
                    batch_op.drop_column(name)
        return
    for name in ("published_by_user_id", "published_at", "visibility"):
        if name in columns:
            op.drop_column("remarks", name)
