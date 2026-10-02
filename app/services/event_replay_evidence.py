"""Typed, read-only replay envelope sourced from one durable event row."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.event_store import EventStatus, EventStore
from app.services.domain_errors import DomainError
from app.services.events.types import EventType

OWNER = "events.replay_evidence"


@dataclass(frozen=True, slots=True)
class GetDurableEventForReplayQuery:
    event_id: UUID
    expected_event_type: EventType


@dataclass(frozen=True, slots=True)
class DurableEventReplayEvidence:
    event_id: UUID
    event_type: EventType
    stored_at: datetime
    payload: Mapping[str, object]
    actor: str | None
    subscriber_id: UUID | None
    account_id: UUID | None
    subscription_id: UUID | None
    invoice_id: UUID | None
    service_order_id: UUID | None


class DurableEventReplayError(DomainError):
    pass


def get_durable_event_for_replay(
    db: Session, query: GetDurableEventForReplayQuery
) -> DurableEventReplayEvidence:
    row = db.scalar(select(EventStore).where(EventStore.event_id == query.event_id))
    if row is None:
        raise DurableEventReplayError(
            code=f"{OWNER}.not_found",
            message="The original event is no longer available for retry.",
        )
    if row.status is not EventStatus.failed:
        raise DurableEventReplayError(
            code=f"{OWNER}.event_not_failed",
            message="The original event is not in a retryable state.",
        )
    if not row.is_active:
        raise DurableEventReplayError(
            code=f"{OWNER}.event_inactive",
            message="The original event is no longer available for retry.",
        )
    try:
        event_type = EventType(row.event_type)
    except ValueError as exc:
        raise DurableEventReplayError(
            code=f"{OWNER}.event_type_invalid",
            message="The original event type can no longer be replayed.",
        ) from exc
    if event_type is not query.expected_event_type:
        raise DurableEventReplayError(
            code=f"{OWNER}.event_type_mismatch",
            message="The stored event does not match the run being retried.",
        )
    if not isinstance(row.payload, Mapping):
        raise DurableEventReplayError(
            code=f"{OWNER}.payload_invalid",
            message="The original event data is incomplete and cannot be retried.",
        )
    return DurableEventReplayEvidence(
        event_id=row.event_id,
        event_type=event_type,
        stored_at=row.created_at,
        payload=dict(row.payload),
        actor=row.actor,
        subscriber_id=row.subscriber_id,
        account_id=row.account_id,
        subscription_id=row.subscription_id,
        invoice_id=row.invoice_id,
        service_order_id=row.service_order_id,
    )


__all__ = [
    "DurableEventReplayEvidence",
    "DurableEventReplayError",
    "GetDurableEventForReplayQuery",
    "get_durable_event_for_replay",
]
