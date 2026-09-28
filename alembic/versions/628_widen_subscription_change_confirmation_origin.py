"""Widen subscription change confirmation origin provenance.

Revision ID: 628_widen_subscription_change_confirmation_origin
Revises: 627_network_map_kmz_transfer
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision: str = "628_widen_subscription_change_confirmation_origin"
down_revision: str | None = "627_network_map_kmz_transfer"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "subscription_change_requests",
        "confirmation_origin",
        existing_type=sa.String(40),
        type_=sa.String(120),
        existing_nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "subscription_change_requests",
        "confirmation_origin",
        existing_type=sa.String(120),
        type_=sa.String(40),
        existing_nullable=True,
    )
