"""Sub's `SecretSource` — OpenBao, read once at startup.

ADR-0009 (`dotmac_starter_mt`): **a secret is held, never dereferenced.** Nothing
on a settings resolution path reaches a network, and a value that cannot be held
is not a setting. Material that lives in a secret store is read by the PRODUCT
and installed at boot; the kernel ships no client and never fetches.

This is that product half, over the OpenBao client Sub already has.

## Why the required five

Per the classification ruled on 2026-08-09, five specs stop being settings:

* `auth/credential_encryption_key`
* `auth/totp_encryption_key`
* `network/wireguard_key_encryption_key`

  Each encrypts data **in this same database**. Holding them as settings — even
  encrypted at rest — would put the key inside the store it protects, secured by
  another key facing the identical question. Sub's existing `bao://` handling for
  these is already correct; this moves *where the code looks*, not where the
  secret lives.

* `auth/jwt_secret`
* `radius/auth_shared_secret`

  Dotmac issues and rotates both, and both are boot-stable, so holding them in
  memory costs nothing on the read path and keeps rotation central.

The other seven (`smtp_password`, the geocoding keys, `meta_app_secret`, the
vLLM keys, `voice_transcription_api_key`) are third-party credentials Sub holds
a COPY of. Those become real settings encrypted at rest — a settings row gives
history, an actor and an admin surface that a vault path does not.

## Failure behaviour is the whole contract

`load` RAISES when OpenBao is unreachable. It must never return a partial or
empty mapping for that case: empty is indistinguishable from "nothing is
configured", and the kernel would install it as a successful load. So this uses
`resolve_openbao_ref`, which raises, and NOT `get_secret`, which swallows every
exception and returns a default — convenient for a caller with a fallback,
catastrophic for a source whose silence the kernel would trust.

`install_secret_source` then fails the boot rather than starting with secrets it
could not fetch. That is deliberate: a process that starts without them would
discover it at the first request that needed one.
"""

from __future__ import annotations

import logging
import os
import stat
from collections.abc import Mapping
from pathlib import Path

from app.config import settings
from app.services.secrets import (
    is_openbao_configured,
    resolve_openbao_ref,
    resolve_openbao_ref_optional,
)

logger = logging.getLogger(__name__)

# Secret name -> the OpenBao reference holding it. The names are what
# application code asks for via `get_secret(name)`; the references are where
# this deployment keeps them, and are the only place those paths appear.
SECRET_REFS: Mapping[str, str] = {
    "credential_encryption_key": "bao://secret/settings/auth#credential_encryption_key",
    "totp_encryption_key": "bao://secret/settings/auth#totp_encryption_key",
    "wireguard_key_encryption_key": (
        "bao://secret/settings/network#wireguard_key_encryption_key"
    ),
    "jwt_secret": "bao://secret/settings/auth#jwt_secret",
    "radius_auth_shared_secret": "bao://secret/settings/radius#auth_shared_secret",
}

#: OpenBao material that is held the same way but whose ABSENCE is a legitimate
#: state, so a missing path must not fail the boot.
#:
#: The required set above is all-or-nothing on purpose: those five are needed by
#: every process, so a partial load is a reason to stop. These are needed by ONE
#: feature, and a deployment that does not use that feature has nothing to
#: provision. Failing every boot over a dormant feature's material would be a
#: worse answer than the feature reporting itself unconfigured when asked.
#:
#: The distinction is still the strict one: a MISSING path means not
#: provisioned; an unreachable store, a bad token or a missing field on a path
#: that does exist all still raise — see `secrets.resolve_openbao_ref_optional`.
#:
#: **`machine_credential_hmac_key`** — what `dotmac_kernel.machine_auth` hashes
#: every presented key with. It belongs with the three encryption keys above by
#: the same reasoning: it protects `machine_credentials` rows in THIS database,
#: so storing it here would put the lock beside the door. It is deliberately its
#: own key rather than a subkey derived from `credential_encryption_key` the way
#: `_api_key_hmac_secret` does it — deriving one from the other couples two
#: rotations that have nothing to do with each other, and rotating connector
#: encryption would invalidate every machine credential at the same instant.
#:
#: OPTIONAL *for now*, and the reason is the migration state rather than the end
#: state. No credential has been minted yet, so a deployment without this key is
#: not broken — it has a dormant feature, and `_machine_principal` skips to the
#: legacy verifier. Requiring it today would mean a deploy that reached the
#: registry before the operator reached OpenBao takes the whole application down
#: over a feature with zero rows.
#:
#: It MOVES to `SECRET_REFS` in the retirement change that deletes the legacy
#: branch. There machine auth is the only way an integration authenticates,
#: absence stops being "dormant" and becomes "every integration fails closed",
#: and a boot that refuses is the correct answer. The gate in
#: `_machine_principal` goes in that same change: it is live code only while
#: absence is legitimate, and dead defensive code afterwards.
#:
#: While optional, the key deliberately has its own OpenBao path. The optional
#: resolver recognizes a missing PATH as "not provisioned" but treats a missing
#: FIELD on an existing path as configuration drift and raises. Co-locating this
#: field with the required `settings/auth` payload would therefore make its
#: documented legitimate absence fail every application boot.
#:
#: **`conversion_ingest_api_key`** is stable HMAC material for the optional
#: Fiber marketing conversion projection. A deployment without Fiber
#: acquisition may omit its entire path. Once provisioned, a missing field or
#: unreachable store remains a boot failure rather than silently changing the
#: derived subject identity.
OPTIONAL_SECRET_REFS: Mapping[str, str] = {
    "machine_credential_hmac_key": (
        "bao://secret/settings/machine_auth#machine_credential_hmac_key"
    ),
    "conversion_ingest_api_key": (
        "bao://secret/settings/marketing#conversion_ingest_api_key"
    ),
}

