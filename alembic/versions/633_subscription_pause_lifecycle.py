"""Add first-class subscription and account pause lifecycle evidence.

Revision ID: 633_subscription_pause_lifecycle
Revises: 632_reviewed_payment_allocation_reversal
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "633_subscription_pause_lifecycle"
down_revision: str | None = "632_reviewed_payment_allocation_reversal"
branch_labels = None
depends_on = None


_PERMISSIONS = {
    "subscription:pause": "Pause an active subscription and its billing period",
    "subscription:resume": "Resume a paused subscription after eligibility review",
    "support:ticket_service_pause:read": "View ticket-linked service pauses",
    "support:ticket_service_pause:resume": "Resume eligible ticket-linked pauses",
}


def _seed_permissions() -> None:
    bind = op.get_bind()
    if "permissions" not in sa.inspect(bind).get_table_names():
        return
    now = datetime.now(UTC)
    for key, description in _PERMISSIONS.items():
        permission_id = bind.execute(
            sa.text("SELECT id FROM permissions WHERE key = :key"), {"key": key}
        ).scalar()
        if permission_id is None:
            bind.execute(
                sa.text(
                    """
                    INSERT INTO permissions (
                        id, key, description, is_active, is_ui_assignable,
                        created_at, updated_at
                    ) VALUES (:id, :key, :description, true, true, :now, :now)
                    """
                ),
                {
                    "id": str(uuid4()),
                    "key": key,
                    "description": description,
                    "now": now,
                },
            )


def upgrade() -> None:
    op.execute("ALTER TYPE subscriptionstatus ADD VALUE IF NOT EXISTS 'paused'")
    op.execute("ALTER TYPE subscriberstatus ADD VALUE IF NOT EXISTS 'paused'")
    op.execute("ALTER TYPE lifecycleeventtype ADD VALUE IF NOT EXISTS 'pause'")

    op.create_table(
        "subscription_pause_episodes",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("subscription_id", sa.UUID(), nullable=False),
        sa.Column("account_id", sa.UUID(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("effective_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("effective_duration_seconds", sa.Integer(), nullable=True),
        sa.Column("previous_subscription_status", sa.String(length=32), nullable=False),
        sa.Column("resulting_subscription_status", sa.String(length=32), nullable=True),
        sa.Column("previous_account_status", sa.String(length=32), nullable=False),
        sa.Column("resulting_account_status", sa.String(length=32), nullable=True),
        sa.Column(
            "previous_next_billing_at", sa.DateTime(timezone=True), nullable=True
        ),
        sa.Column(
            "projected_next_billing_at", sa.DateTime(timezone=True), nullable=True
        ),
        sa.Column(
            "resulting_next_billing_at", sa.DateTime(timezone=True), nullable=True
        ),
        sa.Column("billing_policy_key", sa.String(length=80), nullable=False),
        sa.Column("billing_policy_version", sa.Integer(), nullable=False),
        sa.Column(
            "policy_snapshot",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("resume_preview_fingerprint", sa.String(length=64), nullable=True),
        sa.Column("created_by", sa.String(length=160), nullable=False),
        sa.Column("resumed_by", sa.String(length=160), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "resumed_at IS NULL OR resumed_at >= effective_at",
            name="ck_subscription_pause_episode_nonnegative_duration",
        ),
        sa.CheckConstraint(
            "status <> 'resumed' OR resumed_at IS NOT NULL",
            name="ck_subscription_pause_episode_resumed_at",
        ),
        sa.CheckConstraint(
            "status IN ('active', 'resumed', 'canceled')",
            name="ck_subscription_pause_episode_status",
        ),
        sa.CheckConstraint(
            "effective_duration_seconds IS NULL OR effective_duration_seconds >= 0",
            name="ck_subscription_pause_episode_duration",
        ),
        sa.ForeignKeyConstraint(
            ["account_id"], ["subscribers.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["subscription_id"], ["subscriptions.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_subscription_pause_episodes_account_id",
        "subscription_pause_episodes",
        ["account_id"],
    )
    op.create_index(
        "ix_subscription_pause_episodes_subscription_id",
        "subscription_pause_episodes",
        ["subscription_id"],
    )
    op.create_index(
        "uq_subscription_pause_episode_active",
        "subscription_pause_episodes",
        ["subscription_id"],
        unique=True,
        postgresql_where=sa.text("status = 'active'"),
    )
    op.add_column(
        "service_entitlements",
        sa.Column("source_pause_episode_id", sa.UUID(), nullable=True),
    )
    op.create_foreign_key(
        "fk_service_entitlements_pause_episode",
        "service_entitlements",
        "subscription_pause_episodes",
        ["source_pause_episode_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "uq_service_entitlements_active_pause_episode",
        "service_entitlements",
        ["source_pause_episode_id"],
        unique=True,
        postgresql_where=sa.text(
            "status = 'active' AND source_pause_episode_id IS NOT NULL"
        ),
    )

    op.create_table(
        "subscription_pause_causes",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("pause_episode_id", sa.UUID(), nullable=False),
        sa.Column("reason_code", sa.String(length=80), nullable=False),
        sa.Column("source_type", sa.String(length=48), nullable=False),
        sa.Column("source_id", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("ticket_id", sa.UUID(), nullable=True),
        sa.Column("sla_clock_id", sa.UUID(), nullable=True),
        sa.Column("sla_breach_id", sa.UUID(), nullable=True),
        sa.Column("automation_event_id", sa.UUID(), nullable=True),
        sa.Column("automation_rule_id", sa.UUID(), nullable=True),
        sa.Column("automation_rule_version_id", sa.UUID(), nullable=True),
        sa.Column("automation_step_index", sa.Integer(), nullable=True),
        sa.Column("selection_policy", sa.String(length=80), nullable=False),
        sa.Column("resume_policy", sa.String(length=80), nullable=False),
        sa.Column(
            "workflow_policy_snapshot",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("released_by", sa.String(length=160), nullable=True),
        sa.Column("release_reason", sa.Text(), nullable=True),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('active', 'released', 'canceled')",
            name="ck_subscription_pause_cause_status",
        ),
        sa.CheckConstraint(
            "status <> 'released' OR released_at IS NOT NULL",
            name="ck_subscription_pause_cause_released_at",
        ),
        sa.ForeignKeyConstraint(
            ["pause_episode_id"],
            ["subscription_pause_episodes.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["ticket_id"], ["tickets.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["sla_breach_id"], ["sla_breaches.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["sla_clock_id"], ["sla_clocks.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key"),
    )
    op.create_index(
        "ix_subscription_pause_cause_episode_status",
        "subscription_pause_causes",
        ["pause_episode_id", "status"],
    )
    op.create_index(
        "ix_subscription_pause_causes_ticket_id",
        "subscription_pause_causes",
        ["ticket_id"],
    )
    op.create_index(
        "ix_subscription_pause_causes_sla_clock_id",
        "subscription_pause_causes",
        ["sla_clock_id"],
    )
    op.create_index(
        "uq_subscription_pause_cause_source",
        "subscription_pause_causes",
        ["source_type", "source_id"],
        unique=True,
    )
    _seed_permissions()


def downgrade() -> None:
    op.drop_index(
        "ix_subscription_pause_causes_sla_clock_id",
        table_name="subscription_pause_causes",
    )
    op.drop_index(
        "ix_subscription_pause_causes_ticket_id",
        table_name="subscription_pause_causes",
    )
    op.drop_index(
        "uq_subscription_pause_cause_source", table_name="subscription_pause_causes"
    )
    op.drop_index(
        "ix_subscription_pause_cause_episode_status",
        table_name="subscription_pause_causes",
    )
    op.drop_table("subscription_pause_causes")
    op.drop_index(
        "uq_service_entitlements_active_pause_episode",
        table_name="service_entitlements",
    )
    op.drop_constraint(
        "fk_service_entitlements_pause_episode",
        "service_entitlements",
        type_="foreignkey",
    )
    op.drop_column("service_entitlements", "source_pause_episode_id")
    op.drop_index(
        "uq_subscription_pause_episode_active",
        table_name="subscription_pause_episodes",
    )
    op.drop_index(
        "ix_subscription_pause_episodes_subscription_id",
        table_name="subscription_pause_episodes",
    )
    op.drop_index(
        "ix_subscription_pause_episodes_account_id",
        table_name="subscription_pause_episodes",
    )
    op.drop_table("subscription_pause_episodes")
    # PostgreSQL enum values are retained for forward-fix safety.
