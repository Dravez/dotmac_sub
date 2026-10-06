"""Fast unit lane: real creation, workflow decision, and staff queue evidence."""

from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.admin_alert import AdminNotification
from app.models.automation import AutomationRun
from app.models.event_store import EventStore
from app.models.notification import Notification, NotificationChannel
from app.models.service_extension import (
    ServiceExtension,
    ServiceExtensionPurpose,
    ServiceExtensionScope,
    ServiceExtensionStatus,
)
from app.models.service_team import ServiceTeam, ServiceTeamMember
from app.models.test_connection_review import TestConnectionFinanceReview as Review
from app.schemas.test_connection import TestConnectionCreated as CreationEvidence
from app.services import automation_rules, service_extensions
from app.services.automation_contracts import AutomationOperator
from app.services.events.handlers.automation import AutomationEventHandler
from app.services.events.types import Event, EventType
from app.services.operator_tenant import OPERATOR_TENANT_ID
from app.services.owner_commands import CommandContext
from app.services.test_connection_finance import (
    NotifyTestConnectionFinanceCommand,
    notify_test_connection_finance,
)
from app.services.test_connection_finance import (
    TestConnectionFinanceError as FinanceError,
)
from tests.staff_identity_fixtures import add_bound_staff_user

NOW = datetime(2026, 10, 6, 12, tzinfo=UTC)


@pytest.fixture(autouse=True)
def no_external_dispatch(monkeypatch: pytest.MonkeyPatch) -> None:
    # Keep the actual durable outbox; drive only the tested handler explicitly.
    monkeypatch.setattr(
        "app.services.events.dispatcher.run_after_commit", lambda *_: None
    )
    monkeypatch.setattr(service_extensions, "_now_utc", lambda: NOW)


def _context(scope: str, *, key: str | None = None) -> CommandContext:
    return CommandContext.system(
        actor="service:pytest-test-connections",
        scope=scope,
        reason="Verify Test Connection review",
        idempotency_key=key or str(uuid4()),
    )


def _create(
    db: Session,
    customer_id: UUID,
    *,
    purpose: ServiceExtensionPurpose = ServiceExtensionPurpose.test_connection,
    key: str | None = None,
) -> tuple[UUID, Event | None]:
    db.commit()
    outcome = service_extensions.create_service_extension(
        db,
        service_extensions.CreateServiceExtensionCommand(
            context=_context(service_extensions.CREATE_SCOPE, key=key),
            reason="Temporary service for connection testing",
            window_start=NOW - timedelta(hours=1),
            window_end=NOW,
            days=1,
            scope_type=ServiceExtensionScope.subscribers,
            subscriber_identifiers=(str(customer_id),),
            subscriber_ids_resolved=True,
            purpose=purpose,
        ),
    )
    row = db.scalar(
        select(EventStore).where(
            EventStore.event_type == EventType.test_connection_created.value,
            EventStore.payload["extension_id"].as_string() == str(outcome.extension_id),
        )
    )
    event = (
        Event(
            event_type=EventType.test_connection_created,
            event_id=row.event_id,
            payload=dict(row.payload),
            occurred_at=NOW,
        )
        if row
        else None
    )
    db.commit()
    return outcome.extension_id, event


@dataclass(frozen=True)
class FinanceSetup:
    team_id: UUID
    version_id: UUID
    recipients: tuple[UUID, ...]
    emails: tuple[str, ...]


