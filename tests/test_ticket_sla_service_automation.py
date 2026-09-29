from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.models.catalog import Subscription, SubscriptionStatus
from app.models.enforcement_lock import EnforcementReason
from app.models.support import Ticket, TicketStatus
from app.services import account_lifecycle, ticket_sla_service_automation
from app.services.owner_commands import CommandContext


def _command(ticket_id):
    event_id = uuid4()
    return ticket_sla_service_automation.SuspendTicketServiceForSlaBreachCommand(
        ticket_id=ticket_id,
        event_id=event_id,
        rule_id=uuid4(),
        rule_version_id=uuid4(),
        step_index=0,
        context=CommandContext.system(
            actor="automation-runtime",
            scope="subscription:suspend",
            reason="test ticket SLA breach consequence",
            correlation_id=event_id,
            causation_id=event_id,
            idempotency_key=f"automation-test:{event_id}",
        ),
    )


def test_suspends_the_only_active_service(
    db_session, subscriber, active_subscription, monkeypatch
):
    ticket = Ticket(
        title="SLA breached",
        status=TicketStatus.open.value,
        priority="urgent",
        customer_account_id=subscriber.id,
    )
    db_session.add(ticket)
    db_session.flush()
    ticket_id = ticket.id
    subscription_id = active_subscription.id
    db_session.commit()

    calls = []
    lock_id = uuid4()

    def fake_suspend(db, selected_id, **kwargs):
        calls.append((selected_id, kwargs))
        return SimpleNamespace(id=lock_id)

    monkeypatch.setattr(account_lifecycle, "suspend_subscription", fake_suspend)

    outcome = ticket_sla_service_automation.suspend_unique_active_service_for_ticket_sla_breach(
        db_session,
        _command(ticket_id),
    )

    assert outcome.subscription_id == subscription_id
    assert outcome.enforcement_lock_id == lock_id
    assert not outcome.replayed
    assert calls[0][0] == str(subscription_id)
    assert calls[0][1]["reason"] is EnforcementReason.ticket_sla
    assert calls[0][1]["source"].startswith("automation:ticket-sla:")


def test_multiple_active_services_fail_closed(
    db_session, subscriber, active_subscription, catalog_offer, monkeypatch
):
    second = Subscription(
        subscriber_id=subscriber.id,
        offer_id=catalog_offer.id,
        status=SubscriptionStatus.active,
    )
    ticket = Ticket(
        title="Ambiguous SLA service",
        status=TicketStatus.open.value,
        priority="urgent",
        customer_account_id=subscriber.id,
    )
    db_session.add_all((second, ticket))
    db_session.flush()
    ticket_id = ticket.id
    db_session.commit()

    called = False

    def fake_suspend(*args, **kwargs):
        nonlocal called
        called = True
        return SimpleNamespace(id=uuid4())

    monkeypatch.setattr(account_lifecycle, "suspend_subscription", fake_suspend)

    with pytest.raises(
        ticket_sla_service_automation.TicketSlaServiceAutomationError
    ) as exc_info:
        ticket_sla_service_automation.suspend_unique_active_service_for_ticket_sla_breach(
            db_session,
            _command(ticket_id),
        )

    assert exc_info.value.code.endswith(".active_service_ambiguous")
    assert not called
