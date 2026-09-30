"""Enforcement application evidence: the ONE writer (ADR 0017).

``access.enforcement_evidence`` owns the ``EnforcementApplication`` observation:
one current-state row per (subscription, NAS device, effect) recording what an
enforcement attempt actually did on a device. ``access.session_enforcement``
(``app/services/enforcement.py``) performs the attempts and hands each final
per-NAS ``EnforcementOutcome`` here.

Transaction mode ``out_of_band_evidence``: the write runs on its own
``db_session_adapter.create_session()`` unit of work so the evidence of an
irreversible device effect survives the caller's rollback. It never joins the
caller's transaction, emits no domain event, and never raises into the caller
(except a Celery soft time limit, which must reach the task).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, cast
from uuid import UUID, uuid4

from sqlalchemy import and_, case, func, or_, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from app.logging import sanitize_exception
from app.models.catalog import NasDevice, Subscription, SubscriptionStatus
from app.models.enforcement_application import (
    ENFORCEMENT_APPLICATION_DETAIL_MAX_LENGTH,
    EnforcementApplication,
    EnforcementEffect,
    EnforcementFailureClass,
    EnforcementOutcomeValue,
    EnforcementPath,
)
from app.services.db_session_adapter import db_session_adapter
from app.services.nas.enforcement_failure import classify_enforcement_failure

if TYPE_CHECKING:
    from sqlalchemy import Table

logger = logging.getLogger(__name__)

__all__ = [
    "EffectOutcomeTotal",
    "EnforcementEvidenceShadowReport",
    "EnforcementOutcome",
    "FailedEnforcementRow",
    "FailureClassTotal",
    "NasEnforcementSummary",
    "UncoveredBlockedSubscription",
    "enforcement_evidence_shadow_report",
    "record_enforcement_application",
]


@dataclass(frozen=True)
class EnforcementOutcome:
    """Typed result of one enforcement attempt on one NAS (ADR-0017 §4).

    Replaces the historical bare ``bool`` return from the per-NAS enforcement
    helpers, which conflated "not applicable" (not MikroTik, no credentials,
    feature disabled) with a real transport failure. This is a value object
    only: producing one never writes anything — see
    ``record_enforcement_application`` for the sole writer.
    """

    outcome: EnforcementOutcomeValue
    failure_class: EnforcementFailureClass | None
    path: EnforcementPath | None
    detail: str | None

    @classmethod
    def applied(cls, path: EnforcementPath) -> EnforcementOutcome:
        return cls(
            outcome=EnforcementOutcomeValue.applied,
            failure_class=None,
            path=path,
            detail=None,
        )

    @classmethod
    def not_applicable(cls, detail: str | None = None) -> EnforcementOutcome:
        return cls(
            outcome=EnforcementOutcomeValue.not_applicable,
            failure_class=None,
            path=None,
            detail=detail,
        )

    @classmethod
    def failed_from(
        cls, exc: BaseException, path: EnforcementPath
    ) -> EnforcementOutcome:
        failure_class, detail = classify_enforcement_failure(exc)
        return cls(
            outcome=EnforcementOutcomeValue.failed,
            failure_class=failure_class,
            path=path,
            detail=detail[:ENFORCEMENT_APPLICATION_DETAIL_MAX_LENGTH],
        )


def _is_task_time_limit(exc: BaseException) -> bool:
    try:
        from billiard.exceptions import SoftTimeLimitExceeded
    except ImportError:  # pragma: no cover - billiard ships with Celery
        return False
    return isinstance(exc, SoftTimeLimitExceeded)


def record_enforcement_application(
    *,
    subscription_id: UUID,
    nas_device_id: UUID,
    effect: EnforcementEffect,
    outcome: EnforcementOutcome,
) -> None:
    """Out-of-band writer for enforcement evidence (ADR-0017 §2, §3, §7).

    The sole writer of ``EnforcementApplication``. Opens its own short
    session via ``db_session_adapter.create_session()`` (never the caller's
    session — the caller may hold ``SELECT ... FOR UPDATE`` on the
    subscription row, and this upsert must survive that transaction's
    rollback), upserts the current-state row for
    ``(subscription_id, nas_device_id, effect)``, commits, and closes.

    Never raises: the device effect has already happened, so failing the
    caller here could re-trigger it. A write failure is logged at ``ERROR``
    (so it opens a GlitchTip issue) and swallowed.
    """
    session: Session | None = None
    try:
        # Inside the try: an unreachable database at session-open time must
        # be swallowed and logged like any other write failure (ADR-0017 §7).
        session = db_session_adapter.create_session()
        now = datetime.now(UTC)
        table = cast("Table", EnforcementApplication.__table__)
        dialect_name = session.bind.dialect.name if session.bind is not None else ""
        is_postgresql = dialect_name == "postgresql"
        if is_postgresql:
            session.execute(text("SET LOCAL lock_timeout = '2s'"))

        failure_class_value = (
            outcome.failure_class.value if outcome.failure_class is not None else None
        )
        path_value = outcome.path.value if outcome.path is not None else None

        values: dict[str, Any] = {
            "id": uuid4(),
            "subscription_id": subscription_id,
            "nas_device_id": nas_device_id,
            "effect": effect.value,
            "outcome": outcome.outcome.value,
            "failure_class": failure_class_value,
            "path": path_value,
            "detail": outcome.detail,
            "last_attempt_at": now,
            "created_at": now,
            "updated_at": now,
        }
        set_: dict[str, Any] = {
            "outcome": outcome.outcome.value,
            "failure_class": failure_class_value,
            "path": path_value,
            "detail": outcome.detail,
            "last_attempt_at": now,
            "updated_at": now,
        }

        if outcome.outcome == EnforcementOutcomeValue.failed:
            values["attempt_count"] = 1
            values["first_failed_at"] = now
            values["last_success_at"] = None
            set_["attempt_count"] = table.c.attempt_count + 1
            set_["first_failed_at"] = func.coalesce(table.c.first_failed_at, now)
            set_["last_success_at"] = table.c.last_success_at
        elif outcome.outcome == EnforcementOutcomeValue.applied:
            values["attempt_count"] = 0
            values["first_failed_at"] = None
            values["last_success_at"] = now
            set_["attempt_count"] = 0
            set_["first_failed_at"] = None
            set_["last_success_at"] = now
        else:  # not_applicable
            # A not-applicable attempt is a fresh state, not a continuation
            # of an earlier failure streak: clear the failure counters so the
            # row never reads "not_applicable" with a live failure history.
            values["attempt_count"] = 0
            values["first_failed_at"] = None
            values["last_success_at"] = None
            set_["attempt_count"] = 0
            set_["first_failed_at"] = None
            set_["last_success_at"] = table.c.last_success_at

        if is_postgresql:
            pg_stmt = pg_insert(table).values(**values)
            pg_stmt = pg_stmt.on_conflict_do_update(
                index_elements=["subscription_id", "nas_device_id", "effect"],
                set_=set_,
            )
            session.execute(pg_stmt)
        else:
            sqlite_stmt = sqlite_insert(table).values(**values)
            sqlite_stmt = sqlite_stmt.on_conflict_do_update(
                index_elements=["subscription_id", "nas_device_id", "effect"],
                set_=set_,
            )
            session.execute(sqlite_stmt)
        session.commit()
    except Exception as exc:
        if _is_task_time_limit(exc):
            # A Celery soft time limit must reach the task; swallowing it here
            # would let the task run past its budget (ADR-0017 section 7 covers
            # write failures, not task cancellation).
            raise
        logger.error(
            "enforcement_application_record_failed",
            extra={
                "event": "enforcement_application_record_failed",
                "subscription_id": str(subscription_id),
                "nas_device_id": str(nas_device_id),
                "effect": effect.value,
                "detail": sanitize_exception(exc),
            },
        )
        if session is not None:
            try:
                session.rollback()
            except Exception:
                pass
    finally:
        if session is not None:
            session.close()


#: Bound on the human-facing sections of the shadow report so an operator's
#: terminal (or a JSON payload) stays reviewable. Totals/per-NAS rollups are
#: full aggregates regardless of this bound; only the row-level lists below
#: are truncated by it.
_DEFAULT_SHADOW_REPORT_LIMIT = 500


@dataclass(frozen=True)
class EffectOutcomeTotal:
    """One (effect, outcome) rollup within the report window."""

    effect: str
    outcome: str
    count: int


@dataclass(frozen=True)
class FailureClassTotal:
    """One failure-class rollup within the report window."""

    failure_class: str
    count: int


def _as_utc(value: datetime | None) -> datetime | None:
    """Aggregates (``max``/``min``) can drop the column's timezone on some
    dialects; the columns hold UTC, so a naive result is UTC."""
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=UTC)


@dataclass(frozen=True)
class NasEnforcementSummary:
    """Per-NAS evidence rollup within the report window.

    ``nas_name`` is ``None`` when ``nas_device_id`` no longer resolves to a
    row in ``nas_devices`` — ``EnforcementApplication`` carries no foreign
    key (ADR-0017 §3), so a dangling id must be tolerated, not treated as an
    error.
    """

    nas_device_id: UUID
    nas_name: str | None
    failed_count: int
    applied_count: int
    last_success_at: datetime | None
    oldest_first_failed_at: datetime | None


@dataclass(frozen=True)
class FailedEnforcementRow:
    """One currently-failed evidence row, juxtaposed with the subscription's
    CURRENT status.

    ``subscription_status`` is read fresh at report time — it is never the
    status implied by the evidence row, and it is ``None`` when
    ``subscription_id`` no longer resolves to a row in ``subscriptions``
    (again tolerated, not an error, per ADR-0017 §3).
    """

    subscription_id: UUID
    nas_device_id: UUID
    effect: str
    failure_class: str | None
    detail: str | None
    attempt_count: int
    first_failed_at: datetime | None
    last_attempt_at: datetime
    subscription_status: str | None


#: Statuses whose intended access is blocked (the enforcement handler blocks on
#: suspended, disabled and expired events; blocked is the explicit state).
_BLOCKED_INTENT_STATUSES = (
    SubscriptionStatus.paused,
    SubscriptionStatus.suspended,
    SubscriptionStatus.blocked,
    SubscriptionStatus.disabled,
    SubscriptionStatus.expired,
)


@dataclass(frozen=True)
class UncoveredBlockedSubscription:
    """A blocked-intent subscription with no address-list-block evidence."""

    subscription_id: UUID
    subscription_status: str
    provisioning_nas_device_id: UUID


@dataclass(frozen=True)
class EnforcementEvidenceShadowReport:
    """The ADR-0017 slice-2 cutover gate's read-only shadow comparison.

    Juxtaposes recorded ``EnforcementApplication`` evidence against router
    logs (an operator cross-checks those separately, see the runbook) and
    against each subscription's current status. It never treats the record
    as intended state — ``mismatch_candidates`` names a *candidate* for human
    review, not a resolved discrepancy.
    """

    since: datetime
    generated_at: datetime
    limit: int
    totals_by_effect_outcome: tuple[EffectOutcomeTotal, ...]
    totals_by_failure_class: tuple[FailureClassTotal, ...]
    per_nas: tuple[NasEnforcementSummary, ...]
    currently_failed: tuple[FailedEnforcementRow, ...]
    mismatch_candidates: tuple[FailedEnforcementRow, ...]
    uncovered_blocked_subscriptions: tuple[UncoveredBlockedSubscription, ...]


def _failed_row(row: Any) -> FailedEnforcementRow:
    status = row.subscription_status
    return FailedEnforcementRow(
        subscription_id=row.subscription_id,
        nas_device_id=row.nas_device_id,
        effect=row.effect,
        failure_class=row.failure_class,
        detail=row.detail,
        attempt_count=row.attempt_count,
        first_failed_at=row.first_failed_at,
        last_attempt_at=row.last_attempt_at,
        subscription_status=status.value if status is not None else None,
    )


def enforcement_evidence_shadow_report(
    db: Session,
    *,
    since: datetime,
    limit: int = _DEFAULT_SHADOW_REPORT_LIMIT,
) -> EnforcementEvidenceShadowReport:
    """Read-only shadow comparison over ``EnforcementApplication`` (ADR-0017).

    Owned by ``access.enforcement_evidence`` because it is the record's
    owner. Issues only ``SELECT`` statements against ``db`` — no flush, no
    commit, no write of any kind — and is the tool an operator runs after the
    ADR-0017 slice deploys, before slice 2 (a resolver reading this evidence)
    is allowed to proceed (see
    ``docs/runbooks/ENFORCEMENT_EVIDENCE_SHADOW_COMPARISON.md``).

    This never treats the evidence row as intended state: ``mismatch_candidates``
    only juxtaposes a failed enforcement attempt against the subscription's
    CURRENT status for a human to review, exactly as ADR-0017's invariants
    require of every reader of this record.
    """
    app_tbl = EnforcementApplication
    window = app_tbl.last_attempt_at >= since

    # Labelled "total", never "count": `Row` already exposes a tuple
    # `.count()` method, and a `.label("count")` column silently shadows it
    # with an int, which mypy (correctly) rejects as a type conflict.
    totals_stmt = (
        select(app_tbl.effect, app_tbl.outcome, func.count().label("total"))
        .where(window)
        .group_by(app_tbl.effect, app_tbl.outcome)
    )
    totals_by_effect_outcome = tuple(
        EffectOutcomeTotal(effect=row.effect, outcome=row.outcome, count=row.total)
        for row in db.execute(totals_stmt)
    )

    failure_totals_stmt = (
        select(app_tbl.failure_class, func.count().label("total"))
        .where(window, app_tbl.failure_class.isnot(None))
        .group_by(app_tbl.failure_class)
    )
    totals_by_failure_class = tuple(
        FailureClassTotal(failure_class=row.failure_class, count=row.total)
        for row in db.execute(failure_totals_stmt)
    )

    per_nas_stmt = (
        select(
            app_tbl.nas_device_id,
            NasDevice.name.label("nas_name"),
            func.sum(
                case(
                    (app_tbl.outcome == EnforcementOutcomeValue.failed.value, 1),
                    else_=0,
                )
            ).label("failed_count"),
            func.sum(
                case(
                    (app_tbl.outcome == EnforcementOutcomeValue.applied.value, 1),
                    else_=0,
                )
            ).label("applied_count"),
            func.max(app_tbl.last_success_at).label("last_success_at"),
            func.min(app_tbl.first_failed_at).label("oldest_first_failed_at"),
        )
        .select_from(app_tbl)
        .outerjoin(NasDevice, NasDevice.id == app_tbl.nas_device_id)
        .where(window)
        .group_by(app_tbl.nas_device_id, NasDevice.name)
    )
    per_nas = tuple(
        NasEnforcementSummary(
            nas_device_id=row.nas_device_id,
            nas_name=row.nas_name,
            failed_count=int(row.failed_count or 0),
            applied_count=int(row.applied_count or 0),
            last_success_at=_as_utc(row.last_success_at),
            oldest_first_failed_at=_as_utc(row.oldest_first_failed_at),
        )
        for row in db.execute(per_nas_stmt)
    )

    failed_base = (
        select(
            app_tbl.subscription_id,
            app_tbl.nas_device_id,
            app_tbl.effect,
            app_tbl.failure_class,
            app_tbl.detail,
            app_tbl.attempt_count,
            app_tbl.first_failed_at,
            app_tbl.last_attempt_at,
            Subscription.status.label("subscription_status"),
        )
        .select_from(app_tbl)
        .outerjoin(Subscription, Subscription.id == app_tbl.subscription_id)
        # NOT windowed: the table is current state and slice 1 has no retrying
        # reconciler, so a failure recorded before `since` and never retried
        # is still the live state (e.g. a customer still blocked on the NAS).
        .where(app_tbl.outcome == EnforcementOutcomeValue.failed.value)
    )

    currently_failed_stmt = failed_base.order_by(app_tbl.first_failed_at.asc()).limit(
        limit
    )
    currently_failed = tuple(
        _failed_row(row) for row in db.execute(currently_failed_stmt)
    )

    # Mismatch candidates (ADR-0017 §8 cutover gate): a failed unblock while
    # the subscription now reads active (still blocked?), or a failed block
    # while the subscription now reads a blocked-intent status (never
    # blocked?).
    # This is evidence juxtaposed with current status, never a resolved
    # discrepancy or a claim about intended state.
    mismatch_stmt = (
        failed_base.where(
            or_(
                and_(
                    app_tbl.effect == EnforcementEffect.address_list_unblock.value,
                    Subscription.status == SubscriptionStatus.active,
                ),
                and_(
                    app_tbl.effect == EnforcementEffect.address_list_block.value,
                    Subscription.status.in_(_BLOCKED_INTENT_STATUSES),
                ),
            )
        )
        .order_by(app_tbl.first_failed_at.asc())
        .limit(limit)
    )
    mismatch_candidates = tuple(_failed_row(row) for row in db.execute(mismatch_stmt))

    # Coverage: a blocked-intent subscription with a served IPv4 and a
    # provisioning NAS but NO address-list-block evidence row at all. That is
    # what a silently failing (or never-reached) evidence writer looks like.
    # Not windowed: absence has no timestamp.
    has_block_evidence = (
        select(app_tbl.id)
        .where(
            app_tbl.subscription_id == Subscription.id,
            app_tbl.effect == EnforcementEffect.address_list_block.value,
        )
        .exists()
    )
    uncovered_stmt = (
        select(
            Subscription.id,
            Subscription.status,
            Subscription.provisioning_nas_device_id,
        )
        .where(
            Subscription.status.in_(_BLOCKED_INTENT_STATUSES),
            Subscription.ipv4_address.isnot(None),
            Subscription.provisioning_nas_device_id.isnot(None),
            ~has_block_evidence,
        )
        .order_by(Subscription.id)
        .limit(limit)
    )
    uncovered_blocked_subscriptions = tuple(
        UncoveredBlockedSubscription(
            subscription_id=row.id,
            subscription_status=row.status.value,
            provisioning_nas_device_id=row.provisioning_nas_device_id,
        )
        for row in db.execute(uncovered_stmt)
    )

    return EnforcementEvidenceShadowReport(
        since=since,
        generated_at=datetime.now(UTC),
        limit=limit,
        totals_by_effect_outcome=totals_by_effect_outcome,
        totals_by_failure_class=totals_by_failure_class,
        per_nas=per_nas,
        currently_failed=currently_failed,
        mismatch_candidates=mismatch_candidates,
        uncovered_blocked_subscriptions=uncovered_blocked_subscriptions,
    )
