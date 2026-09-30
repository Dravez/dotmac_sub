from __future__ import annotations

from pathlib import Path
from unittest.mock import patch
from uuid import UUID

import pytest

from scripts.one_off.prepaid_funding_scope import (
    REVIEWED_SCOPE_CONFIRMATION,
    load_reviewed_account_ids,
    resolve_reviewed_account_scope,
)

FIRST = UUID("11111111-1111-1111-1111-111111111111")
SECOND = UUID("22222222-2222-2222-2222-222222222222")


def test_reviewed_scope_loads_unique_uuid_lines_and_ignores_comments():
    scope = Path("accounts.txt")
    contents = "# ticket 29012\n" f"{FIRST}\n" "\n" f"{SECOND} # reviewed\n"

    with patch.object(Path, "read_text", return_value=contents):
        assert load_reviewed_account_ids(scope) == {FIRST, SECOND}


def test_reviewed_scope_requires_explicit_confirmation():
    scope = Path("accounts.txt")
    with patch.object(Path, "read_text", return_value=f"{FIRST}\n"):
        with pytest.raises(ValueError, match="MATERIALIZE_REVIEWED_PREPAID_SCOPE"):
            resolve_reviewed_account_scope(
                scope,
                allowed_account_ids={FIRST},
                confirmation=None,
            )


def test_reviewed_scope_rejects_accounts_outside_candidate_cohort():
    scope = Path("accounts.txt")
    with patch.object(Path, "read_text", return_value=f"{FIRST}\n{SECOND}\n"):
        with pytest.raises(ValueError, match="outside the current prepaid candidate"):
            resolve_reviewed_account_scope(
                scope,
                allowed_account_ids={FIRST},
                confirmation=REVIEWED_SCOPE_CONFIRMATION,
            )


def test_default_scope_preserves_full_candidate_cohort():
    assert resolve_reviewed_account_scope(
        None,
        allowed_account_ids={FIRST, SECOND},
        confirmation=None,
    ) == {FIRST, SECOND}