def _finance_workflow(db: Session) -> FinanceSetup:
    team = ServiceTeam(name=f"Finance {uuid4()}", is_active=True)
    db.add(team)
    db.flush()
    users = [
        add_bound_staff_user(db, email=f"finance-{uuid4()}@example.com")
        for _ in range(2)
    ]
    for user, person in users:
        db.add(ServiceTeamMember(team_id=team.id, person_id=person.id, is_active=True))
    values = (
        team.id,
        tuple(user.id for user, _ in users),
        tuple(user.email for user, _ in users),
    )
    db.commit()
    rule = automation_rules.create_rule(
        db,
        automation_rules.CreateAutomationRuleCommand(
            tenant_id=OPERATOR_TENANT_ID,
            key=f"billing.test_connection.review_{uuid4().hex}",
            name="Suspicious Test Connection review",
            description="Finance review after more than five creations in seven days",
            trigger_key="billing.test_connection.created",
            conditions=(
                automation_rules.AutomationCondition(
                    field_key="count_7d",
                    operator=AutomationOperator.greater_than,
                    value=5,
                ),
            ),
            actions=(
                automation_rules.AutomationActionStep(
                    action_key="billing.test_connection.notify_finance",
                    inputs=(
                        automation_rules.AutomationActionValue(
                            key="service_team_id", value=values[0]
                        ),
                    ),
                ),
            ),
            permission_keys=frozenset({"*"}),
            context=_context("automation:rule:create"),
        ),
    )
    published = automation_rules.publish_rule(
        db,
        automation_rules.PublishAutomationRuleCommand(
            tenant_id=OPERATOR_TENANT_ID,
            rule_id=rule.rule_id,
            permission_keys=frozenset({"*"}),
            context=_context("automation:rule:publish"),
        ),
    )
    assert published.version_id is not None
    return FinanceSetup(values[0], published.version_id, values[1], values[2])


def test_real_workflow_stays_silent_at_five_and_notifies_finance_at_six(
    db_session: Session, active_subscription
) -> None:
    customer_id = active_subscription.subscriber_id
    finance = _finance_workflow(db_session)
    handler = AutomationEventHandler()
    for count in range(1, 7):
        extension_id, event = _create(db_session, customer_id)
        assert event is not None
        assert CreationEvidence.model_validate(event.payload).count_7d == count
        handler.handle(db_session, event)
        assert db_session.query(AdminNotification).count() == (0 if count <= 5 else 2)
        db_session.commit()
    assert {row.system_user_id for row in db_session.query(AdminNotification)} == set(
        finance.recipients
    )
    assert {
        row.recipient
        for row in db_session.query(Notification).filter(
            Notification.channel == NotificationChannel.email
        )
    } == set(finance.emails)
    assert db_session.query(Notification).count() == 4
    assert db_session.query(Review).count() == 1
    assert [
        row.matched
        for row in db_session.query(AutomationRun).order_by(AutomationRun.created_at)
    ] == [False] * 5 + [True]
    for row in db_session.query(AdminNotification):
        assert "Test Connections created: 6" in row.body
        assert row.target_url == f"/admin/billing/service-extensions/{extension_id}"
    db_session.commit()
    handler.handle(db_session, event)
    assert db_session.query(Notification).count() == 4


def test_window_customer_isolation_and_explicit_classification(
    db_session: Session, active_subscription
) -> None:
    customer_id = active_subscription.subscriber_id
    other_id = uuid4()
    for created_at, target, purpose, status in (
        (
            NOW - timedelta(days=7),
            customer_id,
            ServiceExtensionPurpose.test_connection,
            ServiceExtensionStatus.pending,
        ),
        (
            NOW - timedelta(days=8),
            customer_id,
            ServiceExtensionPurpose.test_connection,
            ServiceExtensionStatus.applied,
        ),
        (
            NOW + timedelta(seconds=1),
            customer_id,
            ServiceExtensionPurpose.test_connection,
            ServiceExtensionStatus.pending,
        ),
        (
            NOW - timedelta(days=1),
            customer_id,
            ServiceExtensionPurpose.outage_compensation,
            ServiceExtensionStatus.pending,
        ),
        (
            NOW - timedelta(days=1),
            other_id,
            ServiceExtensionPurpose.test_connection,
            ServiceExtensionStatus.pending,
        ),
        (
            NOW - timedelta(days=1),
            customer_id,
            ServiceExtensionPurpose.test_connection,
            ServiceExtensionStatus.canceled,
        ),
        (
            NOW - timedelta(days=2),
            customer_id,
            ServiceExtensionPurpose.test_connection,
            ServiceExtensionStatus.reversed,
        ),
    ):
        db_session.add(
            ServiceExtension(
                reason="To test connection",
                purpose=purpose,
                days=1,
                window_start=NOW - timedelta(hours=1),
                window_end=NOW,
                scope_type=ServiceExtensionScope.subscribers,
                scope_subscriber_ids=[str(target)],
                status=status,
                created_at=created_at,
            )
        )
    _, event = _create(db_session, customer_id)
    assert event is not None
    evidence = CreationEvidence.model_validate(event.payload)
    assert evidence.count_7d == 3  # two historical creations plus the current one
    assert evidence.window_start == NOW - timedelta(days=7)
    assert len(evidence.recent_connections) == 3


