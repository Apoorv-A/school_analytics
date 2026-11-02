"""Account lifecycle: session version, forced password change, reset tokens."""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "008_account_lifecycle"
down_revision = "007_user_totp_mfa"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("users"):
        return

    columns = {col["name"] for col in inspector.get_columns("users")}
    if "session_version" not in columns:
        op.add_column(
            "users",
            sa.Column("session_version", sa.Integer(), nullable=False, server_default="1"),
        )
    if "must_change_password" not in columns:
        op.add_column(
            "users",
            sa.Column(
                "must_change_password",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            ),
        )

    if not inspector.has_table("password_reset_tokens"):
        op.create_table(
            "password_reset_tokens",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column(
                "tenant_id",
                sa.Uuid(),
                sa.ForeignKey("tenants.id", ondelete="CASCADE"),
                nullable=False,
                index=True,
            ),
            sa.Column(
                "user_id",
                sa.Integer(),
                sa.ForeignKey("users.id", ondelete="CASCADE"),
                nullable=False,
                index=True,
            ),
            sa.Column("token_hash", sa.String(64), nullable=False, index=True),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.Column(
                "created_by_user_id",
                sa.Integer(),
                sa.ForeignKey("users.id", ondelete="SET NULL"),
                nullable=True,
            ),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if inspector.has_table("password_reset_tokens"):
        op.drop_table("password_reset_tokens")
    if not inspector.has_table("users"):
        return
    columns = {col["name"] for col in inspector.get_columns("users")}
    for name in ("must_change_password", "session_version"):
        if name in columns:
            op.drop_column("users", name)
