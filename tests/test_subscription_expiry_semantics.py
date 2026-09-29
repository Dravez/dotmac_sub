from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from app.models.catalog import BillingMode, SubscriptionStatus
from app.schemas.catalog import SubscriptionRead


def _subscription_read(*, billing_mode: BillingMode, next_billing_at: datetime, end_at: datetime | None = None) -> SubscriptionRead:
    now = datetime(2026, 9, 29, tzinfo=UTC)
    return SubscriptionRead(
        id=uuid4(),
        subscriber_id=uuid4(),
        offer_id=uuid4(),
        status=SubscriptionStatus.active,
        billing_mode=billing_mode,
        next_billing_at=next_billing_at,
        end_at=end_at,
        created_at=now,
        updated_at=now,
    )


def test_prepaid_next_billing_at_is_exposed_as_service_expiry() -> None:
    paid_through = datetime(2026, 10, 1, tzinfo=UTC)
    subscription = _subscription_read(
        billing_mode=BillingMode.prepaid,
        next_billing_at=paid_through,
    )

    assert subscription.expires_at == paid_through


def test_postpaid_next_billing_at_remains_invoice_date_not_expiry() -> None:
    next_invoice = datetime(2026, 10, 1, tzinfo=UTC)
    subscription = _subscription_read(
        billing_mode=BillingMode.postpaid,
        next_billing_at=next_invoice,
    )

    assert subscription.expires_at is None


def test_explicit_contract_end_takes_priority_for_all_billing_modes() -> None:
    next_billing = datetime(2026, 10, 1, tzinfo=UTC)
    contract_end = datetime(2026, 12, 31, tzinfo=UTC)
    subscription = _subscription_read(
        billing_mode=BillingMode.prepaid,
        next_billing_at=next_billing,
        end_at=contract_end,
    )

    assert subscription.expires_at == contract_end
