# Subscription pause lifecycle

Status: implementation contract

## Meaning

`paused` is a first-class Subscription and derived Subscriber status. It means
normal service access and recurring service-period consumption are stopped,
while the customer's service identity and configuration are preserved. It is
not an alias for `suspended`, `blocked`, `disabled`, or `canceled`.

The pre-existing administrative Disable/Restore workflow remains a separate
legacy lifecycle contract. It cannot create or release a ticket-SLA pause
cause and is not used by this Automation action.

| Concern | Paused contract |
| --- | --- |
| Access | denied |
| Billing period | stops at the episode effective time |
| Credentials, IP, devices and offer | preserved |
| Account status | derived `paused` |
| Customer portal | remains available and explains the pause |
| Resume | authorized administrator after cause-specific eligibility |
| Compensation | exact effective pause duration, once |

## Ownership

`access.subscription_lifecycle` owns status transitions, pause episodes and
causes, account projection, the typed prepaid pause-compensation entitlement,
billing-anchor adjustment, lifecycle evidence, and events.
`support.ticket_sla_service_consequence` owns revalidating Ticket/SLA evidence
and coordinating the SLA cause, including typed resume eligibility previews.
Automation adapters, routes, event handlers,
and templates do not write lifecycle or billing state.

One episode represents `[effective_at, resumed_at)`. Multiple typed causes may
hold it open. Source identity and idempotency keys make pause replay converge;
cause release is independent; the last release closes the episode. Only
`active -> paused` begins an episode. Final release results in `active` when no
independent enforcement lock remains, otherwise `suspended`.

## SLA workflow

1. The SLA owner records a real resolution breach and stages the durable event.
2. A published Automation rule selects the typed pause action and policies.
3. The coordinator revalidates an unresolved Ticket and one active service.
4. The lifecycle owner creates the episode/cause and transitions to `paused`.
5. The customer receives a durable notification; network projection denies new
   sessions and disconnects current sessions after commit.
6. At `pending_confirmation` or `closed`, an administrator previews resume.
7. Confirmation rechecks the fingerprint, releases the cause, moves the
   billing anchor by the exact interval once, and restores access if eligible.
   For prepaid service, the same transaction creates one zero-value
   `ServiceEntitlement` linked uniquely to the pause episode for
   `[previous_next_billing_at, resulting_next_billing_at)`. The original paid
   entitlement and invoice period remain immutable. Missing, overlapping, or
   anchor-inconsistent prepaid entitlement evidence fails closed.

## Configuration and invariants

Customer scope, SLA targets, workflow publication, service-selection policy,
resume policy, billing treatment, and notification delivery are configured by
their existing database-backed owners. Code contains only closed typed domain
values and safety invariants. The example 30-day service period is never a
constant: the existing subscription billing anchor/cadence is authoritative.

Deployment adds the status values, evidence tables, permissions, capabilities,
templates, and readers. It does not publish a workflow, grant permissions, or
convert existing subscriptions.
