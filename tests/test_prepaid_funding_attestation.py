"""Trust-anchor requirements for final prepaid reconstruction.

The anchor decides whether a signed reconstruction manifest is believed. In
the local-source configuration it is loaded from a root-managed, read-only
deployment file rather than a database setting or OpenBao reference.

It used to be a `billing` setting holding a `bao://` reference, guarded by
"must be an OpenBao reference". These tests pinned that guard, and the guard was
weaker than it looked: it checked the value WAS a reference and never WHICH
reference, so anyone able to write settings could aim it at a key they
controlled. The local trust file cannot be selected through the settings
surface.
"""

import pytest
from dotmac_kernel.secret_sources import clear_secret_source, install_secret_source

from app.services import prepaid_funding_attestation

PUBLIC_KEY = "-----BEGIN PUBLIC KEY-----\nconfigured\n-----END PUBLIC KEY-----\n"


@pytest.fixture
def held_anchor():
    class _Held:
        def load(self) -> dict[str, str]:
            return {prepaid_funding_attestation.TRUST_KEY_NAME: PUBLIC_KEY}

    install_secret_source(_Held())
    yield
    clear_secret_source()


@pytest.fixture
def nothing_held():
    clear_secret_source()
    yield
    clear_secret_source()


def test_the_anchor_comes_from_the_held_material(db_session, held_anchor):
    assert (
        prepaid_funding_attestation.resolve_trusted_public_key_pem(db_session)
        == PUBLIC_KEY.strip()
    )


def test_an_unheld_anchor_fails_verification_rather_than_the_boot(
    db_session, nothing_held
):
    """Unconfigured behaves exactly as it did: loudly, at use.

    The anchor is in the OPTIONAL held set, so a deployment not using prepaid
    reconstruction boots without it. What must not happen is verification
    quietly succeeding, or the anchor falling back to something a caller can
    influence.
    """

    with pytest.raises(ValueError, match="no reconstruction attestation public key"):
        prepaid_funding_attestation.resolve_trusted_public_key_pem(db_session)


def test_no_settings_row_can_supply_the_anchor(db_session, nothing_held):
    """The point of the move, pinned.

    A `billing` row named like the retired setting must not become the anchor
    again — the settings surface is exactly what an operator with settings-write
    access can reach, and this value is not theirs to change.
    """

    from app.models.domain_settings import SettingDomain
    from app.models.subscription_engine import SettingValueType
    from app.schemas.settings import DomainSettingCreate
    from app.services import domain_settings as domain_settings_service

    domain_settings_service.DomainSettings(domain=SettingDomain.billing).create(
        db_session,
        DomainSettingCreate(
            domain=SettingDomain.billing,
            key="prepaid_reconstruction_attestation_public_key_ref",
            value_type=SettingValueType.string,
            value_text="bao://secret/attacker#public_key_pem",
        ),
    )

    with pytest.raises(ValueError, match="no reconstruction attestation public key"):
        prepaid_funding_attestation.resolve_trusted_public_key_pem(db_session)


def test_signing_key_must_match_the_configured_public_trust_anchor() -> None:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    signing_key = Ed25519PrivateKey.generate()
    other_key = Ed25519PrivateKey.generate()
    private_pem = signing_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("ascii")
    matching_public_pem = (
        signing_key.public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode("ascii")
    )
    other_public_pem = (
        other_key.public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode("ascii")
    )

    assert prepaid_funding_attestation.signing_key_matches_public_key(
        private_pem,
        matching_public_pem,
    )
    assert not prepaid_funding_attestation.signing_key_matches_public_key(
        private_pem,
        other_public_pem,
    )


def test_exporter_reads_an_owner_only_file_and_checks_the_matching_anchor(
    tmp_path,
) -> None:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from scripts.one_off.export_prepaid_funding_snapshot import _resolve_signing_key

    signing_key = Ed25519PrivateKey.generate()
    private_file = tmp_path / "signing.pem"
    public_file = tmp_path / "trusted-public.pem"
    private_pem = signing_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("ascii")
    public_pem = (
        signing_key.public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode("ascii")
    )
    private_file.write_text(private_pem, encoding="utf-8")
    public_file.write_text(public_pem, encoding="utf-8")
    private_file.chmod(0o600)
    public_file.chmod(0o644)

    assert _resolve_signing_key(private_file, public_file) == private_pem

    private_file.chmod(0o644)
    with pytest.raises(RuntimeError, match="owner-only"):
        _resolve_signing_key(private_file, public_file)