def test_creation_replay_does_not_increment_or_emit_twice(
    db_session: Session, active_subscription
) -> None:
    customer_id = active_subscription.subscriber_id
    key = str(uuid4())
    first_id, first_event = _create(db_session, customer_id, key=key)
    second_id, second_event = _create(db_session, customer_id, key=key)
    assert first_id == second_id
    assert first_event is not None and second_event is not None
    assert first_event.event_id == second_event.event_id
    assert second_event.payload["count_7d"] == 1
    assert (
        db_session.query(EventStore)
        .filter_by(event_type=EventType.test_connection_created.value)
        .count()
        == 1
    )
    db_session.commit()
    with pytest.raises(
        service_extensions.ServiceExtensionError, match="different extension inputs"
    ):
        _create(
            db_session,
            customer_id,
            key=key,
            purpose=ServiceExtensionPurpose.outage_compensation,
        )


def test_outage_compensation_has_no_test_event(
    db_session: Session, active_subscription
) -> None:
    _, event = _create(
        db_session,
        active_subscription.subscriber_id,
        purpose=ServiceExtensionPurpose.outage_compensation,
    )
    assert event is None


def test_test_connection_rejects_broad_scope(db_session: Session) -> None:
    db_session.commit()
    with pytest.raises(
        service_extensions.ServiceExtensionError, match="explicit selected customers"
    ):
        service_extensions.create_service_extension(
            db_session,
            service_extensions.CreateServiceExtensionCommand(
                context=_context(service_extensions.CREATE_SCOPE),
                reason="Test",
                window_start=NOW - timedelta(hours=1),
                window_end=NOW,
                days=1,
                scope_type=ServiceExtensionScope.pop_site,
                scope_id=uuid4(),
                purpose=ServiceExtensionPurpose.test_connection,
            ),
        )
    assert db_session.query(ServiceExtension).count() == 0


def test_finance_action_replay_preserves_audience_and_rejects_changed_team(
    db_session: Session, active_subscription
) -> None:
    customer_id = active_subscription.subscriber_id
    finance = _finance_workflow(db_session)
    extension_id, event = _create(db_session, customer_id)
    assert event is not None
    command = NotifyTestConnectionFinanceCommand(
        context=_context("automation:runtime"),
        tenant_id=OPERATOR_TENANT_ID,
        event_id=event.event_id,
        extension_id=extension_id,
        rule_version_id=finance.version_id,
        step_index=0,
        service_team_id=finance.team_id,
    )
    result = notify_test_connection_finance(db_session, command)
    # Simulate a committed action followed by worker failure before step acknowledgement.
    new_user, person = add_bound_staff_user(
        db_session, email=f"new-finance-{uuid4()}@example.com"
    )
    db_session.add(
        ServiceTeamMember(team_id=finance.team_id, person_id=person.id, is_active=True)
    )
    db_session.commit()
    replay = notify_test_connection_finance(db_session, command)
    assert replay.replayed and replay.recipient_ids == result.recipient_ids
    assert new_user.id not in replay.recipient_ids
    assert db_session.query(Notification).count() == 4
    db_session.commit()
    with pytest.raises(FinanceError, match="different evidence"):
        notify_test_connection_finance(
            db_session, replace(command, service_team_id=uuid4())
        )


