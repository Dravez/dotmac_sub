# Automation Center follow-up plan

## Objective

Make the deployed Automation Center an operator-facing workspace rather than a
single control-plane dashboard. Workflows are the product term for the existing
rule mechanism; the internal rule owner and compatibility routes remain intact.

The follow-up also fixes the rule-builder condition interaction, verifies every
visible action, expands the approved customer and support event catalogue, and
teaches users how modules, targets, events, keys, conditions, and actions fit
together.

## Ordered local slices

1. **Workflow builder behavior** (`feat/automation-center-workflow-builder`)
   - Rename operator-facing “rule” copy to “workflow”.
   - Fix condition and action row rendering, removal, serialization, and empty
     state behavior.
   - Keep `/rules` compatibility routes while introducing workflow language in
     page titles, buttons, and guidance.
   - Add focused browser-boundary and serialization tests.

2. **Customer and support event capabilities**
   - Declare additional typed triggers backed by existing durable owner events.
   - Add missing bounded event payloads only at the owning lifecycle command.
   - Keep event creation out of the operator UI; the UI lists approved events
     and explains when developer registration is required.
   - Add registry and event-producer architecture tests.

3. **Mechanism-specific information architecture**
   - Turn `/admin/automation` into a small directory for Workflows, Client
     scripts, Server scripts, and execution history.
   - Move registry/catalogue/ownership diagnostics out of the default operator
     page.
   - Add dedicated list surfaces with search, module/target/status filters, and
     date filtering for workflows and scripts.

4. **Guided authoring and lifecycle detail**
   - Add mechanism-specific creation help and field explanations.
   - Group target choices by module and show the selected event contract.
   - Explain that both script types use JavaScript, while client and server
     execution boundaries differ.
   - Expose draft, published, unavailable-runtime, and version states clearly.

5. **End-to-end verification**
   - Exercise every workflow-builder control and every available lifecycle
     action through focused tests.
   - Verify the expanded customer/support trigger matrix and event payload
     identities.
   - Run formatting, static checks, architecture guards, and focused browser
     checks available in the development environment.

## Contract decisions

- “Workflow” is the user-facing name; `automation_rules` and `/rules` remain
  compatibility internals until a separately approved storage migration exists.
- Admins cannot invent arbitrary events in the UI. An event must be declared by
  the owning SOT domain, emitted by its owner command, and carry a typed payload
  before it becomes selectable.
- Server scripts remain JavaScript, but execute only in the isolated,
  digest-pinned runtime and may return only registered typed actions.
- Diagnostics are valuable for developers and administrators, but they are not
  part of the default operator workflow.
