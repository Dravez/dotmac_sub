from __future__ import annotations

from pathlib import Path

from app.services import automation_capabilities
from app.services.sot_registry.registry import find_service

ROOT = Path(__file__).resolve().parents[2]


def test_only_subscription_lifecycle_owner_writes_paused_status() -> None:
    assignment = "subscription.status = SubscriptionStatus.paused"
    writers = {
        path.relative_to(ROOT).as_posix()
        for path in (ROOT / "app").rglob("*.py")
        if assignment in path.read_text(encoding="utf-8")
    }

    assert writers == {"app/services/account_lifecycle.py"}


def test_automation_pause_action_delegates_to_registered_coordinator() -> None:
    capability = automation_capabilities.action_capability(
        "support.ticket.pause_unique_active_service"
    )
    owner = find_service("support.ticket_sla_service_consequence")

    assert capability is not None
    assert capability.command_owner == owner.name
    assert capability.command_name == (
        "pause_unique_active_service_for_ticket_sla_breach"
    )
    adapter_source = (ROOT / "app/services/automation_actions.py").read_text(
        encoding="utf-8"
    )
    assert "Subscription.status" not in adapter_source
    assert "Subscriber.status" not in adapter_source


def test_pause_and_legacy_suspend_actions_are_publication_conflicts() -> None:
    pause = automation_capabilities.action_capability(
        "support.ticket.pause_unique_active_service"
    )
    suspend = automation_capabilities.action_capability(
        "support.ticket.suspend_unique_active_service"
    )

    assert pause is not None
    assert suspend is not None
    assert pause.conflict_scope == suspend.conflict_scope
    assert pause.conflict_scope == "support.ticket.sla_service_access_consequence"
