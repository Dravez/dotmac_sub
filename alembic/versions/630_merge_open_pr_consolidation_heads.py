"""Merge the two migrations introduced by the consolidated PRs."""

from __future__ import annotations

from collections.abc import Sequence

revision: str = "630_merge_open_pr_consolidation_heads"
down_revision: tuple[str, str] = (
    "629_inbox_classified_lead_origin",
    "629_ticket_sla_enforcement_reason",
)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
