from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from app.models.event_store import EventStatus, EventStore
from app.services.event_replay_evidence import (
    DurableEventReplayError,
    GetDurableEventForReplayQuery,
    get_durable_event_for_replay,
)
from app.services.events.types import EventType


def test_replay_query_returns_only_the_exact_expected_event(
    db_session: Session,
) -> None:
    event_id = uuid4()
    db_session.add(
        EventStore(
            event_id=event_id,
            event_type=EventType.support_ticket_created.value,
            payload={"tenant_id": str(uuid4()), "ticket_id": str(uuid4())},
            status=EventStatus.failed,
        )
    )
    db_session.commit()

    result = get_durable_event_for_replay(
        db_session,
        GetDurableEventForReplayQuery(
            event_id=event_id,
            expected_event_type=EventType.support_ticket_created,
        ),
    )

    assert result.event_id == event_id
    assert result.event_type is EventType.support_ticket_created
    assert result.payload["ticket_id"]


def test_replay_query_rejects_a_different_event_type(db_session: Session) -> None:
    event_id = uuid4()
    db_session.add(
        EventStore(
            event_id=event_id,
            event_type=EventType.support_ticket_created.value,
            payload={},
            status=EventStatus.failed,
        )
    )
    db_session.commit()

    with pytest.raises(DurableEventReplayError) as exc_info:
        get_durable_event_for_replay(
            db_session,
            GetDurableEventForReplayQuery(
                event_id=event_id,
                expected_event_type=EventType.subscriber_created,
            ),
        )

    assert exc_info.value.code == "events.replay_evidence.event_type_mismatch"
