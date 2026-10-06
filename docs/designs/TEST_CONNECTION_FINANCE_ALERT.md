# Test Connection Finance review workflow

Status: implementation contract; activation requires deployment and explicit workflow publication.

## Authority and meaning

`financial.service_extensions` owns creation of temporary service requests.
New requests explicitly select `outage_compensation` or `test_connection`.
Historical rows retain NULL purpose; free-text reasons are never classifiers.
This classification does not change eligibility, maker/checker approval,
application, reversal, billing-anchor, or network-access behavior.

A customer is the canonical Subscriber account UUID selected by the creation
owner, consistent with Automation Center customer scope. All subscriptions of
that account share one count. Different accounts remain isolated. A request
targeting several explicitly selected accounts counts once for each account.
Test Connections cannot use mutable POP/NAS topology as historical membership.

The requirement counts records **created**, not services successfully enabled.
Therefore pending, applied, canceled, and reversed requests all count. A
cancellation or reversal cannot erase creation evidence. Outage compensation,
unclassified historical rows, future timestamps, and other accounts do not count.
An applied-only rule would require a separate approved contract.

## Transaction, time, and replay

Creation retains its existing UUID idempotency lock. Test Connection creation
additionally takes transaction advisory locks for distinct customer UUIDs in
stable order before selecting its UTC creation time. The window is
`(created_at - 7 days, created_at]`: exactly seven-day-old records are excluded,
and the new record is included. Both count and up to ten recent references are
captured before commit, after insertion. A `(purpose, created_at)` index bounds
the date cohort; explicit JSON customer membership restricts the count.

One deterministic `billing.test_connection.created` event is staged per
extension/customer in the same owner transaction. Schema 1 includes operator
tenant, extension/customer UUIDs, creation time, UTC boundaries, integer
`count_7d`, and bounded references with creation actor and requested days.
It contains no contact details or arbitrary free-text reason. The durable
dispatcher handles it after commit; request creation never waits for Finance
delivery. A rollback writes neither the request nor its event. Exact creation
replay changes neither the count nor event identity.

Counts are frozen in the event. Delayed delivery/redrive does not recalculate
against today's window, and different event processing order cannot change
the original decisions. Account-key locking prevents two concurrent creations
from both missing the other's record.

## Workflow and consequence

Financial Access declares a `billing.service_extension` target, the versioned
creation trigger, customer scope, and integer count comparison operators.
Use condition `count_7d greater_than 5` and action
`billing.test_connection.notify_finance` with an explicitly chosen
`service_team_id`. Authoring requires the existing `billing:extension:read`,
`notification:write`, and Automation Center lifecycle permissions.

`financial.test_connection_finance_review` owns the typed notification command.
It locks and validates the durable source event, operator tenant, exact
classified extension, and customer scope. It resolves the selected active team
to active Party-bound SystemUsers through `communications.staff_notifications`.
An empty team or a member without an email fails closed with a visible
automation-step failure rather than claiming delivery.

The owner snapshots recipients in `test_connection_finance_reviews`, keyed by
event/rule-version/step, together with the configured team and evidence digest.
Typed staff participants stage personal in-app and email notices, plus audit,
atomically with that receipt. The message identifies the customer/account,
UTC period, count, recent request references, requested duration, creator,
and exact extension link. It requests review without asserting wrongdoing.

Replay returns the original recipient snapshot. A crash after action commit
but before step completion does not duplicate notices or add newly joined
team members. Changed team or event evidence under the same identity fails.
Existing delivery workers retain email retries and delivery-status authority.
Queued is not delivered. No SMTP client is called by the action.

Each new creation matching `> 5` can alert (sixth, seventh, etc.); event retries
cannot repeat one occurrence. Cross-occurrence cooldown is not silently added.
Deployment seeds or publishes no workflow and sends no historical alerts.

## UI contract

Staff choose Purpose in the existing Service Extension form; the field explains
explicit customer scope, creation-based counting, and separate approval.
Validation errors preserve the selected purpose. Detail shows the owner's
purpose label; NULL is visibly unclassified. Existing form layouts, action
permissions, and dark/mobile styles are retained. Automation Center's existing
team selector configures the Finance action; no special page or script is needed.

## Schema rollout and verification

Migration 645 expands the schema with nullable purpose, its closed-value check,
date index, and review receipt table. It performs no keyword backfill or data
rewrite. Old images may insert NULL while rolling deployment completes.
Keep old creation fingerprints for ordinary compensation so an outstanding
pre-upgrade form can replay. Test Connection purpose is part of its fingerprint.
Use a forward fix after classified requests or review receipts exist; downgrade
refuses to erase that evidence.

Verify fast unit behavior and migrated PostgreSQL separately. PostgreSQL tests
must use the real migration chain, including predecessor-to-645 rehearsal,
closed-purpose constraints, rollback, deterministic event uniqueness, and
concurrent same-customer creation. Test 5/6 thresholds, exact boundary, customer
and purpose isolation, duplicate subscription/selected-ID handling, canceled
history, delayed/repeated delivery, wrong tenant/target, empty/inactive team,
missing email, recipient preservation, and notification failure rollback.

See `docs/runbooks/TEST_CONNECTION_FINANCE_ALERT.md` for operator acceptance.
