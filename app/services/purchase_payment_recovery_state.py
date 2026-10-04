"""Purchase refund/reversal state participant in the payment owner's transaction."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.billing import Payment, PaymentStatus
from app.models.service_period_purchase import (
    PrepaidPeriodPurchase,
    PrepaidPeriodPurchaseStatus,
)
from app.services.domain_errors import DomainError


def _error(suffix: str, message: str) -> DomainError:
    return DomainError(
        code=f"financial.purchase_payment_recovery_state.{suffix}", message=message
    )


_OWNER = "financial.purchase_payment_recovery_state"


@dataclass(frozen=True, slots=True)
class PurchasePaymentRecoveryCommand:
    payment_id: UUID
    evidence_ref: str


def stage_purchase_payment_recovery(
    db: Session, command: PurchasePaymentRecoveryCommand
) -> None:
    """Participate in the payment owner's confirmed refund/reversal transaction."""
    payment = db.get(Payment, command.payment_id)
    if payment is None or payment.reserved_for_purchase_id is None:
        return
    if not command.evidence_ref.strip() or payment.status not in {
        PaymentStatus.refunded,
        PaymentStatus.partially_refunded,
        PaymentStatus.reversed,
    }:
        raise _error(
            "recovery_evidence_invalid",
            "Confirmed refund or reversal evidence is required.",
        )
    purchase = db.get(PrepaidPeriodPurchase, payment.reserved_for_purchase_id)
    if purchase is None:
        raise _error(
            "recovery_evidence_invalid", "Purchase payment ownership is incomplete."
        )
    other_held = db.scalar(
        select(Payment.id)
        .where(
            Payment.reserved_for_purchase_id == purchase.id,
            Payment.id != purchase.payment_id,
            Payment.status.in_(
                [PaymentStatus.succeeded, PaymentStatus.partially_refunded]
            ),
        )
        .limit(1)
    )
    if purchase.payment_id != payment.id:
        # A second genuine provider capture remains a separate receipt. Its
        # refund must never cancel the periods funded by the original receipt.
        if other_held is None and purchase.completed_at is not None:
            primary = (
                db.get(Payment, purchase.payment_id) if purchase.payment_id else None
            )
            if primary is not None and primary.status is PaymentStatus.succeeded:
                purchase.status = PrepaidPeriodPurchaseStatus.completed
                purchase.failure_code = None
                db.flush()
                return
        purchase.status = PrepaidPeriodPurchaseStatus.review_required
        purchase.failure_code = f"{_OWNER}.additional_capture_review"
        db.flush()
        return
    if payment.status in {PaymentStatus.refunded, PaymentStatus.reversed}:
        purchase.status = (
            PrepaidPeriodPurchaseStatus.review_required
            if other_held is not None
            else PrepaidPeriodPurchaseStatus.canceled
        )
        purchase.failure_code = f"{_OWNER}.payment_reversed"
    else:
        purchase.status = PrepaidPeriodPurchaseStatus.review_required
        purchase.failure_code = f"{_OWNER}.payment_partially_refunded"
    db.flush()
