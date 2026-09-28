"""Assemble the canonical provisioning_operations SOT domain from capability shards."""

from __future__ import annotations

from app.services.automation_contracts import (
    AutomationCatalogItem,
    AutomationCatalogState,
    AutomationDomainCapabilities,
)
from app.services.sot_registry.domains.provisioning_operations.core import (
    SERVICES as CORE_SERVICES,
)
from app.services.sot_registry.domains.provisioning_operations.vendor_delivery import (
    SERVICES as VENDOR_DELIVERY_SERVICES,
)
from app.services.sot_registry.domains.provisioning_operations.vendor_identity import (
    SERVICES as VENDOR_IDENTITY_SERVICES,
)
from app.services.sot_registry.model import DomainSOT

DOMAIN = DomainSOT(
    domain="provisioning_operations",
    setting_domains=(
        "provisioning",
        "projects",
        "inventory",
        "field",
    ),
    services=(
        *CORE_SERVICES,
        *VENDOR_IDENTITY_SERVICES,
        *VENDOR_DELIVERY_SERVICES,
    ),
    entrypoints=(
        "app.services.events.handlers.provisioning",
        "app.tasks.ont_provisioning",
        "app.web.admin.provisioning",
        "app.web.admin.projects",
        "app.web.vendor_portal",
        "app.api.vendor_portal",
        "app.api.projects",
        "app.api.field.*",
        "app.services.web_projects",
        "app.services.web_dispatch_work_orders",
        "app.services.work_order_commands",
        "field_mobile",
    ),
    rule="Provisioning callers resolve customer/network context through the "
    "shared context layer before executing workflow steps. Native project "
    "mutation adapters delegate to Projects.update for lifecycle consequences. "
    "Field clients consume completion_requirements from authenticated job "
    "detail and leave completion eligibility to the field transition service. "
    "Dispatch adapters delegate native work-order and assignment writes to "
    "operations.work_order_commands.",
    automation=AutomationDomainCapabilities(
        catalog_items=(
            AutomationCatalogItem(
                key="provisioning.subscription_activation",
                label="Subscription activation provisioning",
                group="Provisioning",
                state=AutomationCatalogState.unavailable,
                explanation="Activation provisions network access through linked lifecycle owners; the ordered steps are not yet registered as Center actions.",
            ),
            AutomationCatalogItem(
                key="provisioning.subscription_resume",
                label="Subscription-resume provisioning",
                group="Provisioning",
                state=AutomationCatalogState.unavailable,
                explanation="Resume and access restoration remain in the existing provisioning flow; no Center trigger or safe action is registered.",
            ),
            AutomationCatalogItem(
                key="provisioning.service_order_workflow",
                label="Service-order provisioning workflow",
                group="Provisioning",
                state=AutomationCatalogState.unavailable,
                explanation="The service-order workflow includes dependent provisioning steps owned by current services and is not yet available as Center actions.",
            ),
            AutomationCatalogItem(
                key="provisioning.readiness_decision",
                label="Provisioning readiness decision",
                group="Provisioning",
                state=AutomationCatalogState.unavailable,
                explanation="Readiness checks stay in the provisioning owner; a rule cannot bypass their required checks.",
            ),
            AutomationCatalogItem(
                key="provisioning.stale_run_cleanup",
                label="Stale provisioning-run cleanup",
                group="Provisioning",
                state=AutomationCatalogState.unavailable,
                explanation="Cleanup is a protected recovery job and is not an editable customer-business action in the Center.",
            ),
            AutomationCatalogItem(
                key="provisioning.compensation_retry",
                label="Provisioning compensation retry",
                group="Provisioning",
                state=AutomationCatalogState.unavailable,
                explanation="Retrying compensation can affect service access and remains under its existing recovery safeguards.",
            ),
            AutomationCatalogItem(
                key="provisioning.bulk_activation_migration",
                label="Background bulk activation and migration",
                group="Provisioning",
                state=AutomationCatalogState.unavailable,
                explanation="Bulk activation is an operator-started process with bounded migration safeguards, not a reusable per-customer rule action.",
            ),
        ),
    ),
)
