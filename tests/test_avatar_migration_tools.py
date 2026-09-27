"""Guarded avatar backfill and report-only orphan evidence."""

from __future__ import annotations

import hashlib
import json
from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.services import avatar
from scripts.migration import migrate_subscriber_avatars as migration
from scripts.migration import report_avatar_orphans as orphan_report

_PNG = b"\x89PNG\r\n\x1a\n" + b"verified-image"


def test_source_inventory_requires_safe_basename_and_real_image(tmp_path: Path) -> None:
    owner = uuid4()
    (tmp_path / "safe.png").write_bytes(_PNG)
    prefix = "/static/avatars/"
    ready = migration.inspect_avatar_source(
        subscriber_id=owner,
        old_url=prefix + "safe.png",
        root=tmp_path,
        prefix=prefix,
    )
    assert ready.status == "ready"
    assert ready.sha256 == hashlib.sha256(_PNG).hexdigest()

    missing = migration.inspect_avatar_source(
        subscriber_id=owner,
        old_url=prefix + "missing.png",
        root=tmp_path,
        prefix=prefix,
    )
    assert missing.status == "missing"
    assert missing.sha256 is None

    for unsafe_url in (
        prefix + "../safe.png",
        prefix + "sub/safe.png",
        prefix + "%2e%2e.png",
        "https://example.test/safe.png",
    ):
        assert (
            migration.inspect_avatar_source(
                subscriber_id=owner,
                old_url=unsafe_url,
                root=tmp_path,
                prefix=prefix,
            ).status
            == "unsafe"
        )

    (tmp_path / "alias.png").symlink_to(tmp_path / "safe.png")
    assert (
        migration.inspect_avatar_source(
            subscriber_id=owner,
            old_url=prefix + "alias.png",
            root=tmp_path,
            prefix=prefix,
        ).status
        == "unsafe"
    )
    (tmp_path / "bad.png").write_bytes(b"not a png")
    assert (
        migration.inspect_avatar_source(
            subscriber_id=owner,
            old_url=prefix + "bad.png",
            root=tmp_path,
            prefix=prefix,
        ).status
        == "unsafe"
    )


def test_manifest_is_digest_bound_and_requires_private_file(tmp_path: Path) -> None:
    owner = uuid4()
    (tmp_path / "safe.png").write_bytes(_PNG)
    prefix = "/static/avatars/"
    row = migration.inspect_avatar_source(
        subscriber_id=owner,
        old_url=prefix + "safe.png",
        root=tmp_path,
        prefix=prefix,
    )
    manifest = {
        "version": 1,
        "source_dir": str(tmp_path),
        "legacy_url_prefix": prefix,
        "rows": [{**row.__dict__, "subscriber_id": str(owner)}],
    }
    path = tmp_path / "manifest.json"
    raw = json.dumps(manifest).encode()
    path.write_bytes(raw)
    path.chmod(0o600)
    digest = hashlib.sha256(raw).hexdigest()
    loaded = migration._load_manifest(path, digest, tmp_path, prefix)
    assert loaded.rows == (row,)
    with pytest.raises(migration.MigrationInputError, match="SHA-256"):
        migration._load_manifest(path, "0" * 64, tmp_path, prefix)
    path.chmod(0o644)
    with pytest.raises(migration.MigrationInputError, match="0600"):
        migration._load_manifest(path, digest, tmp_path, prefix)


def test_backfill_passes_exact_cas_and_verifies_digest(
    tmp_path: Path, monkeypatch
) -> None:
    owner = uuid4()
    (tmp_path / "safe.png").write_bytes(_PNG)
    prefix = "/static/avatars/"
    row = migration.inspect_avatar_source(
        subscriber_id=owner,
        old_url=prefix + "safe.png",
        root=tmp_path,
        prefix=prefix,
    )
    manifest = migration.AvatarInventory(1, str(tmp_path), prefix, (row,))
    captured: list[avatar.UploadAvatarCommand] = []
    monkeypatch.setattr(migration, "_current_url", lambda subscriber_id: row.old_url)
    monkeypatch.setattr(migration, "SessionLocal", lambda: nullcontext(object()))
    monkeypatch.setattr(migration, "_verify_selected", lambda *args: True)

    def upload(db, command):
        captured.append(command)
        return avatar.AvatarOutcome(avatar_url=avatar.avatar_url_for_file(uuid4()))

    monkeypatch.setattr(migration.avatar, "upload_avatar", upload)
    result = migration.apply_inventory(
        manifest, root=tmp_path, actor="operator:reviewed", reason="legacy backfill"
    )
    assert result[0]["status"] == "verified"
    assert captured[0].subscriber_id == owner
    assert captured[0].expected_legacy_url == row.old_url
    assert captured[0].data == _PNG
    assert captured[0].context.actor == "operator:reviewed"


def test_orphan_report_preserves_shared_key_with_active_reference(monkeypatch) -> None:
    now = datetime.now(UTC)
    keys = ("avatars/shared.png", "avatars/unknown.png")

    class Client:
        def list_objects_v2(self, **kwargs):
            return {
                "Contents": [
                    {"Key": key, "LastModified": now - timedelta(hours=48)}
                    for key in keys
                ],
                "IsTruncated": False,
            }

    class QueryResult:
        def all(self):
            return [(keys[0], 1, 1)]

    class Db:
        def execute(self, statement):
            return QueryResult()

    monkeypatch.setattr(
        orphan_report,
        "get_s3_storage",
        lambda: SimpleNamespace(bucket_name="private", client=Client()),
    )
    monkeypatch.setattr(orphan_report, "SessionLocal", lambda: nullcontext(Db()))
    monkeypatch.setattr(orphan_report, "finish_read_transaction", lambda db: None)
    evidence, truncated = orphan_report.report_avatar_orphans(
        max_keys=2, min_age_hours=24
    )
    assert truncated is False
    assert evidence[0].active_references == 1
    assert evidence[0].inactive_references == 1
    assert evidence[0].disposition == "active_reference"
    assert evidence[0].key_sha256 == hashlib.sha256(keys[0].encode()).hexdigest()
    assert not hasattr(evidence[0], "key")
    assert evidence[1].disposition == "investigate_shared_or_inflight_key"


def test_orphan_report_never_ages_naive_timestamp(monkeypatch) -> None:
    class Client:
        def list_objects_v2(self, **kwargs):
            return {
                "Contents": [
                    {"Key": "avatars/unknown.png", "LastModified": datetime.now()}
                ],
                "IsTruncated": False,
            }

    class QueryResult:
        def all(self):
            return []

    class Db:
        def execute(self, statement):
            return QueryResult()

    monkeypatch.setattr(
        orphan_report,
        "get_s3_storage",
        lambda: SimpleNamespace(bucket_name="private", client=Client()),
    )
    monkeypatch.setattr(orphan_report, "SessionLocal", lambda: nullcontext(Db()))
    monkeypatch.setattr(orphan_report, "finish_read_transaction", lambda db: None)
    evidence, _ = orphan_report.report_avatar_orphans(max_keys=1, min_age_hours=24)
    assert evidence[0].age_hours is None
    assert evidence[0].disposition == "too_recent_or_unaged"
