"""Baseline multi-tenant schema.

For greenfield PostgreSQL deployments. Local SQLite development may use
`create_all()` via init_db() until PostgreSQL is configured.
"""

from __future__ import annotations

revision = "001_baseline"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # SQLAlchemy create_all is used for local/dev bootstrap; Alembic revision
    # documents the canonical schema version for production Postgres rollout.
    pass


def downgrade() -> None:
    pass
