"""Detect a NEW writer that opens its own session and commits, undeclared.

ADR 0017 introduces ``TransactionMode.OUT_OF_BAND_EVIDENCE``: a service is
allowed to open its own ``db_session_adapter.create_session()`` unit of work
and commit it independently of the caller's transaction, instead of joining
it. ``tests/architecture/test_out_of_band_evidence_ratchet.py`` binds every
service that DECLARES this mode to its approving ADR -- but its own docstring
says the gap plainly: nothing there detects a new writer that does the same
``create_session()`` + ``commit()`` thing without declaring the mode at all.

This module closes that gap with a static AST scan over ``app/**/*.py`` for
any function whose OWN body (not a nested function's body) both opens a
session via ``create_session()`` -- directly, or through a module-level
alias such as ``SessionLocal = db_session_adapter.create_session`` (see
``app/services/enforcement_scheduled.py``) -- and calls ``.commit()`` on
anything. The result is compared, in both directions, against a fixed
APPROVED set (declared ``out_of_band_evidence`` writers, checked against the
SOT registry) union a BASELINE set (everything else the scan currently
finds, each classified by hand). A hit outside both sets fails the build: it
is either a genuinely new out-of-band writer that needs an ADR and a
registry declaration (ADR 0017), or it needs classifying into BASELINE with
a reason. A BASELINE entry that stops matching must be removed, so this
ratchet only shrinks.
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

#: Declared ``out_of_band_evidence`` writers (ADR 0017), each of which must
#: also be bound to its approving ADR by
#: ``test_out_of_band_evidence_ratchet.APPROVED_OUT_OF_BAND_EVIDENCE`` and be
#: registered in the SOT registry with that transaction mode (see test (c)
#: below).
APPROVED: frozenset[str] = frozenset(
    {
        "app/services/enforcement_evidence.py::record_enforcement_application",
    }
)

#: Everything else the scanner currently finds, classified by hand. Adding an
#: entry here without a genuine reason defeats the ratchet -- see (a)/(b)
#: below, which fail the build on both a new, unclassified hit and a stale
#: baseline entry that no longer matches.
BASELINE: dict[str, str] = {
    # -- Legacy out-of-band writers, already tracked by name in
    # -- test_out_of_band_evidence_ratchet.KNOWN_LEGACY_OUT_OF_BAND_WRITERS
    # -- (ADR 0017 follow-up debt, not yet migrated to the declared mode).
    "app/services/nas/_mikrotik.py::_record_mikrotik_auth_attempt": (
        "legacy out-of-band writer (listed in "
        "test_out_of_band_evidence_ratchet.KNOWN_LEGACY_OUT_OF_BAND_WRITERS)"
    ),
    "app/services/prepaid_service_renewals.py::_record_review_item_out_of_band": (
        "legacy out-of-band writer (listed in "
        "test_out_of_band_evidence_ratchet.KNOWN_LEGACY_OUT_OF_BAND_WRITERS)"
    ),
    # -- Same technique as the legacy writer above (a staff alert on a genuinely
    # -- independent connection). Surfaced by this scanner and now listed in
    # -- KNOWN_LEGACY_OUT_OF_BAND_WRITERS rather than silently baselined.
    "app/services/prepaid_service_renewals.py::_finalize_scheduled_renewal_summary": (
        "legacy out-of-band writer (listed in "
        "test_out_of_band_evidence_ratchet.KNOWN_LEGACY_OUT_OF_BAND_WRITERS)"
    ),
    # -- Scheduled-job / background-worker pattern: a Celery task or scheduled
    # -- runner opens its own session for the lifetime of ONE scheduled
    # -- invocation and commits its own work; there is no caller transaction
    # -- to join because the task itself is the top of the call stack. This
    # -- is the ordinary "adapter owns its own unit of work" shape, not an
    # -- evidence-writer exemption from ADR 0017's caller-transaction rule.
    "app/services/billing/scheduled.py::mark_invoices_overdue": (
        "adapter-owned session lifecycle (task/runner)"
    ),
    "app/services/billing/scheduled.py::run_billing_notifications": (
        "adapter-owned session lifecycle (task/runner)"
    ),
    "app/services/billing/scheduled.py::run_invoice_cycle": (
        "adapter-owned session lifecycle (task/runner)"
    ),
    "app/services/billing_invoice_pdf.py::process_export": (
        "adapter-owned session lifecycle (task/runner)"
    ),
    "app/services/collections/scheduled.py::run_billing_enforcement": (
        "adapter-owned session lifecycle (task/runner)"
    ),
    "app/services/collections/scheduled.py::run_bundle_reconcile": (
        "adapter-owned session lifecycle (task/runner)"
    ),
    "app/services/enforcement_scheduled.py::cleanup_subscription_block_sessions": (
        "adapter-owned session lifecycle (task/runner)"
    ),
    "app/services/network/ont_action_common.py::persist_data_model_root": (
        "adapter-owned session lifecycle: isolated best-effort side-transaction "
        "persisting a detected TR-069 data-model root, own session per call"
    ),
    "app/services/task_idempotency.py::idempotent_task.decorator.wrapper": (
        "infrastructure: the idempotent_task decorator's own TaskExecution "
        "ledger writes, on its own session, independent of the wrapped task"
    ),
    "app/services/web_network_core_runtime.py::_refresh_device_health_worker": (
        "adapter-owned session lifecycle (task/runner): isolated background "
        "device-health refresh, own session per call"
    ),
    "app/tasks/forwarding_control_observations.py::run_forwarding_control_observation_poll": (
        "adapter-owned session lifecycle (task/runner): scheduled read-only "
        "collection task"
    ),
    "app/tasks/gis.py::run_batch_geocode_job": "adapter-owned session lifecycle (task/runner)",
    "app/tasks/gis.py::sync_gis_sources": "adapter-owned session lifecycle (task/runner)",
    "app/tasks/nas.py::check_nas_health": "adapter-owned session lifecycle (task/runner)",
    "app/tasks/nas.py::run_scheduled_backups": "adapter-owned session lifecycle (task/runner)",
    "app/tasks/nas.py::update_subscriber_counts": "adapter-owned session lifecycle (task/runner)",
    "app/tasks/network_operations.py::cleanup_old_operations": (
        "adapter-owned session lifecycle (task/runner)"
    ),
    "app/tasks/nin_tasks.py::verify_nin_task": "adapter-owned session lifecycle (task/runner)",
    "app/tasks/olt_config_backup.py::backup_all_olts": (
        "adapter-owned session lifecycle (task/runner)"
    ),
    "app/tasks/olt_firmware.py::rollback_firmware_task": (
        "adapter-owned session lifecycle (task/runner)"
    ),
    "app/tasks/olt_firmware.py::upgrade_firmware_task": (
        "adapter-owned session lifecycle (task/runner)"
    ),
    "app/tasks/ont_signal_observations.py::record_ont_observations": (
        "adapter-owned session lifecycle (task/runner): scheduled telemetry "
        "observation collector (freezes a status/Rx snapshot), own session"
    ),
    "app/tasks/radius.py::_run_enforcement_reconciler": (
        "false-positive coupling, not an evidence writer: the flagged "
        "``.commit()`` belongs to a separate raw psycopg connection "
        "(``rconn``) used for RADIUS drift bookkeeping, not the "
        "``create_session()`` ORM session opened in this same function, "
        "which is never committed here"
    ),
    "app/tasks/radius.py::reconcile_active_sessions": (
        "adapter-owned session lifecycle (task/runner)"
    ),
    "app/tasks/radius.py::run_radius_sync_job": (
        "adapter-owned session lifecycle (task/runner)"
    ),
    "app/tasks/router_sync.py::_recover_pending_readback": (
        "adapter-owned session lifecycle (task/runner)"
    ),
    "app/tasks/router_sync.py::audit_sot_drift": (
        "adapter-owned session lifecycle (task/runner)"
    ),
    "app/tasks/router_sync.py::capture_scheduled_snapshots": (
        "adapter-owned session lifecycle (task/runner)"
    ),
    "app/tasks/router_sync.py::execute_config_push": (
        "adapter-owned session lifecycle (task/runner)"
    ),
    "app/tasks/router_sync.py::reconcile_config_push_readback": (
        "adapter-owned session lifecycle (task/runner)"
    ),
    "app/tasks/router_sync.py::reconcile_nas_vlan_readback": (
        "adapter-owned session lifecycle (task/runner)"
    ),
    "app/tasks/router_sync.py::sync_all_system_info": (
        "adapter-owned session lifecycle (task/runner)"
    ),
    "app/tasks/tr069.py::apply_acs_config": "adapter-owned session lifecycle (task/runner)",
    "app/tasks/tr069.py::apply_saved_ont_service_config": (
        "adapter-owned session lifecycle (task/runner)"
    ),
    "app/tasks/tr069.py::cleanup_tr069_records": (
        "adapter-owned session lifecycle (task/runner)"
    ),
    "app/tasks/tr069.py::wait_for_ont_bootstrap": (
        "adapter-owned session lifecycle (task/runner)"
    ),
    "app/tasks/uisp_control.py::_mark_pending_readback": (
        "adapter-owned session lifecycle (task/runner)"
    ),
    "app/tasks/uisp_ip_backfill.py::run_uisp_mgmt_ip_backfill": (
        "adapter-owned session lifecycle (task/runner): manual-trigger "
        "inventory backfill, own session per run"
    ),
    "app/tasks/unmatched_radio.py::run_unmatched_radio_review": (
        "adapter-owned session lifecycle (task/runner)"
    ),
    "app/tasks/usage.py::evaluate_fup_rules": "adapter-owned session lifecycle (task/runner)",
}


def _module_create_session_aliases(module: ast.Module) -> frozenset[str]:
    """Module-level ``NAME = <expr>.create_session`` aliases (top-level only).

    Mirrors the pattern in ``app/services/enforcement_scheduled.py``:
    ``SessionLocal = db_session_adapter.create_session``.
    """
    aliases: set[str] = set()
    for node in module.body:
        targets: list[ast.expr] | None = None
        value: ast.expr | None = None
        if isinstance(node, ast.Assign):
            targets = node.targets
            value = node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets = [node.target]
            value = node.value
        if targets is None or value is None:
            continue
        if isinstance(value, ast.Attribute) and value.attr == "create_session":
            for target in targets:
                if isinstance(target, ast.Name):
                    aliases.add(target.id)
    return frozenset(aliases)


def _is_create_session_call(call: ast.Call, aliases: frozenset[str]) -> bool:
    func = call.func
    if isinstance(func, ast.Attribute) and func.attr == "create_session":
        return True
    return isinstance(func, ast.Name) and func.id in aliases


def _is_commit_call(call: ast.Call) -> bool:
    return isinstance(call.func, ast.Attribute) and call.func.attr == "commit"


def _own_body_calls_both(node: ast.AST, aliases: frozenset[str]) -> bool:
    """Whether ``node``'s OWN body (excluding nested defs) has both calls.

    A nested function's/lambda's/class's body is a separate scope reported
    as its own hit, so descent stops there -- otherwise an outer function
    would be flagged purely because something it defines internally happens
    to touch both calls.
    """
    has_create = False
    has_commit = False

    def walk(current: ast.AST) -> None:
        nonlocal has_create, has_commit
        for child in ast.iter_child_nodes(current):
            if isinstance(
                child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)
            ):
                continue
            if isinstance(child, ast.Call):
                if _is_create_session_call(child, aliases):
                    has_create = True
                if _is_commit_call(child):
                    has_commit = True
            walk(child)

    walk(node)
    return has_create and has_commit


def find_out_of_band_writers(root: Path) -> list[str]:
    """Pure scanner: every ``app/**/*.py`` function that opens its own
    session (directly or via a module-level alias) AND commits it, within
    its own body. Returns sorted ``"path::qualname"`` strings, ``qualname``
    dotted through enclosing classes/functions.
    """
    hits: list[str] = []
    app_root = root / "app"
    for path in sorted(app_root.rglob("*.py")):
        try:
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(path))
        except (SyntaxError, UnicodeDecodeError):
            continue
        aliases = _module_create_session_aliases(tree)
        rel = path.relative_to(root).as_posix()
        hits.extend(_scan_module_functions(tree, aliases, rel))
    return sorted(hits)


def _scan_module_functions(
    node: ast.AST, aliases: frozenset[str], rel: str, stack: list[str] | None = None
) -> list[str]:
    stack = stack if stack is not None else []
    found: list[str] = []
    for child in ast.iter_child_nodes(node):
        if isinstance(child, ast.ClassDef):
            found.extend(
                _scan_module_functions(child, aliases, rel, [*stack, child.name])
            )
        elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
            qualname = ".".join([*stack, child.name])
            if _own_body_calls_both(child, aliases):
                found.append(f"{rel}::{qualname}")
            found.extend(
                _scan_module_functions(child, aliases, rel, [*stack, child.name])
            )
        else:
            found.extend(_scan_module_functions(child, aliases, rel, stack))
    return found


def test_hits_equal_approved_union_baseline_two_directional() -> None:
    hits = set(find_out_of_band_writers(ROOT))
    expected = APPROVED | set(BASELINE)

    new_hits = hits - expected
    assert not new_hits, (
        "New writer(s) open their own create_session() session and commit "
        "it without being declared: "
        + ", ".join(sorted(new_hits))
        + ". Either declare TransactionMode.OUT_OF_BAND_EVIDENCE for the "
        "service with an approving ADR (see ADR 0017 and "
        "tests/architecture/test_out_of_band_evidence_ratchet.py) and add it "
        "to APPROVED above, or classify it in BASELINE above with a reason."
    )

    stale_baseline = set(BASELINE) - hits
    assert not stale_baseline, (
        "BASELINE entry no longer matches the scanner and must be removed "
        "so this ratchet only shrinks: " + ", ".join(sorted(stale_baseline))
    )


def test_approved_and_baseline_do_not_overlap() -> None:
    overlap = APPROVED & set(BASELINE)
    assert not overlap, "Entries listed in both APPROVED and BASELINE: " + ", ".join(
        sorted(overlap)
    )


def test_every_approved_entry_is_registered_out_of_band_evidence() -> None:
    from app.services.sot_manifest import TransactionMode
    from app.services.sot_registry.registry import all_services

    mode = TransactionMode.OUT_OF_BAND_EVIDENCE.value
    registered_modules_by_mode = {
        service.module: (
            service.contract.transaction.mode.value if service.contract else None
        )
        for service in all_services()
    }

    for entry in APPROVED:
        module_path, _, _qualname = entry.partition("::")
        dotted_module = (
            module_path[: -len(".py")].replace("/", ".")
            if module_path.endswith(".py")
            else module_path.replace("/", ".")
        )
        actual_mode = registered_modules_by_mode.get(dotted_module)
        assert actual_mode == mode, (
            f"{entry}: registry module {dotted_module!r} has transaction mode "
            f"{actual_mode!r}, expected {mode!r} to justify APPROVED membership"
        )


class TestScannerSensitivity:
    """A guard that never fires proves nothing: plant a hit and a near-miss."""

    def test_a_planted_create_session_plus_commit_function_is_flagged(
        self, tmp_path: Path
    ) -> None:
        app_dir = tmp_path / "app" / "services"
        app_dir.mkdir(parents=True)
        (app_dir / "sneaky_writer.py").write_text(
            "from app.services.db_session_adapter import db_session_adapter\n"
            "\n"
            "\n"
            "def write_something(x):\n"
            "    s = db_session_adapter.create_session()\n"
            "    s.add(x)\n"
            "    s.commit()\n",
            encoding="utf-8",
        )

        hits = find_out_of_band_writers(tmp_path)

        assert "app/services/sneaky_writer.py::write_something" in hits

    def test_create_session_without_commit_is_not_flagged(self, tmp_path: Path) -> None:
        app_dir = tmp_path / "app" / "services"
        app_dir.mkdir(parents=True)
        (app_dir / "reader.py").write_text(
            "from app.services.db_session_adapter import db_session_adapter\n"
            "\n"
            "\n"
            "def read_something():\n"
            "    s = db_session_adapter.create_session()\n"
            "    return s.query(object).first()\n",
            encoding="utf-8",
        )

        hits = find_out_of_band_writers(tmp_path)

        assert "app/services/reader.py::read_something" not in hits

    def test_commit_without_create_session_is_not_flagged(self, tmp_path: Path) -> None:
        app_dir = tmp_path / "app" / "services"
        app_dir.mkdir(parents=True)
        (app_dir / "caller_owned.py").write_text(
            "def write_with_caller_session(db, x):\n    db.add(x)\n    db.commit()\n",
            encoding="utf-8",
        )

        hits = find_out_of_band_writers(tmp_path)

        assert "app/services/caller_owned.py::write_with_caller_session" not in hits


def test_every_known_legacy_out_of_band_writer_is_in_the_baseline() -> None:
    """The ratchet's legacy list and this detector's baseline must agree, so a
    legacy writer can neither be listed without being detected nor detected
    without being listed as legacy."""
    from tests.architecture.test_out_of_band_evidence_ratchet import (
        KNOWN_LEGACY_OUT_OF_BAND_WRITERS,
    )

    missing = sorted(set(KNOWN_LEGACY_OUT_OF_BAND_WRITERS) - set(BASELINE))
    assert not missing, f"legacy writers absent from BASELINE: {missing}"
    legacy_in_baseline = sorted(
        key for key, why in BASELINE.items() if why.startswith("legacy out-of-band")
    )
    assert legacy_in_baseline == sorted(KNOWN_LEGACY_OUT_OF_BAND_WRITERS), (
        "BASELINE entries classified as legacy out-of-band writers must match "
        "KNOWN_LEGACY_OUT_OF_BAND_WRITERS exactly"
    )
