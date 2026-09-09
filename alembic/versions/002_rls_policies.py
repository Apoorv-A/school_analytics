"""Apply PostgreSQL row-level security policies."""

from __future__ import annotations

from alembic import op

revision = "002_rls_policies"
down_revision = "001_baseline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    from app.tenant.rls import apply_rls_policies

    apply_rls_policies(op.get_bind())


def downgrade() -> None:
    pass
