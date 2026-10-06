"""Shared native Test Connection command setup, imported by each unit suite."""

from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from app.models.catalog import AccessCredential, Subscription, SubscriptionStatus
from app.models.subscriber import Subscriber
from app.models.system_user import SystemUser
from app.services import test_connection as owner
from app.services.owner_commands import CommandContext


@pytest.fixture
def test_service(
    db_session: Session,
    subscriber: Subscriber,
    subscription: Subscription,
    monkeypatch: pytest.MonkeyPatch,
) -> owner.ActivateTestConnectionCommand:
    staff = SystemUser(
        first_name="Ada",
        last_name="Engineer",
        email=f"test-{uuid4()}@example.com",
        is_active=True,
    )
    subscription.login = f"test-{uuid4()}"
    subscription.status = SubscriptionStatus.suspended
    subscriber.billing_enabled = False
    subscriber.is_active = False
    credential = AccessCredential(
        subscriber_id=subscriber.id,
        subscription_id=subscription.id,
        username=subscription.login,
        secret_hash="fixture-secret",
        is_active=False,
    )
    db_session.add_all([staff, credential])
    db_session.flush()
    actor_id, account_id, subscription_id = staff.id, subscriber.id, subscription.id
    db_session.commit()
    monkeypatch.setattr(
        owner,
        "configuration",
        lambda db: owner.TestConnectionConfiguration(
            default_hours=2, maximum_hours=24, deadline_verified=True
        ),
    )
    monkeypatch.setattr(
        "app.services.external_radius_targets.active_external_radius_targets",
        lambda db, **kwargs: [{"db_url": "postgresql://test@localhost/test_radius"}],
    )
    monkeypatch.setattr(
        "app.services.credential_crypto.decrypt_credential",
        lambda value: "fixture-password",
    )
    # Keep the real transactional outbox but do not perform network delivery in
    # owner-record unit tests. Transport behavior has separate tests.
    from app.services.events import dispatcher

    emit = dispatcher.emit_event
    monkeypatch.setattr(
        owner,
        "emit_event",
        lambda db, event_type, payload, **kwargs: emit(
            db, event_type, payload, dispatch_after_commit=False, **kwargs
        ),
    )
    command_id = uuid4()
    context = CommandContext(
        command_id=command_id,
        correlation_id=command_id,
        actor=str(actor_id),
        scope=owner.PERMISSION,
        reason="Customer connectivity troubleshooting",
        idempotency_key=str(command_id),
    )
    return owner.ActivateTestConnectionCommand(
        context=context,
        subscriber_id=account_id,
        subscription_id=subscription_id,
        actor_id=actor_id,
        duration_hours=2,
    )
