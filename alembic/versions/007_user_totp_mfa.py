"""Add TOTP MFA columns to users."""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "007_user_totp_mfa"
down_revision = "006_intervention_cases"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("users"):
        return
    columns = {col["name"] for col in inspector.get_columns("users")}
    if "totp_secret_enc" not in columns:
        op.add_column("users", sa.Column("totp_secret_enc", sa.String(512), nullable=True))
    if "totp_pending_secret_enc" not in columns:
        op.add_column(
            "users", sa.Column("totp_pending_secret_enc", sa.String(512), nullable=True)
        )
    if "mfa_enrolled_at" not in columns:
        op.add_column(
            "users",
            sa.Column("mfa_enrolled_at", sa.DateTime(timezone=True), nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("users"):
        return
    columns = {col["name"] for col in inspector.get_columns("users")}
    for name in ("mfa_enrolled_at", "totp_pending_secret_enc", "totp_secret_enc"):
        if name in columns:
            op.drop_column("users", name)
