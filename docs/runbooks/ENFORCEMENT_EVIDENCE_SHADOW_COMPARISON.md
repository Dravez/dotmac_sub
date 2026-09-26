# Enforcement evidence shadow comparison (ADR-0017 slice-2 gate)

ADR-0017 (`docs/adr/0017-enforcement-application-evidence.md`) requires a
recorded shadow comparison of `EnforcementApplication` evidence against logs
and router state before slice 2 (any resolver or projection reading this
record) is allowed to proceed. This runbook is that comparison. It is
read-only: it changes nothing in Sub and nothing on any router.

## When to run it

- After the ADR-0017 slice-1 deploy (the commit that starts writing
  `enforcement_applications`) has been running in production for **at least 7
  days**, so the default `--since-hours 168` window covers a full week
  including any weekly maintenance pattern.
- Whenever slice 2 work is proposed, to refresh the gate's evidence.

## Preconditions

Running this against the real production database is a production-access
operation. It requires:

- explicit authorization from Michael, and
- Michael naming the exact target host.

Do not infer a target host from a prior deployment mapping.

## Command

Run inside the deployed container (or an ad hoc container using the exact
reviewed image), against the target host's database only:

```bash
APP_IMAGE=ghcr.io/michaelayoade/dotmac_sub:sha-<reviewed-sha> \
docker compose -f docker-compose.yml run --rm --no-deps app \
  python -m scripts.enforcement_evidence_shadow_report \
  --since-hours 168 --limit 500
```

Add `--json` for a machine-readable payload (e.g. to attach to the slice-2
change as recorded evidence).

## How to read each section

- **totals by (effect, outcome)** — the shape of enforcement traffic in the
  window: how much of each effect (`address_list_block`,
  `address_list_unblock`, `session_kick`) applied vs. failed vs. was not
  applicable. A large `failed` count for one effect is the first signal to
  investigate.
- **totals by failure_class** — which failure mode dominates
  (`auth_rejected`, `unreachable`, `timeout`, `command_failed`,
  `not_capable`). `auth_rejected` concentrated on one NAS is exactly the
  2026-09-17 Eagle FM Access failure mode ADR-0017 exists to catch.
- **per-NAS summary** — failed/applied counts, last successful application,
  and oldest still-open failure streak, per router. A NAS with a nonzero
  `failed` count and no recent `last_success_at` is a router evidence should
  no longer be trusted for, until cross-checked below.
- **currently failed** — the bounded list (default 500, `--limit`) of
  evidence rows whose most recent recorded outcome is `failed`, joined with
  each subscription's **current** status. This juxtaposition is evidence, not
  a verdict: a `None` subscription status means the subscription id no longer
  resolves (ADR-0017 §3 — no foreign key, dangling ids are expected and must
  be tolerated, not treated as an error).
- **mismatch candidates** — a strict subset of "currently failed": a failed
  `address_list_unblock` while the subscription now reads `active` (the
  customer may still be network-blocked despite an active subscription), or a
  failed `address_list_block` while the subscription now reads `suspended` or
  `blocked` (the block may never have reached the router). Each is a
  **candidate for human review**, never a resolved discrepancy and never
  itself the intended state — that judgment is exactly what this runbook
  exists to support, not replace.

## Cross-check against logs (Loki)

The pre-ADR-0017 failure path was log-only (`app.services.enforcement`),
using exactly these two warning messages:

```
Address-list %s: API fallback failed for %s
API kick failed on %s
```

On the observability host, query Loki for the same window (Sub's production
logs carry `app="dotmac-sub"`):

```logql
{app="dotmac-sub"} |= "API fallback failed" or |= "API kick failed on"
```

Cross-check: every NAS/subscription pair the shadow report names under
"currently failed" or "mismatch candidates" should have a corresponding log
line in the same window (or, for anything failing before slice 1 deployed,
only in the logs — the record starts empty). A NAS with heavy log warnings
but no corresponding evidence row is a sign the writer itself is failing
silently (ADR-0017 §7 logs that case at `ERROR` under
`enforcement_application_record_failed` — check for it separately).

## Spot-check router state (read-only)

For a handful of the mismatch candidates (not all — this is a sampled
integrity check, not a full reconciliation), verify the router's actual
address-list membership using an operator's normal RouterOS access. This is a
**read-only** query; do not modify the address list from this runbook:

```
/ip firewall address-list print where list=<suspended-list-name> address=<subscriber-ip>
```

Use the subscription's currently assigned IP and the suspended/blocked
address-list name configured for that NAS. Compare:

- a failed `address_list_block` mismatch candidate: if the address is
  **absent** from the list, the block genuinely never applied — confirms the
  candidate;
- a failed `address_list_unblock` mismatch candidate: if the address is
  **present** on the list, the unblock genuinely never applied — confirms the
  candidate.

If the router state actually agrees with the subscription's current status
(the candidate does not reproduce), record that as a false positive for this
run — it does not invalidate the mechanism, but note it in the recorded
evidence for slice 2's reviewers.

## Recording the result

Attach the `--json` output (or a summary quoting the counts above) to the
slice-2 change as the recorded shadow comparison ADR-0017 requires. Slice 2
must not proceed without this evidence attached and reviewed.
