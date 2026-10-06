"""Classify new temporary Test Connection requests without guessing history."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "645_test_connection_extension_purpose"
down_revision = "644_automation_scheduled_rules"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "service_extensions", sa.Column("purpose", sa.String(32), nullable=True)
    )
    op.create_check_constraint(
        "serviceextensionpurpose",
        "service_extensions",
        "purpose IS NULL OR purpose IN ('outage_compensation', 'test_connection')",
    )
    op.create_index(
        "ix_service_extensions_purpose_created",
        "service_extensions",
        ["purpose", "created_at"],
    )
    op.create_table(
        "test_connection_finance_reviews",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "event_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("event_store.event_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "rule_version_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("automation_rule_versions.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("step_index", sa.Integer(), nullable=False),
        sa.Column(
            "service_team_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("service_teams.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("recipient_ids", sa.JSON(), nullable=False),
        sa.Column("payload_sha256", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "event_id",
            "rule_version_id",
            "step_index",
            name="uq_test_connection_review_step",
        ),
        sa.CheckConstraint("step_index >= 0", name="ck_test_connection_review_step"),
    )


def downgrade() -> None:
    # Forward-fix once classified evidence exists; dropping it would erase
    # which requests Finance counted.
    connection = op.get_bind()
    if connection.scalar(
        sa.text("SELECT count(*) FROM service_extensions WHERE purpose IS NOT NULL")
    ):
        raise RuntimeError(
            "Classified service-extension evidence exists; use a forward fix."
        )
    if connection.scalar(
        sa.text("SELECT count(*) FROM test_connection_finance_reviews")
    ):
        raise RuntimeError("Finance review evidence exists; use a forward fix.")
    op.drop_table("test_connection_finance_reviews")
    op.drop_index(
        "ix_service_extensions_purpose_created", table_name="service_extensions"
    )
    op.drop_constraint("serviceextensionpurpose", "service_extensions", type_="check")
    op.drop_column("service_extensions", "purpose")
