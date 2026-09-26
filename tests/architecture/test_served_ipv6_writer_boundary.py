"""Shrink-only guard on writers of the served IPv6 projection.

``network.ip_assignment_lifecycle`` owns the desired IPv6 at exact service
grain. ``Subscription.ipv6_address`` is a compatibility projection of the
active ``IPAssignment`` — see ``app/models/catalog.py`` and
``docs/designs/IP_ASSIGNMENT_LIFECYCLE_SOT.md``. Unlike IPv4, the IPv6
projection is NOT written by ``connectivity_reconciler.py`` (that module only
ever assigns ``subscription.ipv4_address``); the IPv6 allocator instead lives
in ``provisioning_helpers`` via ``setattr(subscription,
f"{version_key}_address", ...)`` — see the blind-spot note in
``served_ipv6_writer_baseline.txt``.

This is the IPv6 twin of ``test_served_ipv4_writer_boundary.py``: same owner,
same projection shape, same shrink-only ratchet, kept as a separate baseline
because the two attributes have independent (and currently different) writer
sets.

This guard does not claim the boundary is in place. It freezes the remaining
direct writers so a change cannot add another one while the cutover lands.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from scripts.architecture.sot_debt import (
    served_ipv4_projection_writes,
    served_ipv6_projection_writes,
)

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


# The scanner counts only direct ``<x>.<attribute> = ...``-shaped assignments
# (see ``_served_projection_writes``'s docstring). This proof plants exactly
# one true write alongside three near-misses in a synthetic tree and asserts
# the scanner names the write and nothing else — shared here for both served
# attributes since it is the same detection rule for each.
@pytest.mark.parametrize(
    ("attribute", "scanner"),
    [
        ("ipv4_address", served_ipv4_projection_writes),
        ("ipv6_address", served_ipv6_projection_writes),
    ],
)
def test_scanner_counts_only_direct_attribute_assignment(
    tmp_path: Path,
    attribute: str,
    scanner: Callable[..., dict[str, int]],
) -> None:
    planted = tmp_path / "planted_writer.py"
    planted.write_text(
        f"""
def touch(sub, other):
    sub.{attribute} = "planted"  # the one true write
    sub.{attribute}_id = "not-the-projection"  # near-miss: different attribute
    y = sub.{attribute}  # near-miss: a read, not a write
    if sub.{attribute} == other:  # near-miss: a comparison, not a write
        pass
""",
        encoding="utf-8",
    )

    counts = scanner(app_dir=tmp_path, project_root=tmp_path)

    assert counts == {"planted_writer.py": 1}
