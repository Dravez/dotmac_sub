"""PostgreSQL evidence against the real migrated schema, never create_all."""

from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier
from uuid import UUID, uuid4

import pytest
from sqlalchemy import inspect, select, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.models.event_store import EventStore
from app.models.service_extension import (
    ServiceExtension,
    ServiceExtensionPurpose,
    ServiceExtensionScope,
)
from app.models.subscriber import Subscriber
from app.services import service_extensions
from app.services.events.types import EventType
from app.services.owner_commands import CommandContext
from app.services.subscriber import _default_reseller_id

NOW = datetime(2026, 10, 6, 12, tzinfo=UTC)


@pytest.fixture(autouse=True)
def isolated_delivery(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.services.events.dispatcher.run_after_commit", lambda *_: None
    )
    monkeypatch.setattr(service_extensions, "_now_utc", lambda: NOW)


def _create(
    db: Session, customer_id: UUID, key: str
) -> service_extensions.CreateServiceExtensionOutcome:
    return service_extensions.create_service_extension(
        db,
        service_extensions.CreateServiceExtensionCommand(
            context=CommandContext.system(
                actor="service:pytest-test-connection-concurrency",
                scope=service_extensions.CREATE_SCOPE,
                reason="Verify customer-scoped serialization",
                idempotency_key=key,
            ),
            reason="Temporary connection test",
            purpose=ServiceExtensionPurpose.test_connection,
            window_start=NOW - timedelta(hours=1),
            window_end=NOW,
            days=1,
            scope_type=ServiceExtensionScope.subscribers,
            subscriber_identifiers=(str(customer_id),),
            subscriber_ids_resolved=True,
        ),
    )


@pytest.mark.parametrize("same_key", (False, True))
def test_parallel_creation_count_and_idempotency(engine, same_key: bool) -> None:
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    with factory() as setup:
        customer = Subscriber(
            first_name="Test Connection",
            last_name="Concurrency",
            email=f"connection-{uuid4()}@example.com",
            reseller_id=_default_reseller_id(setup),
        )
        setup.add(customer)
        setup.commit()
        customer_id = customer.id
        for _ in range(4):
            _create(setup, customer_id, str(uuid4()))

    barrier = Barrier(2)
    shared_key = str(uuid4())

    def worker(_: int) -> tuple[UUID, bool, int]:
        with factory() as db:
            barrier.wait(timeout=15)
            outcome = _create(db, customer_id, shared_key if same_key else str(uuid4()))
            event = db.scalar(
                select(EventStore).where(
                    EventStore.event_type == EventType.test_connection_created.value,
                    EventStore.account_id == customer_id,
                    EventStore.payload["extension_id"].astext
                    == str(outcome.extension_id),
                )
            )
            assert event is not None
            return outcome.extension_id, outcome.replayed, event.payload["count_7d"]

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(worker, range(2)))
    if same_key:
        assert outcomes[0][0] == outcomes[1][0]
        assert sorted(item[1] for item in outcomes) == [False, True]
        assert [item[2] for item in outcomes] == [5, 5]
    else:
        assert outcomes[0][0] != outcomes[1][0]
        assert sorted(item[2] for item in outcomes) == [5, 6]
    with factory() as db:
        assert db.query(EventStore).filter_by(
            event_type=EventType.test_connection_created.value, account_id=customer_id
        ).count() == (5 if same_key else 6)


def test_migrated_purpose_constraint_and_receipt_uniqueness(engine) -> None:
    inspector = inspect(engine)
    assert "purpose" in {
        column["name"] for column in inspector.get_columns("service_extensions")
    }
    assert "serviceextensionpurpose" in {
        constraint["name"]
        for constraint in inspector.get_check_constraints("service_extensions")
    }
    assert "uq_test_connection_review_step" in {
        constraint["name"]
        for constraint in inspector.get_unique_constraints(
            "test_connection_finance_reviews"
        )
    }
    with engine.connect() as db:
        with pytest.raises(IntegrityError):
            with db.begin():
                db.execute(
                    text(
                        "INSERT INTO service_extensions (id,reason,window_start,window_end,days,scope_type,status,created_at,purpose) VALUES (:id,'invalid purpose',:start,:end,1,'subscribers','pending',:end,'invented')"
                    ),
                    {"id": uuid4(), "start": NOW - timedelta(hours=1), "end": NOW},
                )


def test_failed_creation_rolls_back_classification_and_event(
    engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    with factory() as setup:
        customer = Subscriber(
            first_name="Test",
            last_name="Rollback",
            email=f"rollback-{uuid4()}@example.com",
            reseller_id=_default_reseller_id(setup),
        )
        setup.add(customer)
        setup.commit()
        customer_id = customer.id
    original = service_extensions._stage_test_connection_events

    def failed_stage(
        db: Session,
        *,
        extension: ServiceExtension,
        customer_ids: Sequence[UUID],
        context: CommandContext,
    ) -> None:
        original(db, extension=extension, customer_ids=customer_ids, context=context)
        raise RuntimeError("injected failure after event staging")

    monkeypatch.setattr(
        service_extensions, "_stage_test_connection_events", failed_stage
    )
    with factory() as db:
        with pytest.raises(RuntimeError, match="injected failure"):
            _create(db, customer_id, str(uuid4()))
        assert (
            db.query(EventStore)
            .filter_by(
                event_type=EventType.test_connection_created.value,
                account_id=customer_id,
            )
            .count()
            == 0
        )
        assert (
            db.query(ServiceExtension)
            .filter(
                ServiceExtension.scope_subscriber_ids.cast(JSONB).contains(
                    [str(customer_id)]
                )
            )
            .count()
            == 0
        )
