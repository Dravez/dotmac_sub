"""Add the dedicated Ticket SLA enforcement-lock reason.

Revision ID: 630_ticket_sla_enforcement_reason
Revises: 628_widen_subscription_change_confirmation_origin
Create Date: 2026-09-29
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "630_ticket_sla_enforcement_reason"
down_revision: str | None = "628_widen_subscription_change_confirmation_origin"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TYPE enforcementreason ADD VALUE IF NOT EXISTS 'ticket_sla'")


def downgrade() -> None:
    # PostgreSQL enum values cannot be removed safely while locks may use them.
    pass
