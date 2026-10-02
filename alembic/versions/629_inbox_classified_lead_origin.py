"""Allow classified Inbox conversations as immutable Lead origins.

Revision ID: 629_inbox_classified_lead_origin
Revises: 628_widen_subscription_change_confirmation_origin
Create Date: 2026-09-29
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "629_inbox_classified_lead_origin"
down_revision: str | None = "628_widen_subscription_change_confirmation_origin"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_BASE_METHODS = (
    "'ad_lead_form_webhook', 'landing_page', 'portal', 'agent_declared', "
    "'campaign_response', 'referral', 'reviewed_import', 'inbox_form'"
)


def _replace_constraints(*, classified_origin: bool) -> None:
    op.drop_constraint(
        "ck_lead_origin_captures_method",
        "lead_origin_captures",
        type_="check",
    )
    op.drop_constraint(
        "ck_lead_origin_captures_method_platform",
        "lead_origin_captures",
        type_="check",
    )
    methods = _BASE_METHODS
    if classified_origin:
        methods += ", 'inbox_classification'"
    op.create_check_constraint(
        "ck_lead_origin_captures_method",
        "lead_origin_captures",
        f"capture_method IN ({methods})",
    )
    method_platform = (
        "(capture_method <> 'landing_page' OR source_platform = 'website') AND "
        "(capture_method <> 'portal' OR source_platform = 'portal') AND "
        "(capture_method <> 'agent_declared' OR source_platform = 'agent') AND "
        "(capture_method <> 'referral' OR source_platform = 'referral') AND "
        "(capture_method <> 'reviewed_import' OR "
        "source_platform = 'legacy_import') AND "
        "(capture_method <> 'inbox_form' OR source_platform = 'team_inbox')"
    )
    if classified_origin:
        method_platform += (
            " AND (capture_method <> 'inbox_classification' OR "
            "source_platform = 'team_inbox')"
        )
    op.create_check_constraint(
        "ck_lead_origin_captures_method_platform",
        "lead_origin_captures",
        method_platform,
    )


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("SET LOCAL statement_timeout = '30s'")
    _replace_constraints(classified_origin=True)


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("SET LOCAL statement_timeout = '30s'")
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM lead_origin_captures
                WHERE capture_method = 'inbox_classification'
            ) THEN
                RAISE EXCEPTION
                    'cannot remove inbox_classification while origin rows exist';
            END IF;
        END
        $$
        """
    )
    _replace_constraints(classified_origin=False)
