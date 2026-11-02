"""Add hashed MFA recovery codes table."""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "011_user_mfa_recovery_codes"
down_revision = "010_import_mapping_queue"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if inspector.has_table("user_mfa_recovery_codes"):
        return
    op.create_table(
        "user_mfa_recovery_codes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.Uuid(),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("code_hash", sa.String(64), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("(CURRENT_TIMESTAMP)"),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "user_id",
            "code_hash",
            name="uq_user_mfa_recovery_code_hash",
        ),
    )
    op.create_index(
        "ix_user_mfa_recovery_codes_tenant_id",
        "user_mfa_recovery_codes",
        ["tenant_id"],
    )
    op.create_index(
        "ix_user_mfa_recovery_codes_user_id",
        "user_mfa_recovery_codes",
        ["user_id"],
    )
    op.create_index(
        "ix_user_mfa_recovery_codes_code_hash",
        "user_mfa_recovery_codes",
        ["code_hash"],
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("user_mfa_recovery_codes"):
        return
    op.drop_index("ix_user_mfa_recovery_codes_code_hash", "user_mfa_recovery_codes")
    op.drop_index("ix_user_mfa_recovery_codes_user_id", "user_mfa_recovery_codes")
    op.drop_index("ix_user_mfa_recovery_codes_tenant_id", "user_mfa_recovery_codes")
    op.drop_table("user_mfa_recovery_codes")
