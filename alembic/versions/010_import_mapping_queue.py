"""Assessment import mapping queue for unmapped external keys."""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "010_import_mapping_queue"
down_revision = "009_security_audit_events"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if inspector.has_table("import_mapping_queue"):
        return

    op.create_table(
        "import_mapping_queue",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "tenant_id",
            sa.Uuid(),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("source", sa.String(40), nullable=False),
        sa.Column("external_key", sa.String(64), nullable=False),
        sa.Column("payload_snippet", sa.JSON(), nullable=True),
        sa.Column(
            "status",
            sa.String(8),
            nullable=False,
            server_default="pending",
        ),
        sa.Column(
            "mapped_assessment_id",
            sa.Integer(),
            sa.ForeignKey("assessments.id", ondelete="SET NULL"),
            nullable=True,
        ),
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
            "external_key",
            name="uq_import_mapping_pending_key",
        ),
    )
    op.create_index(
        "ix_import_mapping_queue_tenant_id",
        "import_mapping_queue",
        ["tenant_id"],
    )
    op.create_index(
        "ix_import_mapping_queue_tenant_status",
        "import_mapping_queue",
        ["tenant_id", "status"],
    )
    op.create_index(
        "ix_import_mapping_queue_created_at",
        "import_mapping_queue",
        ["created_at"],
    )
    op.create_index(
        "ix_import_mapping_queue_mapped_assessment_id",
        "import_mapping_queue",
        ["mapped_assessment_id"],
    )

    if bind.dialect.name == "postgresql":
        op.execute("ALTER TABLE import_mapping_queue ENABLE ROW LEVEL SECURITY")
        op.execute("ALTER TABLE import_mapping_queue FORCE ROW LEVEL SECURITY")
        op.execute("DROP POLICY IF EXISTS tenant_isolation ON import_mapping_queue")
        op.execute(
            """
            CREATE POLICY tenant_isolation ON import_mapping_queue
            USING (tenant_id::text = current_setting('app.tenant_id', true))
            WITH CHECK (tenant_id::text = current_setting('app.tenant_id', true))
            """
        )


def downgrade() -> None:
    op.drop_index(
        "ix_import_mapping_queue_mapped_assessment_id",
        table_name="import_mapping_queue",
    )
    op.drop_index(
        "ix_import_mapping_queue_created_at", table_name="import_mapping_queue"
    )
    op.drop_index(
        "ix_import_mapping_queue_tenant_status", table_name="import_mapping_queue"
    )
    op.drop_index(
        "ix_import_mapping_queue_tenant_id", table_name="import_mapping_queue"
    )
    op.drop_table("import_mapping_queue")
