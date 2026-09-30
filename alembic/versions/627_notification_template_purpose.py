"""Persist customer-send purpose on notification templates.

Revision ID: 627_notification_template_purpose
Revises: 626_automation_script_control_plane
Create Date: 2026-09-30
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "627_notification_template_purpose"
down_revision: str | None = "626_automation_script_control_plane"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "notification_templates",
        sa.Column(
            "purpose",
            sa.String(length=40),
            nullable=False,
            server_default=sa.text("'general'"),
        ),
    )
    op.execute(
        sa.text(
            "UPDATE notification_templates SET purpose = 'account' "
            "WHERE code = 'update_customer_details' AND channel = 'email'"
        )
    )
    op.create_check_constraint(
        "ck_notification_templates_purpose",
        "notification_templates",
        "purpose IN ('general', 'billing', 'service', 'account', 'credentials')",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_notification_templates_purpose",
        "notification_templates",
        type_="check",
    )
    op.drop_column("notification_templates", "purpose")
