"""Helpers for explicitly reviewed prepaid funding scopes."""

from __future__ import annotations

from pathlib import Path
from uuid import UUID

REVIEWED_SCOPE_CONFIRMATION = "MATERIALIZE_REVIEWED_PREPAID_SCOPE"


def load_reviewed_account_ids(path: Path) -> set[UUID]:
    """Read one UUID per line from a reviewer-owned scope file."""
    account_ids: set[UUID] = set()
    for line_number, raw_line in enumerate(
        path.read_text(encoding="utf-8").splitlines(),
        start=1,
    ):
        value = raw_line.split("#", 1)[0].strip()
        if not value:
            continue
        try:
            account_id = UUID(value)
        except ValueError as exc:
            raise ValueError(
                f"reviewed scope contains an invalid account UUID at line "
                f"{line_number}: {value}"
            ) from exc
        if account_id in account_ids:
            raise ValueError(
                f"reviewed scope contains duplicate account UUID: {account_id}"
            )
        account_ids.add(account_id)
    if not account_ids:
        raise ValueError("reviewed scope must contain at least one account UUID")
    return account_ids


def resolve_reviewed_account_scope(
    path: Path | None,
    *,
    allowed_account_ids: set[UUID],
    confirmation: str | None,
) -> set[UUID]:
    """Return the full candidate cohort or a confirmed candidate subset."""
    if path is None:
        if confirmation:
            raise ValueError(
                "--confirm-reviewed-scope requires --reviewed-account-ids-file"
            )
        return set(allowed_account_ids)
    if confirmation != REVIEWED_SCOPE_CONFIRMATION:
        raise ValueError(
            "scoped reconciliation requires --confirm-reviewed-scope "
            f"{REVIEWED_SCOPE_CONFIRMATION}"
        )
    selected = load_reviewed_account_ids(path)
    unexpected = selected - allowed_account_ids
    if unexpected:
        rendered = ", ".join(sorted(str(value) for value in unexpected))
        raise ValueError(
            "reviewed scope contains accounts outside the current prepaid "
            f"candidate cohort: {rendered}"
        )
    return selected
