#!/usr/bin/env python
"""ADR-0017 slice-2 cutover gate: shadow-compare enforcement evidence.

Thin adapter over ``access.enforcement_evidence.enforcement_evidence_shadow_report``
(``app/services/enforcement_evidence.py``), which owns the query. This script
only parses arguments, opens a read-only session, calls the service, and
formats the result — no business logic.

Read-only: opens no writes. Run this against a real database only with
production access explicitly authorized and named by Michael (see
``docs/runbooks/ENFORCEMENT_EVIDENCE_SHADOW_COMPARISON.md`` for when to run
it, how to read each section, and how the result gates ADR-0017 slice 2).

    poetry run python -m scripts.enforcement_evidence_shadow_report \\
        --since-hours 168 [--limit 500] [--json]
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from datetime import UTC, datetime, timedelta

from app.services.db_session_adapter import db_session_adapter
from app.services.enforcement_evidence import (
    EnforcementEvidenceShadowReport,
    enforcement_evidence_shadow_report,
)


def _report_payload(report: EnforcementEvidenceShadowReport) -> dict[str, object]:
    return asdict(report)


def _print_human(report: EnforcementEvidenceShadowReport) -> None:
    print("enforcement evidence shadow report")
    print(f"  since        : {report.since.isoformat()}")
    print(f"  generated_at : {report.generated_at.isoformat()}")
    print(f"  row limit    : {report.limit}")

    print("\n  totals by (effect, outcome):")
    for total in report.totals_by_effect_outcome:
        print(f"    {total.effect:<24} {total.outcome:<14} {total.count}")

    print("\n  totals by failure_class:")
    for class_total in report.totals_by_failure_class:
        print(f"    {class_total.failure_class:<20} {class_total.count}")

    print("\n  per-NAS summary:")
    for nas in report.per_nas:
        name = nas.nas_name if nas.nas_name is not None else "<dangling nas id>"
        print(
            f"    {nas.nas_device_id} ({name}) "
            f"failed={nas.failed_count} applied={nas.applied_count} "
            f"last_success_at={nas.last_success_at} "
            f"oldest_first_failed_at={nas.oldest_first_failed_at}"
        )

    print(f"\n  currently failed (showing up to {report.limit}):")
    for failed in report.currently_failed:
        print(
            f"    subscription={failed.subscription_id} "
            f"(status={failed.subscription_status}) "
            f"nas={failed.nas_device_id} effect={failed.effect} "
            f"failure_class={failed.failure_class} attempts={failed.attempt_count} "
            f"first_failed_at={failed.first_failed_at} "
            f"last_attempt_at={failed.last_attempt_at} detail={failed.detail!r}"
        )

    print(
        f"\n  mismatch candidates (evidence vs current status, "
        f"showing up to {report.limit}):"
    )
    if not report.mismatch_candidates:
        print("    none")
    for candidate in report.mismatch_candidates:
        print(
            f"    subscription={candidate.subscription_id} "
            f"(status={candidate.subscription_status}) "
            f"nas={candidate.nas_device_id} effect={candidate.effect} "
            f"failure_class={candidate.failure_class} attempts={candidate.attempt_count} "
            f"first_failed_at={candidate.first_failed_at}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--since-hours",
        type=int,
        default=168,
        help="lower bound of the report window, in hours before now (default: 168)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=500,
        help="bound on each row-level list (default: 500)",
    )
    parser.add_argument("--json", action="store_true", help="emit the report as JSON")
    args = parser.parse_args()

    since = datetime.now(UTC) - timedelta(hours=args.since_hours)

    with db_session_adapter.read_session() as db:
        report = enforcement_evidence_shadow_report(db, since=since, limit=args.limit)

    if args.json:
        print(json.dumps(_report_payload(report), indent=2, default=str))
    else:
        _print_human(report)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
