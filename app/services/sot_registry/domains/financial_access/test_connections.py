"""Typed Automation Center support for temporary connection-test requests."""

from app.services.automation_contracts import (
    AutomationActionCapability,
    AutomationActionInput,
    AutomationConditionField,
    AutomationOperator,
    AutomationTriggerCapability,
    AutomationValueType,
)
from app.services.sot_manifest import (
    AuthorityInput,
    AuthorityKind,
    AuthorityMigrationState,
    ConcernContract,
    ErrorContract,
    EventContract,
    MigrationContract,
    OwnerRole,
    ServiceContract,
    SOTService,
    TransactionContract,
    TransactionMode,
    owner_command_boundary_error_codes,
)

TRIGGERS = (
    AutomationTriggerCapability(
        key="billing.test_connection.created",
        label="Test Connection created",
        event_type="billing.test_connection.created",
        event_schema_version=1,
        entity_type="billing.service_extension",
        tenant_id_field="tenant_id",
        entity_id_field="extension_id",
        fields=(
            AutomationConditionField(
                key="customer_id",
                label="Customer",
                value_type=AutomationValueType.uuid,
                operators=(AutomationOperator.in_values,),
            ),
            AutomationConditionField(
                key="count_7d",
                label="Test Connections created in the preceding 7 days",
                value_type=AutomationValueType.integer,
                operators=(
                    AutomationOperator.greater_than,
                    AutomationOperator.greater_than_or_equal,
                    AutomationOperator.equals,
                    AutomationOperator.less_than_or_equal,
                ),
            ),
        ),
        author_permission="billing:extension:read",
        runtime_enabled=True,
    ),
)

ACTIONS = (
    AutomationActionCapability(
        key="billing.test_connection.notify_finance",
        label="Notify Finance of repeated Test Connections",
        entity_type="billing.service_extension",
        command_owner="financial.test_connection_finance_review",
        command_name="notify_test_connection_finance",
        input_schema_version=1,
        inputs=(
            AutomationActionInput(
                key="service_team_id",
                label="Finance team (in-app and email)",
                value_type=AutomationValueType.uuid,
            ),
        ),
        author_permission="notification:write",
        runtime_scope="automation:runtime",
        idempotency="event/rule-version/step with immutable team and recipient snapshot",
        runtime_enabled=True,
    ),
)

_OWNER = "financial.test_connection_finance_review"
_CONCERN = "temporary Test Connection Finance review notifications"
SERVICES = (
    SOTService(
        name=_OWNER,
        module="app.services.test_connection_finance",
        owns=(_CONCERN, "Test Connection Finance recipient snapshots"),
        depends_on=(
            "financial.service_extensions",
            "automation.rule_definitions",
            "events.store",
            "customer.accounts",
            "operations.service_team_lifecycle",
            "communications.staff_notifications",
            "observability.audit_log",
        ),
        contract=ServiceContract(
            events=EventContract(
                event_types=("billing.test_connection.finance_review_queued",),
                schema_version=1,
                delivery_owner="events.dispatcher",
                compatibility="Version 1 contains review/source-event/customer IDs, recipient count and rule provenance; no recipient contacts.",
                replay="One deterministic evidence event per committed Finance review receipt; exact replay emits nothing.",
            ),
            concerns=(
                ConcernContract(
                    name=_CONCERN,
                    role=OwnerRole.APPLICATION_COORDINATOR,
                    input_names=(
                        "classified creation evidence",
                        "frozen Test Connection event",
                        "canonical customer identity",
                        "configured team and active staff",
                        "typed staff notification participant",
                        "published Finance workflow version",
                    ),
                ),
                ConcernContract(
                    name="Test Connection Finance recipient snapshots",
                    role=OwnerRole.AUTHORITATIVE_RECORD,
                    input_names=(
                        "frozen Test Connection event",
                        "configured team and active staff",
                    ),
                    canonical_writer=_OWNER,
                ),
            ),
            authoritative_inputs=(
                AuthorityInput(
                    name="published Finance workflow version",
                    owner="automation.rule_definitions",
                    kind=AuthorityKind.CONTROL_INPUT,
                    source="immutable published version, exact action position and configured Finance team UUID",
                ),
                AuthorityInput(
                    name="classified creation evidence",
                    owner="financial.service_extensions",
                    kind=AuthorityKind.AUTHORITATIVE_RECORD,
                    source="immutable Test Connection purpose and explicit customer scope",
                ),
                AuthorityInput(
                    name="frozen Test Connection event",
                    owner="events.store",
                    kind=AuthorityKind.AUTHORITATIVE_RECORD,
                    source="version-1 event with creation-time count and bounded request references",
                ),
                AuthorityInput(
                    name="canonical customer identity",
                    owner="customer.accounts",
                    kind=AuthorityKind.AUTHORITATIVE_RECORD,
                    source="Subscriber display identity and account number",
                ),
                AuthorityInput(
                    name="configured team and active staff",
                    owner="operations.service_team_lifecycle",
                    kind=AuthorityKind.AUTHORITATIVE_RECORD,
                    source="published workflow team UUID and active team-to-SystemUser membership",
                ),
                AuthorityInput(
                    name="typed staff notification participant",
                    owner="communications.staff_notifications",
                    kind=AuthorityKind.AUTHORITATIVE_RECORD,
                    source="source-linked in-app/email rows and per-recipient dedupe keys",
                ),
            ),
            transaction=TransactionContract(
                mode=TransactionMode.COORDINATOR_MANAGED,
                boundary="One execute_owner_command atomically stages the recipient snapshot, participant notifications, and audit. Participants flush only.",
                locking="Lock the durable source event before receipt lookup or any channel staging; unique event/version/step arbitrates replay.",
                idempotency="Deterministic review UUID pins team, evidence digest, and recipient IDs. Replay returns the original audience without new sends.",
                retries="Retry after full rollback; committed receipts survive failures between action completion and automation step acknowledgement.",
            ),
            errors=ErrorContract(
                retryable_codes=(f"{_OWNER}.recipients_unavailable",),
                domain_codes=tuple(
                    f"{_OWNER}.{code}"
                    for code in (
                        "invalid_scope",
                        "invalid_evidence",
                        "recipients_unavailable",
                        "replay_conflict",
                    )
                )
                + owner_command_boundary_error_codes(_OWNER),
                mapping_owner="automation.execution",
                fail_closed_on=(
                    "wrong tenant or target",
                    "unclassified source",
                    "missing recipients or email",
                    "changed replay evidence",
                ),
            ),
            migration=MigrationContract(
                state=AuthorityMigrationState.NATIVE,
                new_owner=_OWNER,
                verification="threshold, time window, customer isolation, notification replay, and migrated PostgreSQL tests",
                cutover_gate="Migration 645 and trigger/action registration are deployed together; operators explicitly publish the workflow.",
                fallback_retirement="No keyword matching, scheduler workaround, direct delivery, or automatic workflow publication.",
            ),
            steward="billing and Finance operations",
            design_refs=(
                "docs/designs/TEST_CONNECTION_FINANCE_ALERT.md",
                "docs/designs/AUTOMATION_CENTER_SOT.md",
            ),
            test_refs=(
                "tests/test_test_connection_finance.py",
                "tests/integration/test_test_connection_finance.py",
                "tests/architecture/test_test_connection_boundary.py",
            ),
        ),
    ),
)
