"""``enforcement_evidence_shadow_report`` (ADR-0017 slice-2 cutover gate).

Exercises the read-only query against a real SQLite database carrying only
the three tables it touches. Mirrors ``tests/test_enforcement_application_writer.py``'s
private per-test engine/sessionmaker pattern rather than the shared
``tests/conftest.py`` ``engine``/``db_session`` fixtures: that shared engine is
session-scoped and its rows persist across the whole suite, which would leak
``EnforcementApplication`` rows (no foreign keys, ADR-0017 §3) between the many
narrow scenarios below.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.catalog import NasDevice, Subscription, SubscriptionStatus
from app.models.enforcement_application import (
    EnforcementApplication,
    EnforcementEffect,
    EnforcementFailureClass,
    EnforcementOutcomeValue,
    EnforcementPath,
)
from app.services.enforcement_evidence import enforcement_evidence_shadow_report

NOW = datetime(2026, 9, 26, 12, 0, tzinfo=UTC)
SINCE = NOW - timedelta(days=7)
BEFORE_WINDOW = SINCE - timedelta(days=1)


@pytest.fixture()
def report_engine():
    """A private, per-test SQLite engine carrying only the three tables the
    report reads — see module docstring for why this is not the shared
    ``tests/conftest.py`` engine fixture."""
    engine = create_engine(
        "sqlite+pysqlite://", connect_args={"check_same_thread": False}
    )
    EnforcementApplication.__table__.create(bind=engine)
    NasDevice.__table__.create(bind=engine)
    Subscription.__table__.create(bind=engine)
    yield engine
    engine.dispose()


@pytest.fixture()
def report_sessionmaker(report_engine):
    return sessionmaker(bind=report_engine)


def _add_evidence(
    session,
    *,
    subscription_id,
    nas_device_id,
    effect: EnforcementEffect,
    outcome: EnforcementOutcomeValue,
    failure_class: EnforcementFailureClass | None = None,
    attempt_count: int = 0,
    first_failed_at: datetime | None = None,
    last_attempt_at: datetime = NOW,
    last_success_at: datetime | None = None,
    detail: str | None = None,
) -> None:
    session.add(
        EnforcementApplication(
            subscription_id=subscription_id,
            nas_device_id=nas_device_id,
            effect=effect.value,
            outcome=outcome.value,
            failure_class=failure_class.value if failure_class else None,
            path=EnforcementPath.ssh.value,
            detail=detail,
            attempt_count=attempt_count,
            first_failed_at=first_failed_at,
            last_attempt_at=last_attempt_at,
            last_success_at=last_success_at,
        )
    )


def _add_nas(session, *, nas_device_id, name: str) -> None:
    session.add(NasDevice(id=nas_device_id, name=name))


def _add_subscription(
    session,
    *,
    subscription_id,
    status: SubscriptionStatus,
    ipv4_address: str | None = None,
    provisioning_nas_device_id=None,
) -> None:
    session.add(
        Subscription(
            id=subscription_id,
            subscriber_id=uuid4(),
            offer_id=uuid4(),
            status=status,
            ipv4_address=ipv4_address,
            provisioning_nas_device_id=provisioning_nas_device_id,
        )
    )


class TestTotals:
    def test_totals_by_effect_outcome_and_failure_class_within_window(
        self, report_sessionmaker
    ):
        nas_id = uuid4()
        with report_sessionmaker() as session:
            _add_evidence(
                session,
                subscription_id=uuid4(),
                nas_device_id=nas_id,
                effect=EnforcementEffect.address_list_block,
                outcome=EnforcementOutcomeValue.failed,
                failure_class=EnforcementFailureClass.auth_rejected,
                attempt_count=1,
                first_failed_at=NOW,
            )
            _add_evidence(
                session,
                subscription_id=uuid4(),
                nas_device_id=nas_id,
                effect=EnforcementEffect.address_list_block,
                outcome=EnforcementOutcomeValue.failed,
                failure_class=EnforcementFailureClass.auth_rejected,
                attempt_count=1,
                first_failed_at=NOW,
            )
            _add_evidence(
                session,
                subscription_id=uuid4(),
                nas_device_id=nas_id,
                effect=EnforcementEffect.session_kick,
                outcome=EnforcementOutcomeValue.applied,
                last_success_at=NOW,
            )
            # Outside the window: must not be counted.
            _add_evidence(
                session,
                subscription_id=uuid4(),
                nas_device_id=nas_id,
                effect=EnforcementEffect.address_list_block,
                outcome=EnforcementOutcomeValue.failed,
                failure_class=EnforcementFailureClass.timeout,
                attempt_count=5,
                first_failed_at=BEFORE_WINDOW,
                last_attempt_at=BEFORE_WINDOW,
            )
            session.commit()

            report = enforcement_evidence_shadow_report(session, since=SINCE)

        effect_outcome = {
            (row.effect, row.outcome): row.count
            for row in report.totals_by_effect_outcome
        }
        assert effect_outcome == {
            ("address_list_block", "failed"): 2,
            ("session_kick", "applied"): 1,
        }

        failure_class_totals = {
            row.failure_class: row.count for row in report.totals_by_failure_class
        }
        assert failure_class_totals == {"auth_rejected": 2}


class TestPerNas:
    def test_per_nas_aggregation_tolerates_a_dangling_nas_id(self, report_sessionmaker):
        known_nas = uuid4()
        dangling_nas = uuid4()  # never inserted into nas_devices
        with report_sessionmaker() as session:
            _add_nas(session, nas_device_id=known_nas, name="Eagle FM Access")
            _add_evidence(
                session,
                subscription_id=uuid4(),
                nas_device_id=known_nas,
                effect=EnforcementEffect.address_list_block,
                outcome=EnforcementOutcomeValue.failed,
                failure_class=EnforcementFailureClass.unreachable,
                attempt_count=3,
                first_failed_at=NOW - timedelta(days=2),
            )
            _add_evidence(
                session,
                subscription_id=uuid4(),
                nas_device_id=known_nas,
                effect=EnforcementEffect.session_kick,
                outcome=EnforcementOutcomeValue.applied,
                last_success_at=NOW,
            )
            _add_evidence(
                session,
                subscription_id=uuid4(),
                nas_device_id=dangling_nas,
                effect=EnforcementEffect.address_list_block,
                outcome=EnforcementOutcomeValue.failed,
                failure_class=EnforcementFailureClass.command_failed,
                attempt_count=1,
                first_failed_at=NOW,
            )
            session.commit()

            report = enforcement_evidence_shadow_report(session, since=SINCE)

        by_nas = {row.nas_device_id: row for row in report.per_nas}

        known_row = by_nas[known_nas]
        assert known_row.nas_name == "Eagle FM Access"
        assert known_row.failed_count == 1
        assert known_row.applied_count == 1
        assert known_row.last_success_at == NOW
        assert known_row.oldest_first_failed_at == NOW - timedelta(days=2)

        dangling_row = by_nas[dangling_nas]
        assert dangling_row.nas_name is None
        assert dangling_row.failed_count == 1
        assert dangling_row.applied_count == 0


class TestCurrentlyFailed:
    def test_currently_failed_rows_join_current_subscription_status_and_tolerate_a_dangling_subscription_id(
        self, report_sessionmaker
    ):
        known_sub = uuid4()
        dangling_sub = uuid4()  # never inserted into subscriptions
        nas_id = uuid4()
        with report_sessionmaker() as session:
            _add_subscription(
                session, subscription_id=known_sub, status=SubscriptionStatus.blocked
            )
            _add_evidence(
                session,
                subscription_id=known_sub,
                nas_device_id=nas_id,
                effect=EnforcementEffect.address_list_block,
                outcome=EnforcementOutcomeValue.failed,
                failure_class=EnforcementFailureClass.auth_rejected,
                attempt_count=1,
                first_failed_at=NOW,
                detail="auth rejected",
            )
            _add_evidence(
                session,
                subscription_id=dangling_sub,
                nas_device_id=nas_id,
                effect=EnforcementEffect.address_list_unblock,
                outcome=EnforcementOutcomeValue.failed,
                failure_class=EnforcementFailureClass.timeout,
                attempt_count=2,
                first_failed_at=NOW,
            )
            session.commit()

            report = enforcement_evidence_shadow_report(session, since=SINCE)

        by_sub = {row.subscription_id: row for row in report.currently_failed}
        assert by_sub[known_sub].subscription_status == "blocked"
        assert by_sub[known_sub].detail == "auth rejected"
        assert by_sub[dangling_sub].subscription_status is None


class TestMismatchCandidates:
    def test_failed_unblock_while_subscription_now_active_is_a_mismatch_candidate(
        self, report_sessionmaker
    ):
        active_sub = uuid4()
        suspended_sub = uuid4()  # near-miss: failed unblock but not active
        nas_id = uuid4()
        with report_sessionmaker() as session:
            _add_subscription(
                session, subscription_id=active_sub, status=SubscriptionStatus.active
            )
            _add_subscription(
                session,
                subscription_id=suspended_sub,
                status=SubscriptionStatus.suspended,
            )
            _add_evidence(
                session,
                subscription_id=active_sub,
                nas_device_id=nas_id,
                effect=EnforcementEffect.address_list_unblock,
                outcome=EnforcementOutcomeValue.failed,
                failure_class=EnforcementFailureClass.command_failed,
                attempt_count=1,
                first_failed_at=NOW,
            )
            _add_evidence(
                session,
                subscription_id=suspended_sub,
                nas_device_id=nas_id,
                effect=EnforcementEffect.address_list_unblock,
                outcome=EnforcementOutcomeValue.failed,
                failure_class=EnforcementFailureClass.command_failed,
                attempt_count=1,
                first_failed_at=NOW,
            )
            session.commit()

            report = enforcement_evidence_shadow_report(session, since=SINCE)

        candidate_subs = {row.subscription_id for row in report.mismatch_candidates}
        assert active_sub in candidate_subs
        assert suspended_sub not in candidate_subs

    def test_failed_block_while_subscription_now_suspended_or_blocked_is_a_mismatch_candidate(
        self, report_sessionmaker
    ):
        suspended_sub = uuid4()
        blocked_sub = uuid4()
        active_sub = uuid4()  # near-miss: failed block but subscription active
        nas_id = uuid4()
        with report_sessionmaker() as session:
            _add_subscription(
                session,
                subscription_id=suspended_sub,
                status=SubscriptionStatus.suspended,
            )
            _add_subscription(
                session, subscription_id=blocked_sub, status=SubscriptionStatus.blocked
            )
            _add_subscription(
                session, subscription_id=active_sub, status=SubscriptionStatus.active
            )
            for sub_id in (suspended_sub, blocked_sub, active_sub):
                _add_evidence(
                    session,
                    subscription_id=sub_id,
                    nas_device_id=nas_id,
                    effect=EnforcementEffect.address_list_block,
                    outcome=EnforcementOutcomeValue.failed,
                    failure_class=EnforcementFailureClass.unreachable,
                    attempt_count=1,
                    first_failed_at=NOW,
                )
            session.commit()

            report = enforcement_evidence_shadow_report(session, since=SINCE)

        candidate_subs = {row.subscription_id for row in report.mismatch_candidates}
        assert suspended_sub in candidate_subs
        assert blocked_sub in candidate_subs
        assert active_sub not in candidate_subs


class TestCurrentStateIsNotWindowed:
    def test_a_never_retried_failure_before_the_window_is_still_reported(
        self, report_sessionmaker
    ):
        """The table is current state and slice 1 has no reconciler: a failed
        unblock recorded before `since` and never retried is still live."""
        sub_id, nas_id = uuid4(), uuid4()
        with report_sessionmaker() as session:
            _add_subscription(
                session, subscription_id=sub_id, status=SubscriptionStatus.active
            )
            _add_evidence(
                session,
                subscription_id=sub_id,
                nas_device_id=nas_id,
                effect=EnforcementEffect.address_list_unblock,
                outcome=EnforcementOutcomeValue.failed,
                failure_class=EnforcementFailureClass.auth_rejected,
                attempt_count=1,
                first_failed_at=BEFORE_WINDOW,
                last_attempt_at=BEFORE_WINDOW,
            )
            session.commit()
            report = enforcement_evidence_shadow_report(session, since=SINCE)

        # Sensitivity: the row really is outside the window.
        assert BEFORE_WINDOW < SINCE
        assert sub_id in {row.subscription_id for row in report.currently_failed}
        assert sub_id in {row.subscription_id for row in report.mismatch_candidates}


class TestBlockedIntentStatuses:
    def test_failed_block_on_a_disabled_subscription_is_a_mismatch_candidate(
        self, report_sessionmaker
    ):
        sub_id, nas_id = uuid4(), uuid4()
        with report_sessionmaker() as session:
            _add_subscription(
                session, subscription_id=sub_id, status=SubscriptionStatus.disabled
            )
            _add_evidence(
                session,
                subscription_id=sub_id,
                nas_device_id=nas_id,
                effect=EnforcementEffect.address_list_block,
                outcome=EnforcementOutcomeValue.failed,
                failure_class=EnforcementFailureClass.unreachable,
                attempt_count=1,
                first_failed_at=NOW,
            )
            session.commit()
            report = enforcement_evidence_shadow_report(session, since=SINCE)

        assert sub_id in {row.subscription_id for row in report.mismatch_candidates}


class TestCoverage:
    def test_blocked_subscription_with_no_block_evidence_is_uncovered(
        self, report_sessionmaker
    ):
        uncovered, covered, no_ip, active = uuid4(), uuid4(), uuid4(), uuid4()
        nas_id = uuid4()
        with report_sessionmaker() as session:
            _add_subscription(
                session,
                subscription_id=uncovered,
                status=SubscriptionStatus.suspended,
                ipv4_address="10.0.0.1",
                provisioning_nas_device_id=nas_id,
            )
            _add_subscription(
                session,
                subscription_id=covered,
                status=SubscriptionStatus.suspended,
                ipv4_address="10.0.0.2",
                provisioning_nas_device_id=nas_id,
            )
            _add_evidence(
                session,
                subscription_id=covered,
                nas_device_id=nas_id,
                effect=EnforcementEffect.address_list_block,
                outcome=EnforcementOutcomeValue.applied,
            )
            # Near-misses: no served IPv4 (no attempt expected), and active.
            _add_subscription(
                session,
                subscription_id=no_ip,
                status=SubscriptionStatus.suspended,
                provisioning_nas_device_id=nas_id,
            )
            _add_subscription(
                session,
                subscription_id=active,
                status=SubscriptionStatus.active,
                ipv4_address="10.0.0.3",
                provisioning_nas_device_id=nas_id,
            )
            session.commit()
            report = enforcement_evidence_shadow_report(session, since=SINCE)

        ids = {row.subscription_id for row in report.uncovered_blocked_subscriptions}
        assert ids == {uncovered}


class TestNoWrite:
    def test_performs_no_write(self, report_sessionmaker, monkeypatch):
        """Sensitivity: the query must issue only SELECTs. A commit call, or a
        pending new/dirty object left in the identity map, would mean the
        report mutated the session it was handed to read from."""
        nas_id = uuid4()
        with report_sessionmaker() as session:
            _add_evidence(
                session,
                subscription_id=uuid4(),
                nas_device_id=nas_id,
                effect=EnforcementEffect.address_list_block,
                outcome=EnforcementOutcomeValue.failed,
                failure_class=EnforcementFailureClass.auth_rejected,
                attempt_count=1,
                first_failed_at=NOW,
            )
            session.commit()

            commit_calls = 0
            original_commit = session.commit

            def _tracked_commit():
                nonlocal commit_calls
                commit_calls += 1
                return original_commit()

            monkeypatch.setattr(session, "commit", _tracked_commit)

            enforcement_evidence_shadow_report(session, since=SINCE)

            assert commit_calls == 0
            assert not session.new
            assert not session.dirty
