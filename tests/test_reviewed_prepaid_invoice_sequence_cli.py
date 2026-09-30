"""Synthetic manifest coverage for the dry-run-first typed adapter."""

import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

from app.services.prepaid_calendar_contracts import (
    ReviewedPrepaidCalendarBasis,
    ReviewedPrepaidCalendarSelection,
)
from scripts.billing.reconstruct_reviewed_prepaid_invoice_sequence import (
    _calendar,
    _manifest,
)


@pytest.mark.parametrize(
    "basis",
    [
        ReviewedPrepaidCalendarBasis.business_midnight,
        ReviewedPrepaidCalendarBasis.documented_anniversary,
    ],
)
def test_manifest_parses_explicit_calendar_into_typed_selection(
    tmp_path: Path, basis: ReviewedPrepaidCalendarBasis
):
    calendar: dict[str, object] = {"basis": basis.value}
    anchor = datetime(2026, 7, 29, tzinfo=UTC)
    if basis is ReviewedPrepaidCalendarBasis.documented_anniversary:
        calendar["expected_initial_anchor_at"] = anchor.isoformat()
    payload = {
        "subscription_id": str(uuid4()),
        "documents": [
            {
                "invoice_id": str(uuid4()),
                "line_id": str(uuid4()),
                "service_start_on": "2026-06-29",
                "next_billing_on": "2026-07-29",
                "expected_total": "100.00",
            }
        ],
        "allocations": [],
        "settlement_evidence": [],
        "existing_allocation_evidence": [],
        "expected_opening_credit": "20.00",
        "expected_post_repair_credit": "0.00",
        "expected_authoritative_prepaid_funding": "0.00",
        "approval": {
            "approver_system_user_id": str(uuid4()),
            "approver_name": "Synthetic Approver",
            "ticket_reference": "pytest-sequence",
        },
        "calendar": calendar,
    }
    path = tmp_path / "synthetic-manifest.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    query = _manifest(path)
    assert query.calendar == ReviewedPrepaidCalendarSelection(
        basis=basis,
        expected_initial_anchor_at=anchor
        if basis is ReviewedPrepaidCalendarBasis.documented_anniversary
        else None,
    )
    del payload["calendar"]
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert _manifest(path).calendar == ReviewedPrepaidCalendarSelection()
    assert query.approval.approved_at is None
    assert query.approval.evidence_sha256 is None


@pytest.mark.parametrize(
    "payload",
    [
        None,
        {},
        {"basis": "utc_midnight"},
        {"basis": "documented_anniversary"},
        {
            "basis": "documented_anniversary",
            "expected_initial_anchor_at": "2026-07-29T00:00:00",
        },
        {
            "basis": "business_midnight",
            "expected_initial_anchor_at": "2026-07-29T00:00:00Z",
        },
        {"basis": "business_midnight", "clock": "01:00"},
    ],
)
def test_calendar_parser_refuses_ambiguous_or_arbitrary_overrides(payload: object):
    with pytest.raises(ValueError):
        _calendar(payload)
