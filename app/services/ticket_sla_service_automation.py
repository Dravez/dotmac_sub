"""Typed SLA-breach automation consequence for one linked customer service."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.catalog import Subscription, SubscriptionStatus
from app.models.enforcement_lock import EnforcementLock, EnforcementReason
from app.models.support import Ticket
from app.services import account_lifecycle
from app.services.domain_errors import DomainError
from app.services.owner_commands import (
    CommandContext,
    OwnerCommandDefinition,
    execute_owner_command,
)

OWNER = "support.ticket_sla_service_consequence"
CONCERN = "ticket SLA-breach service suspension consequence"

_SUSPEND = OwnerCommandDefinition(
    owner=OWNER,
    concern=CONCERN,
    name="suspend_unique_active_service_for_ticket_sla_breach",
)


class TicketSlaServiceAutomationError(DomainError):
    """Fail-closed rejection from the SLA-breach service consequence owner."""


@dataclass(frozen=True, slots=True)
class SuspendTicketServiceForSlaBreachCommand:
    ticket_id: UUID
    event_id: UUID
    rule_id: UUID
    rule_version_id: UUID
    step_index: int
    context: CommandContext


@dataclass(frozen=True, slots=True)
class SuspendTicketServiceForSlaBreachOutcome:
    ticket_id: UUID
    subscription_id: UUID
    enforcement_lock_id: UUID
    replayed: bool


def _error(
    suffix: str, message: str, **details: object
) -> TicketSlaServiceAutomationError:
    return TicketSlaServiceAutomationError(
        code=f"{OWNER}.{suffix}",
        message=message,
        details=details,
        retryable=False,
    )


def _source(command: SuspendTicketServiceForSlaBreachCommand) -> str:
    return (
        f"automation:ticket-sla:{command.event_id}:"
        f"{command.rule_version_id}:{command.step_index}"
    )[:255]


def suspend_unique_active_service_for_ticket_sla_breach(
    db: Session,
    command: SuspendTicketServiceForSlaBreachCommand,
) -> SuspendTicketServiceForSlaBreachOutcome:
    """Suspend the only active service linked to the breached Ticket.

    The ticket's canonical account link is ``customer_account_id`` with the
    compatibility ``subscriber_id`` fallback. Zero or multiple active services
    are ambiguous and fail closed. A retry first resolves the exact automation
    lock source, so a crash after suspension but before step completion cannot
    turn a successful action into a false no-service failure.
    """

    def operation() -> SuspendTicketServiceForSlaBreachOutcome:
        ticket = db.scalar(
            select(Ticket).where(Ticket.id == command.ticket_id).with_for_update()
        )
        if ticket is None:
            raise _error(
                "ticket_not_found",
                "The SLA-breached ticket no longer exists.",
                ticket_id=str(command.ticket_id),
            )
        account_id = ticket.customer_account_id or ticket.subscriber_id
        if account_id is None:
            raise _error(
                "customer_account_missing",
                "The SLA-breached ticket is not linked to a customer account.",
                ticket_id=str(ticket.id),
            )

        source = _source(command)
        prior_locks = tuple(
            db.scalars(
                select(EnforcementLock)
                .join(
                    Subscription,
                    Subscription.id == EnforcementLock.subscription_id,
                )
                .where(
                    Subscription.subscriber_id == account_id,
                    EnforcementLock.reason == EnforcementReason.ticket_sla,
                    EnforcementLock.source == source,
                    EnforcementLock.is_active.is_(True),
                )
                .with_for_update()
            ).all()
        )
        if len(prior_locks) == 1:
            prior = prior_locks[0]
            return SuspendTicketServiceForSlaBreachOutcome(
                ticket_id=ticket.id,
                subscription_id=prior.subscription_id,
                enforcement_lock_id=prior.id,
                replayed=True,
            )
        if len(prior_locks) > 1:
            raise _error(
                "idempotency_conflict",
                "More than one active lock matches this automation action.",
                ticket_id=str(ticket.id),
                event_id=str(command.event_id),
            )

        active_services = tuple(
            db.scalars(
                select(Subscription)
                .where(
                    Subscription.subscriber_id == account_id,
                    Subscription.status == SubscriptionStatus.active,
                )
                .order_by(Subscription.id.asc())
                .with_for_update()
            ).all()
        )
        if not active_services:
            raise _error(
                "active_service_not_found",
                "The linked customer has no active service to suspend.",
                ticket_id=str(ticket.id),
                customer_account_id=str(account_id),
            )
        if len(active_services) != 1:
            raise _error(
                "active_service_ambiguous",
                "The linked customer has multiple active services; no service was suspended.",
                ticket_id=str(ticket.id),
                customer_account_id=str(account_id),
                active_service_count=len(active_services),
            )

        subscription = active_services[0]
        lock = account_lifecycle.suspend_subscription(
            db,
            str(subscription.id),
            reason=EnforcementReason.ticket_sla,
            source=source,
            notes=(
                f"Suspended by Automation Center after SLA breach on ticket "
                f"{ticket.id}; rule {command.rule_id}."
            ),
            evidence_context=command.context,
        )
        return SuspendTicketServiceForSlaBreachOutcome(
            ticket_id=ticket.id,
            subscription_id=subscription.id,
            enforcement_lock_id=lock.id,
            replayed=False,
        )

    return execute_owner_command(
        db,
        definition=_SUSPEND,
        context=command.context,
        operation=operation,
    )


__all__ = [
    "SuspendTicketServiceForSlaBreachCommand",
    "SuspendTicketServiceForSlaBreachOutcome",
    "TicketSlaServiceAutomationError",
    "suspend_unique_active_service_for_ticket_sla_breach",
]
