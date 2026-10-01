"""Explicit activation of Studio payment content and proved-pair composition.

The one persisted activation row gates both the settlement producer and email
handler. Merely installing the distribution or adopting content enables neither.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from dotmac_template_studio import service as studio
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.models.notification import NotificationChannel, NotificationTemplate
from app.models.payment_email import PaymentEmailCutover
from app.services.domain_errors import DomainError
from app.services.events import emit_event
from app.services.events.types import EventType
from app.services.operator_tenant import operator_tenant_id
from app.services.owner_commands import (
    CommandContext,
    OwnerCommandDefinition,
    execute_owner_command,
)
from app.services.payment_template_adoption import (
    PAYMENT_TEMPLATES,
    ParityStatus,
    ReviewedPaymentEmailTemplates,
    payment_email_parity_report,
    require_rls_runtime_role,
)

_COMMAND = OwnerCommandDefinition(
    owner="communications.payment_email_cutover",
    concern="reviewed payment email authority cutover",
    name="activate_payment_email_cutover",
)


def active_cutover(db: Session) -> PaymentEmailCutover | None:
    return db.get(PaymentEmailCutover, operator_tenant_id())


def composition_enabled(db: Session) -> bool:
    # Producers/dispatchers retain a shared row lock until their source work
    # commits. Pause takes FOR UPDATE, so its completed response is the fence
    # after which no transaction can enter new collection under the old gate.
    cutover = db.scalar(
        select(PaymentEmailCutover)
        .where(PaymentEmailCutover.tenant_id == operator_tenant_id())
        .with_for_update(read=True, key_share=True)
        .execution_options(populate_existing=True)
    )
    return cutover is not None and cutover.composition_enabled


def pause_payment_email_composition(db: Session, *, context: CommandContext) -> None:
    """Stop new pair collection and its producer while retaining Studio authority.

    Already queued episodes finish from their immutable parts and coverage.
    This is deliberately a one-way rollback command; resumption requires a new
    reviewed activation contract rather than replaying the original activation.
    """
    tenant_id = operator_tenant_id()
    if context.scope != str(tenant_id):
        raise DomainError(
            code="payment_email_cutover.invalid_scope",
            message="Operator tenant scope does not match",
            retryable=False,
        )

    def operation() -> None:
        require_rls_runtime_role(db)
        cutover = db.scalar(
            select(PaymentEmailCutover)
            .where(PaymentEmailCutover.tenant_id == tenant_id)
            .with_for_update()
        )
        if cutover is None:
            raise DomainError(
                code="payment_email_cutover.not_active",
                message="Payment email authority has not been activated",
                retryable=False,
            )
        if not cutover.composition_enabled:
            return
        cutover.composition_enabled = False
        db.flush()
        emit_event(
            db,
            EventType.payment_email_composition_paused,
            {"schema_version": 1, "tenant_id": str(tenant_id)},
            actor=context.actor,
            defer_until_commit=True,
            dispatch_after_commit=False,
            record_only=True,
        )

    execute_owner_command(
        db,
        definition=OwnerCommandDefinition(
            owner=_COMMAND.owner,
            concern=_COMMAND.concern,
            name="pause_payment_email_composition",
        ),
        context=context,
        operation=operation,
    )


def activate_payment_email_cutover(
    db: Session,
    *,
    context: CommandContext,
    reviewed: ReviewedPaymentEmailTemplates,
) -> UUID:
    """Switch only after current locked content proves exact adoption parity."""
    tenant_id = operator_tenant_id()
    if context.scope != str(tenant_id):
        raise DomainError(
            code="payment_email_cutover.invalid_scope",
            message="Operator tenant scope does not match",
            retryable=False,
        )

    def operation() -> UUID:
        require_rls_runtime_role(db)
        if db.get_bind().dialect.name == "postgresql":
            # Refuse a concurrent alias insert/rebind during parity and sealing.
            db.execute(text("SET LOCAL lock_timeout = '5s'"))
            db.execute(
                text(
                    "LOCK TABLE public.notification_templates IN SHARE ROW EXCLUSIVE MODE"
                )
            )
        # Lock source identities in a deterministic order while proving parity.
        ids = (reviewed.payment_received_legacy_id, reviewed.invoice_paid_legacy_id)
        rows = db.scalars(
            select(NotificationTemplate)
            .where(NotificationTemplate.id.in_(ids))
            .order_by(NotificationTemplate.id)
            .with_for_update()
        ).all()
        if len(rows) != 2 or any(
            row.channel is not NotificationChannel.email for row in rows
        ):
            raise DomainError(
                code="payment_email_cutover.invalid_identity",
                message="Both reviewed legacy email identities are required",
                retryable=False,
            )
        content = tuple(
            studio.get_by_slug(db, tenant_id, item.slug, "email")
            for item in PAYMENT_TEMPLATES
        )
        for template in sorted(content, key=lambda item: str(item.id)):
            db.refresh(template, with_for_update=True)
        existing = active_cutover(db)
        if existing is not None:
            if (
                existing.receipt_legacy_id,
                existing.invoice_legacy_id,
                existing.receipt_content_id,
                existing.invoice_content_id,
            ) != (*ids, content[0].id, content[1].id):
                raise DomainError(
                    code="payment_email_cutover.identity_changed",
                    message="Activated payment email identity changed",
                    retryable=False,
                )
            return existing.tenant_id
        report = payment_email_parity_report(db)
        if (
            any(item.status is not ParityStatus.match for item in report.items)
            or tuple(item.legacy_template_id for item in report.items) != ids
        ):
            raise DomainError(
                code="payment_email_cutover.parity_failed",
                message="Current payment email adoption parity is required",
                retryable=False,
            )
        db.add(
            PaymentEmailCutover(
                tenant_id=tenant_id,
                receipt_legacy_id=ids[0],
                invoice_legacy_id=ids[1],
                receipt_content_id=content[0].id,
                invoice_content_id=content[1].id,
                activated_at=datetime.now(UTC),
                activated_by=context.actor,
            )
        )
        for row in rows:
            row.studio_content_sealed = True
        db.flush()
        emit_event(
            db,
            EventType.payment_email_cutover_activated,
            {
                "schema_version": 1,
                "receipt_legacy_id": str(ids[0]),
                "invoice_legacy_id": str(ids[1]),
                "receipt_content_id": str(content[0].id),
                "invoice_content_id": str(content[1].id),
            },
            actor=context.actor,
            defer_until_commit=True,
            dispatch_after_commit=False,
            record_only=True,
        )
        return tenant_id

    return execute_owner_command(
        db, definition=_COMMAND, context=context, operation=operation
    )


_PAYMENT_CODES = frozenset(
    {"payment_received", "payment_received_email", "invoice_paid", "invoice_paid_email"}
)


def legacy_content_is_sealed(
    db: Session, code: str, channel: NotificationChannel | str
) -> bool:
    value = channel.value if isinstance(channel, NotificationChannel) else channel
    if value != "email" or code not in _PAYMENT_CODES:
        return False
    # Legacy templates are global in this product; their immutable seal must
    # not disappear when a different tenant context hides the cutover row.
    return (
        db.scalar(
            select(NotificationTemplate.id)
            .where(NotificationTemplate.studio_content_sealed.is_(True))
            .limit(1)
        )
        is not None
    )
