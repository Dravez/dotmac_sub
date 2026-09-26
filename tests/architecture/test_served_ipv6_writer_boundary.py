"""Shrink-only guard on writers of the served IPv6 projection.

``network.ip_assignment_lifecycle`` owns the desired IPv6 at exact service
grain. ``Subscription.ipv6_address`` is a compatibility projection of the
active ``IPAssignment`` — see ``app/models/catalog.py``,
``app/services/connectivity_reconciler.py``, and
``docs/designs/IP_ASSIGNMENT_LIFECYCLE_SOT.md``.

This is the IPv6 twin of ``test_served_ipv4_writer_boundary.py``: same owner,
same projection shape, same shrink-only ratchet, kept as a separate baseline
because the two attributes have independent (and currently different) writer
sets.

This guard does not claim the boundary is in place. It freezes the remaining
direct writers so a change cannot add another one while the cutover lands.
"""

from __future__ import annotations

from pathlib import Path

from scripts.architecture.sot_debt import served_ipv6_projection_writes

BASELINE = Path(__file__).with_name("served_ipv6_writer_baseline.txt")


def _read_baseline(path: Path) -> dict[str, int]:
    """Read ``count path`` entries from the shrink-only baseline."""

    counts: dict[str, int] = {}
    for line_number, raw in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        count, _, name = stripped.partition(" ")
        try:
            parsed = int(count)
        except ValueError as exc:  # pragma: no cover - malformed baseline
            raise ValueError(
                f"invalid baseline entry {path}:{line_number}: {raw!r}"
            ) from exc
        if parsed < 1:  # pragma: no cover - malformed baseline
            raise ValueError(f"baseline count must be positive at {path}:{line_number}")
        counts[name.strip()] = parsed
    return counts


REMEDY = (
    "Route the write through network.ip_assignment_lifecycle so the served "
    "column stays a projection of an exact-service IPAssignment. Provisioning "
    "callers can use provisioning_helpers.ensure_ip_assignments_for_"
    "subscription (there is no IPv6-only equivalent of "
    "ensure_ipv4_assignment_for_subscription yet)."
)


def test_no_new_served_ipv6_projection_writers() -> None:
    current = served_ipv6_projection_writes()
    baseline = _read_baseline(BASELINE)

    added = sorted(set(current) - set(baseline))
    assert not added, (
        "new direct writes to Subscription.ipv6_address in files absent from "
        f"the shrink-only baseline. {REMEDY}\n  " + "\n  ".join(added)
    )

    grew = sorted(
        f"{name}: {baseline[name]} -> {current[name]}"
        for name in set(current) & set(baseline)
        if current[name] > baseline[name]
    )
    assert not grew, (
        f"existing served-IPv6 writers gained new write sites. {REMEDY}\n  "
        + "\n  ".join(grew)
    )


def test_served_ipv6_writer_baseline_only_shrinks() -> None:
    current = served_ipv6_projection_writes()
    baseline = _read_baseline(BASELINE)

    retired = sorted(set(baseline) - set(current))
    assert not retired, (
        "served-IPv6 writers were removed; delete them from the shrink-only "
        "baseline so it keeps describing real debt:\n  " + "\n  ".join(retired)
    )

    shrunk = sorted(
        f"{name}: baseline {baseline[name]}, now {current[name]}"
        for name in set(current) & set(baseline)
        if current[name] < baseline[name]
    )
    assert not shrunk, (
        "served-IPv6 write sites were removed; lower these baseline counts so "
        "the ratchet cannot be spent twice:\n  " + "\n  ".join(shrunk)
    )