PREPAID_PUBLIC_KEY_FILE_ENV = "PREPAID_RECONSTRUCTION_PUBLIC_KEY_FILE"


def load_prepaid_public_key_file() -> str | None:
    """Read the configured deployment public authority key, if present.

    The path is deployment-owned and the file is mounted read-only into app
    containers. Refuse relative paths, symlinks, non-regular files and files
    writable by group/other: this key is public, but controls which funding
    manifests the application accepts.
    """

    raw_path = settings.prepaid_reconstruction_public_key_file
    if not raw_path:
        return None
    path = Path(raw_path)
    if not path.is_absolute():
        raise RuntimeError(
            f"{PREPAID_PUBLIC_KEY_FILE_ENV} must be an absolute file path"
        )
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise RuntimeError(
            "prepaid reconstruction public key file is unavailable"
        ) from exc
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_mode & 0o022:
            raise RuntimeError(
                "prepaid reconstruction public key must be a regular, "
                "non-group/world-writable file"
            )
        with os.fdopen(descriptor, "r", encoding="utf-8") as stream:
            descriptor = -1
            value = stream.read()
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if not value.strip():
        raise RuntimeError("prepaid reconstruction public key file is empty")
    return value


class OpenBaoSecretSource:
    """Loads OpenBao boot material and the optional local public authority key.

    Satisfies `dotmac_kernel.secret_sources.SecretSource` structurally — the
    kernel declares the protocol and holds the result; it never learns what
    OpenBao is, and takes no dependency on this client.
    """

    def __init__(
        self,
        refs: Mapping[str, str] | None = None,
        optional_refs: Mapping[str, str] | None = None,
    ) -> None:
        self._refs = dict(refs if refs is not None else SECRET_REFS)
        self._optional_refs = dict(
            optional_refs if optional_refs is not None else OPTIONAL_SECRET_REFS
        )

    def load(self) -> Mapping[str, str]:
        """Every secret this deployment holds, by name.

        Raises rather than returning a partial mapping for the REQUIRED set: a
        missing secret and an unreachable store are both reasons to stop, and
        the kernel treats a successful return as the complete set.

        The optional set is the one exception, and it is narrow: a path that
        does not exist is skipped, because material for a feature this
        deployment does not use was never going to be there. Every other
        failure still raises, so "unreachable" can never be read as "not
        provisioned".
        """
        loaded: dict[str, str] = {}
        for name, reference in self._refs.items():
            # `resolve_openbao_ref` raises; `get_secret` would swallow and
            # return a default, which the kernel would install as success.
            loaded[name] = resolve_openbao_ref(reference)
        skipped: list[str] = []
        for name, reference in self._optional_refs.items():
            value = resolve_openbao_ref_optional(reference)
            if value is None:
                skipped.append(name)
                continue
            loaded[name] = value
        public_key = load_prepaid_public_key_file()
        if public_key is not None:
            loaded["prepaid_attestation_public_key"] = public_key
        logger.info(
            "Loaded %d boot secret(s) from OpenBao; local prepaid public key %s%s",
            len(loaded),
            "loaded" if public_key is not None else "not provisioned",
            f"; optional OpenBao material absent: {', '.join(sorted(skipped))}"
            if skipped
            else "",
        )
        return loaded


class LocalPrepaidTrustAnchorSource:
    """Install only the prepaid verification key from its mounted local file."""

    def load(self) -> Mapping[str, str]:
        public_key = load_prepaid_public_key_file()
        if public_key is None:
            raise RuntimeError(
                f"{PREPAID_PUBLIC_KEY_FILE_ENV} is required for local authority"
            )
        return {"prepaid_attestation_public_key": public_key}


def install() -> tuple[str, ...]:
    """Install the configured source at startup; return names, never values."""
    from dotmac_kernel.secret_sources import install_secret_source

    if is_openbao_configured():
        return install_secret_source(OpenBaoSecretSource())
    return install_secret_source(LocalPrepaidTrustAnchorSource())


def install_if_configured() -> tuple[str, ...]:
    """Install held boot material when OpenBao or the local public key is set.

    The OpenBao gate reads configuration and performs no I/O — it does not
    probe reachability. A configured but unreachable OpenBao still fails boot;
    with no OpenBao, the local public trust file can be installed by itself.

    When neither source is configured, consumers keep their existing
    environment fallbacks. When OpenBao is configured but unreachable, loading
    raises and the boot fails rather than installing an incomplete secret set.
    """

    if (
        not is_openbao_configured()
        and not settings.prepaid_reconstruction_public_key_file
    ):
        logger.info(
            "No OpenBao configured; holding no boot secrets "
            "(readers fall back to their environment variables)"
        )
        return ()
    return install()
