"""Owner for exact-second compensation from finalized customer outages."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

from sqlalchemy import exists, select
from sqlalchemy.orm import Session

from app.models.billing import ServiceEntitlement, ServiceEntitlementStatus
from app.models.catalog import Subscription
from app.models.domain_settings import SettingDomain
from app.models.network_monitoring import CustomerOutageInterval
from app.models.service_period_purchase import (
    OutageCompensationDecision,
    OutageCompensationDecisionInterval,
    OutageCompensationDecisionStatus,
)
from app.services.account_lifecycle import (
    BillingAnchorProjectionCommand,
    BillingAnchorProjectionSource,
    stage_subscription_billing_anchor,
)
from app.services.billing._common import lock_account
from app.services.domain_errors import DomainError
from app.services.owner_commands import (
    CommandContext,
    OwnerCommandDefinition,
    execute_owner_command,
)
from app.services.settings_spec import resolve_value

_OWNER = "financial.outage_compensation"
_POLICY_VERSION = 1
_APPLY_COMMAND = OwnerCommandDefinition(
    owner=_OWNER,
    concern="finalized outage service-period compensation",
    name="apply_outage_compensation",
)


class OutageCompensationError(DomainError, ValueError):
    """Stable fail-closed outage compensation error."""


def _error(suffix: str, message: str, **details: object) -> OutageCompensationError:
    return OutageCompensationError(
        code=f"{_OWNER}.{suffix}", message=message, details=details
    )


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class TimeInterval:
    starts_at: datetime
    ends_at: datetime


@dataclass(frozen=True, slots=True)
class OutageCompensationPreview:
    account_id: UUID
    subscription_id: UUID
    interval_ids: tuple[UUID, ...]
    status: OutageCompensationDecisionStatus
    threshold_seconds: int
    eligible_seconds: int
    funded_overlap_seconds: int
    tail_before: datetime | None
    tail_after: datetime | None
    fingerprint: str
    policy_snapshot: dict[str, object]


@dataclass(frozen=True, slots=True)
class ApplyOutageCompensationCommand:
    subscription_id: UUID
    expected_fingerprint: str
    idempotency_key: str
    effective_at: datetime
    context: CommandContext


@dataclass(frozen=True, slots=True)
class OutageCompensationResult:
    decision_id: UUID
    status: OutageCompensationDecisionStatus
    entitlement_id: UUID | None
    compensated_seconds: int
    tail_after: datetime | None
    replayed: bool


def merge_intervals(intervals: list[TimeInterval]) -> tuple[TimeInterval, ...]:
    ordered = sorted(
        (
            TimeInterval(_utc(item.starts_at), _utc(item.ends_at))
            for item in intervals
            if item.ends_at > item.starts_at
        ),
        key=lambda item: (item.starts_at, item.ends_at),
    )
    merged: list[TimeInterval] = []
    for item in ordered:
        if not merged or item.starts_at > merged[-1].ends_at:
            merged.append(item)
            continue
        previous = merged[-1]
        merged[-1] = TimeInterval(
            previous.starts_at, max(previous.ends_at, item.ends_at)
        )
    return tuple(merged)


def interval_seconds(intervals: tuple[TimeInterval, ...]) -> int:
    return sum(int((item.ends_at - item.starts_at).total_seconds()) for item in intervals)


def intersect_seconds(
    left: tuple[TimeInterval, ...], right: tuple[TimeInterval, ...]
) -> int:
    total = 0
    left_index = 0
    right_index = 0
    while left_index < len(left) and right_index < len(right):
        start = max(left[left_index].starts_at, right[right_index].starts_at)
        end = min(left[left_index].ends_at, right[right_index].ends_at)
        if end > start:
            total += int((end - start).total_seconds())
        if left[left_index].ends_at <= right[right_index].ends_at:
            left_index += 1
        else:
            right_index += 1
    return total


def _threshold_seconds(db: Session) -> int:
    raw = resolve_value(db, SettingDomain.billing, "outage_compensation_min_hours")
    if isinstance(raw, bool):
        raise _error("configuration_invalid", "Outage threshold is invalid.")
    try:
        hours = int(raw)
    except (TypeError, ValueError) as exc:
        raise _error("configuration_invalid", "Outage threshold is invalid.") from exc
    if hours < 1 or hours > 168:
        raise _error("configuration_invalid", "Outage threshold must be 1–168 hours.")
    return hours * 3600


def _pending_cluster(
    db: Session, subscription_id: UUID
) -> tuple[CustomerOutageInterval, ...]:
    consumed = exists(
        select(OutageCompensationDecisionInterval.id).where(
            OutageCompensationDecisionInterval.customer_outage_interval_id
            == CustomerOutageInterval.id
        )
    )
    rows = list(
        db.scalars(
            select(CustomerOutageInterval)
            .where(
                CustomerOutageInterval.subscription_id == subscription_id,
                CustomerOutageInterval.state == "confirmed_unavailable",
                CustomerOutageInterval.ended_at.is_not(None),
                CustomerOutageInterval.finalized_at.is_not(None),
                ~consumed,
            )
            .order_by(CustomerOutageInterval.started_at, CustomerOutageInterval.id)
        ).all()
    )
    if not rows:
        return ()
    cluster = [rows[0]]
    cluster_end = _utc(rows[0].ended_at)  # type: ignore[arg-type]
    for row in rows[1:]:
        start = _utc(row.started_at)
        if start > cluster_end:
            break
        cluster.append(row)
        assert row.ended_at is not None
        cluster_end = max(cluster_end, _utc(row.ended_at))
    return tuple(cluster)


def preview_outage_compensation(
    db: Session, *, subscription_id: UUID, effective_at: datetime
) -> OutageCompensationPreview:
    if resolve_value(db, SettingDomain.billing, "outage_compensation_enabled") is not True:
        raise _error("feature_disabled", "Outage compensation is not enabled.")
    subscription = db.get(Subscription, subscription_id)
    if subscription is None:
        raise _error("subscription_not_found", "Subscription was not found.")
    rows = _pending_cluster(db, subscription_id)
    if not rows:
        raise _error("no_finalized_outage", "No finalized outage awaits a decision.")
    threshold = _threshold_seconds(db)
    eligible = merge_intervals(
        [
            TimeInterval(row.started_at, row.ended_at)
            for row in rows
            if row.ended_at is not None and row.exclusion_candidate is None
        ]
    )
    eligible_seconds = interval_seconds(eligible)
    funded_rows = list(
        db.scalars(
            select(ServiceEntitlement)
            .where(
                ServiceEntitlement.subscription_id == subscription_id,
                ServiceEntitlement.account_id == subscription.subscriber_id,
                ServiceEntitlement.status == ServiceEntitlementStatus.active,
            )
            .order_by(ServiceEntitlement.starts_at, ServiceEntitlement.ends_at)
        ).all()
    )
    funded = merge_intervals(
        [TimeInterval(row.starts_at, row.ends_at) for row in funded_rows]
    )
    funded_overlap = intersect_seconds(eligible, funded)
    tail_before = max((item.ends_at for item in funded), default=None)
    status = OutageCompensationDecisionStatus.compensated
    if not eligible:
        status = OutageCompensationDecisionStatus.excluded
    elif eligible_seconds < threshold:
        status = OutageCompensationDecisionStatus.below_threshold
    elif funded_overlap <= 0 or tail_before is None:
        status = OutageCompensationDecisionStatus.no_funded_overlap
    elif (
        subscription.next_billing_at is not None
        and _utc(subscription.next_billing_at) > tail_before
    ):
        status = OutageCompensationDecisionStatus.review_required
    tail_after = (
        tail_before + timedelta(seconds=funded_overlap)
        if status is OutageCompensationDecisionStatus.compensated
        and tail_before is not None
        else tail_before
    )
    policy_snapshot: dict[str, object] = {
        "threshold_seconds": threshold,
        "duration_unit": "exact_seconds",
        "funding_cap": "outage_intersection_with_active_entitlements",
        "planned_maintenance": "exclude_explicit_candidates",
        "evaluated_at": _utc(effective_at).isoformat(),
    }
    payload = {
        "subscription_id": str(subscription_id),
        "account_id": str(subscription.subscriber_id),
        "intervals": [
            {
                "id": str(row.id),
                "start": _utc(row.started_at).isoformat(),
                "end": _utc(row.ended_at).isoformat() if row.ended_at else None,
                "excluded": row.exclusion_candidate,
            }
            for row in rows
        ],
        "funded": [
            (str(row.id), _utc(row.starts_at).isoformat(), _utc(row.ends_at).isoformat())
            for row in funded_rows
        ],
        "status": status.value,
        "eligible_seconds": eligible_seconds,
        "funded_overlap_seconds": funded_overlap,
        "tail_before": tail_before.isoformat() if tail_before else None,
        "tail_after": tail_after.isoformat() if tail_after else None,
        "policy": policy_snapshot,
        "policy_version": _POLICY_VERSION,
    }
    fingerprint = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return OutageCompensationPreview(
        account_id=subscription.subscriber_id,
        subscription_id=subscription.id,
        interval_ids=tuple(row.id for row in rows),
        status=status,
        threshold_seconds=threshold,
        eligible_seconds=eligible_seconds,
        funded_overlap_seconds=funded_overlap,
        tail_before=tail_before,
        tail_after=tail_after,
        fingerprint=fingerprint,
        policy_snapshot=policy_snapshot,
    )


def apply_outage_compensation(
    db: Session, command: ApplyOutageCompensationCommand
) -> OutageCompensationResult:
    return execute_owner_command(
        db,
        definition=_APPLY_COMMAND,
        context=command.context,
        operation=lambda: _stage_outage_compensation(db, command),
    )


def _stage_outage_compensation(
    db: Session, command: ApplyOutageCompensationCommand
) -> OutageCompensationResult:
    key = command.idempotency_key.strip()
    if not key:
        raise _error("idempotency_required", "An idempotency key is required.")
    subscription = db.get(Subscription, command.subscription_id)
    if subscription is None:
        raise _error("subscription_not_found", "Subscription was not found.")
    lock_account(db, str(subscription.subscriber_id))
    existing = db.scalar(
        select(OutageCompensationDecision).where(
            OutageCompensationDecision.idempotency_key == key
        )
    )
    if existing is not None:
        if existing.preview_fingerprint != command.expected_fingerprint:
            raise _error("idempotency_conflict", "Decision key names another preview.")
        return OutageCompensationResult(
            decision_id=existing.id,
            status=existing.status,
            entitlement_id=existing.entitlement_id,
            compensated_seconds=existing.funded_overlap_seconds,
            tail_after=existing.tail_after,
            replayed=True,
        )
    preview = preview_outage_compensation(
        db,
        subscription_id=subscription.id,
        effective_at=command.effective_at,
    )
    if preview.fingerprint != command.expected_fingerprint:
        raise _error("stale_preview", "Outage evidence changed before application.")
    decision = OutageCompensationDecision(
        account_id=preview.account_id,
        subscription_id=preview.subscription_id,
        status=preview.status,
        threshold_seconds=preview.threshold_seconds,
        eligible_seconds=preview.eligible_seconds,
        funded_overlap_seconds=preview.funded_overlap_seconds,
        tail_before=preview.tail_before,
        tail_after=preview.tail_after,
        policy_version=_POLICY_VERSION,
        policy_snapshot=preview.policy_snapshot,
        preview_fingerprint=preview.fingerprint,
        idempotency_key=key,
        created_by=command.context.actor,
        applied_at=_utc(command.effective_at),
    )
    db.add(decision)
    db.flush()
    rows = list(
        db.scalars(
            select(CustomerOutageInterval)
            .where(CustomerOutageInterval.id.in_(preview.interval_ids))
            .order_by(CustomerOutageInterval.started_at, CustomerOutageInterval.id)
        ).all()
    )
    eligible_seen: list[TimeInterval] = []
    for row in rows:
        assert row.ended_at is not None
        duration = int((_utc(row.ended_at) - _utc(row.started_at)).total_seconds())
        if row.exclusion_candidate is not None:
            included_seconds = 0
            excluded_seconds = duration
        else:
            before = interval_seconds(merge_intervals(eligible_seen))
            eligible_seen.append(TimeInterval(row.started_at, row.ended_at))
            after = interval_seconds(merge_intervals(eligible_seen))
            included_seconds = after - before
            excluded_seconds = duration - included_seconds
        db.add(
            OutageCompensationDecisionInterval(
                decision_id=decision.id,
                customer_outage_interval_id=row.id,
                incident_id=row.incident_id,
                started_at=row.started_at,
                ended_at=row.ended_at,
                included_seconds=included_seconds,
                excluded_seconds=excluded_seconds,
                exclusion_reason=row.exclusion_candidate,
            )
        )
    entitlement_id: UUID | None = None
    if preview.status is OutageCompensationDecisionStatus.compensated:
        assert preview.tail_before is not None and preview.tail_after is not None
        tail_currency = db.scalar(
            select(ServiceEntitlement.currency)
            .where(
                ServiceEntitlement.subscription_id == subscription.id,
                ServiceEntitlement.status == ServiceEntitlementStatus.active,
                ServiceEntitlement.ends_at == preview.tail_before,
            )
            .order_by(ServiceEntitlement.created_at.desc())
        ) or "NGN"
        entitlement = ServiceEntitlement(
            account_id=subscription.subscriber_id,
            subscription_id=subscription.id,
            source_outage_compensation_id=decision.id,
            starts_at=preview.tail_before,
            ends_at=preview.tail_after,
            amount_funded=Decimal("0.00"),
            currency=tail_currency,
            status=ServiceEntitlementStatus.active,
            metadata_={
                "source": "outage_compensation",
                "decision_id": str(decision.id),
                "exact_seconds": preview.funded_overlap_seconds,
                "source_interval_ids": [str(item) for item in preview.interval_ids],
            },
        )
        db.add(entitlement)
        db.flush()
        entitlement_id = entitlement.id
        decision.entitlement_id = entitlement.id
        stage_subscription_billing_anchor(
            db,
            subscription,
            BillingAnchorProjectionCommand(
                subscription_id=subscription.id,
                expected_previous=subscription.next_billing_at,
                target=preview.tail_after,
                source=BillingAnchorProjectionSource.outage_compensation,
                evidence_ref=f"outage-compensation:{decision.id}",
            ),
        )
    db.flush()
    return OutageCompensationResult(
        decision_id=decision.id,
        status=decision.status,
        entitlement_id=entitlement_id,
        compensated_seconds=decision.funded_overlap_seconds,
        tail_after=decision.tail_after,
        replayed=False,
    )


__all__ = [
    "ApplyOutageCompensationCommand",
    "OutageCompensationError",
    "OutageCompensationPreview",
    "OutageCompensationResult",
    "TimeInterval",
    "apply_outage_compensation",
    "intersect_seconds",
    "merge_intervals",
    "preview_outage_compensation",
]
