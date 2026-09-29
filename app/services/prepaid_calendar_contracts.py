"""Typed calendar evidence owned by financial.prepaid_service_renewals.

These are contracts only. Adapters cannot choose a clock time; the renewal
owner resolves reviewed dates from either business midnight or an exact
documentary anniversary observed by the reconciliation owner.
"""

from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum


class ReviewedPrepaidCalendarBasis(StrEnum):
    business_midnight = "business_midnight"
    documented_anniversary = "documented_anniversary"


@dataclass(frozen=True, slots=True)
class DocumentedPrepaidServicePeriod:
    starts_at: datetime
    ends_at: datetime


@dataclass(frozen=True, slots=True)
class ReviewedPrepaidCalendarSelection:
    basis: ReviewedPrepaidCalendarBasis = ReviewedPrepaidCalendarBasis.business_midnight
    expected_initial_anchor_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class ReviewedPrepaidServicePeriodQuery:
    starts_on: date
    ends_on: date
    basis: ReviewedPrepaidCalendarBasis = ReviewedPrepaidCalendarBasis.business_midnight
    documented_period: DocumentedPrepaidServicePeriod | None = None
