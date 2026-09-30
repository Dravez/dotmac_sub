"""Add evidence for reviewed payment-allocation reversals.

Revision ID: 629_reviewed_payment_allocation_reversal
Revises: 628_widen_subscription_change_confirmation_origin
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "629_reviewed_payment_allocation_reversal"
down_revision: str | None = "628_widen_subscription_change_confirmation_origin"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "payment_allocations",
        sa.Column("reversal_ledger_entry_id", sa.UUID(), nullable=True),
    )
    op.add_column(
        "payment_allocations",
        sa.Column("reversal_consumption_ledger_entry_id", sa.UUID(), nullable=True),
    )
    op.add_column(
        "payment_allocations",
        sa.Column("reversal_preview_fingerprint", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "payment_allocations",
        sa.Column("reversal_idempotency_key", sa.String(length=120), nullable=True),
    )
    op.add_column(
        "payment_allocations",
        sa.Column("reversal_reason", sa.Text(), nullable=True),
    )
    op.add_column(
        "payment_allocations",
        sa.Column("reversal_actor_id", sa.UUID(), nullable=True),
    )
    op.add_column(
        "payment_allocations",
        sa.Column("reversed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_payment_allocations_reversal_ledger_entry",
        "payment_allocations",
        "ledger_entries",
        ["reversal_ledger_entry_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_payment_allocations_reversal_consumption_ledger_entry",
        "payment_allocations",
        "ledger_entries",
        ["reversal_consumption_ledger_entry_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "uq_payment_allocations_reversal_idempotency_key",
        "payment_allocations",
        ["reversal_idempotency_key"],
        unique=True,
    )
    op.create_index(
        "uq_payment_allocations_reversal_ledger_entry_id",
        "payment_allocations",
        ["reversal_ledger_entry_id"],
        unique=True,
    )
    op.create_index(
        "uq_payment_allocations_reversal_consumption_ledger_entry_id",
        "payment_allocations",
        ["reversal_consumption_ledger_entry_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(
        "uq_payment_allocations_reversal_consumption_ledger_entry_id",
        table_name="payment_allocations",
    )
    op.drop_index(
        "uq_payment_allocations_reversal_ledger_entry_id",
        table_name="payment_allocations",
    )
    op.drop_index(
        "uq_payment_allocations_reversal_idempotency_key",
        table_name="payment_allocations",
    )
    op.drop_constraint(
        "fk_payment_allocations_reversal_consumption_ledger_entry",
        "payment_allocations",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_payment_allocations_reversal_ledger_entry",
        "payment_allocations",
        type_="foreignkey",
    )
    for name in (
        "reversed_at",
        "reversal_actor_id",
        "reversal_reason",
        "reversal_idempotency_key",
        "reversal_preview_fingerprint",
        "reversal_consumption_ledger_entry_id",
        "reversal_ledger_entry_id",
    ):
        op.drop_column("payment_allocations", name)