@pytest.mark.parametrize("invalid", ("tenant", "event", "extension", "team"))
def test_finance_action_fails_closed_without_partial_notifications(
    db_session: Session, active_subscription, invalid: str
) -> None:
    customer_id = active_subscription.subscriber_id
    finance = _finance_workflow(db_session)
    extension_id, event = _create(db_session, customer_id)
    assert event is not None
    command = NotifyTestConnectionFinanceCommand(
        context=_context("automation:runtime"),
        tenant_id=OPERATOR_TENANT_ID,
        event_id=event.event_id,
        extension_id=extension_id,
        rule_version_id=finance.version_id,
        step_index=0,
        service_team_id=finance.team_id,
    )
    field = {
        "tenant": "tenant_id",
        "event": "event_id",
        "extension": "extension_id",
        "team": "service_team_id",
    }[invalid]
    with pytest.raises(FinanceError):
        notify_test_connection_finance(db_session, replace(command, **{field: uuid4()}))
    assert db_session.query(Notification).count() == 0
    assert db_session.query(Review).count() == 0


def test_workflow_requires_module_permissions(db_session: Session) -> None:
    with pytest.raises(automation_rules.AutomationRuleError, match="authorized"):
        automation_rules._validate_definition(
            db=db_session,
            trigger_key="billing.test_connection.created",
            conditions=(),
            actions=(
                automation_rules.AutomationActionStep(
                    action_key="billing.test_connection.notify_finance",
                    inputs=(
                        automation_rules.AutomationActionValue(
                            key="service_team_id", value=uuid4()
                        ),
                    ),
                ),
            ),
            permission_keys=frozenset({"automation:rule:create"}),
        )


def test_delayed_event_uses_its_original_count(
    db_session: Session, active_subscription
) -> None:
    customer_id = active_subscription.subscriber_id
    _finance_workflow(db_session)
    events = [_create(db_session, customer_id)[1] for _ in range(8)]
    sixth = events[5]
    assert sixth is not None
    AutomationEventHandler().handle(db_session, sixth)
    assert all(
        "Test Connections created: 6" in row.body
        for row in db_session.query(AdminNotification)
    )
    assert db_session.query(AdminNotification).count() == 2


def test_notification_failure_rolls_back_all_recipients(
    db_session: Session, active_subscription, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.services import test_connection_finance as finance_service

    customer_id = active_subscription.subscriber_id
    finance = _finance_workflow(db_session)
    extension_id, event = _create(db_session, customer_id)
    assert event is not None
    original = finance_service.stage_staff_direct_notification
    calls = 0

    def failing_stage(db: Session, command):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("injected notification failure")
        return original(db, command)

    monkeypatch.setattr(
        finance_service, "stage_staff_direct_notification", failing_stage
    )
    with pytest.raises(RuntimeError, match="injected notification failure"):
        notify_test_connection_finance(
            db_session,
            NotifyTestConnectionFinanceCommand(
                context=_context("automation:runtime"),
                tenant_id=OPERATOR_TENANT_ID,
                event_id=event.event_id,
                extension_id=extension_id,
                rule_version_id=finance.version_id,
                step_index=0,
                service_team_id=finance.team_id,
            ),
        )
    assert db_session.query(Notification).count() == 0
    assert db_session.query(AdminNotification).count() == 0
    assert db_session.query(Review).count() == 0


def test_request_references_are_bounded_but_count_is_complete(
    db_session: Session, active_subscription
) -> None:
    customer_id = active_subscription.subscriber_id
    events = [_create(db_session, customer_id)[1] for _ in range(12)]
    event = events[-1]
    assert event is not None
    evidence = CreationEvidence.model_validate(event.payload)
    assert evidence.count_7d == 12
    assert len(evidence.recent_connections) == 10
    assert evidence.extension_id in {
        ref.extension_id for ref in evidence.recent_connections
    }
