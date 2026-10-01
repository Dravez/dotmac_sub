"""Fail deployment safely when a required OpenBao boot secret is unavailable."""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from app.services.kernel_key_provider import KEYRING_REF
from app.services.kernel_secret_source import (
    OPTIONAL_SECRET_REFS,
    PREPAID_TRUST_PUBLIC_KEY_FILE_ENV,
    SECRET_REFS,
    load_prepaid_trust_public_key_file,
)
from app.services.prepaid_funding_attestation import TRUST_KEY_NAME
from app.services.secrets import resolve_openbao_ref

#: Material required by every deployed settings-write surface.
REQUIRED_REFS: Mapping[str, str] = {
    **SECRET_REFS,
    "settings_encryption_keyring": KEYRING_REF,
}

#: Material a deployment may legitimately not have. Reported, never gating —
#: see `report_optional_boot_material`.
OPTIONAL_REFS: Mapping[str, str] = {
    **OPTIONAL_SECRET_REFS,
}


def optional_refs_for_preflight() -> Mapping[str, str]:
    """Do not resolve the legacy trust ref when the local file is selected."""

    if not os.getenv(PREPAID_TRUST_PUBLIC_KEY_FILE_ENV, "").strip():
        return OPTIONAL_REFS
    return {
        name: reference
        for name, reference in OPTIONAL_REFS.items()
        if name != TRUST_KEY_NAME
    }


@dataclass(frozen=True, slots=True)
class BootSecretPreflightResult:
    """Names checked and names that could not provide a non-empty value."""

    checked_names: tuple[str, ...]
    failed_names: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not self.failed_names


def check_required_boot_secrets(
    refs: Mapping[str, str] = REQUIRED_REFS,
    resolver: Callable[[str], str] = resolve_openbao_ref,
) -> BootSecretPreflightResult:
    """Resolve required fields without returning or logging their values."""
    checked: list[str] = []
    failed: list[str] = []
    for name, reference in refs.items():
        checked.append(name)
        try:
            value = resolver(reference)
        except Exception:  # The deployment needs only the failed field name.
            failed.append(name)
            continue
        if not value.strip():
            failed.append(name)
    return BootSecretPreflightResult(tuple(checked), tuple(failed))


def report_optional_boot_material(
    refs: Mapping[str, str] = OPTIONAL_REFS,
    resolver: Callable[[str], str] = resolve_openbao_ref,
) -> tuple[str, ...]:
    """Optional names that resolve to nothing. Reported, never gating.

    These belong to ONE optional feature each. A deployment not using the
    feature has nothing to provision, so absence must not fail a deploy — the
    application makes the same distinction at boot.

    It is still worth SAYING, because the failure mode of a silently missing
    one is remote from its cause: prepaid manifest verification refuses every
    manifest later than this line.
    """
    absent: list[str] = []
    for name, reference in refs.items():
        try:
            value = resolver(reference)
        except Exception:  # Only the name; the reason may quote the payload.
            absent.append(name)
            continue
        if not value.strip():
            absent.append(name)
    return tuple(absent)


def main() -> int:
    result = check_required_boot_secrets()
    if not result.ok:
        names = ", ".join(result.failed_names)
        print(f"OpenBao boot-secret preflight failed for: {names}")
        return 1
    print(
        "OpenBao boot-secret preflight passed for "
        f"{len(result.checked_names)} required fields."
    )
    if os.getenv(PREPAID_TRUST_PUBLIC_KEY_FILE_ENV, "").strip():
        try:
            trust_key = load_prepaid_trust_public_key_file()
        except RuntimeError:
            print("Prepaid trust-key file preflight failed.")
            return 1
        if not trust_key:
            print("Prepaid trust-key file preflight failed: file is not configured.")
            return 1
        print("Local prepaid trust-key file preflight passed.")

    absent = report_optional_boot_material(optional_refs_for_preflight())
    if absent:
        print(
            "Optional boot material not provisioned (features using it will "
            f"report themselves unconfigured): {', '.join(absent)}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
