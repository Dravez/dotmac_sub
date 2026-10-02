#!/usr/bin/env python3
"""Inventory legacy avatar files, or apply an exact reviewed inventory.

Dry-run prints a JSON manifest and never writes DB or S3. Apply requires that
manifest's SHA-256, an explicit source directory, actor, reason, and acknowledgement
of unresolved files. No mode deletes local files or S3 objects.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
from dataclasses import asdict, dataclass
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy import select

from app.config import settings
from app.db import SessionLocal, finish_read_transaction
from app.models.subscriber import Subscriber
from app.services import avatar
from app.services.file_storage import FileValidationError, file_uploads
from app.services.owner_commands import CommandContext

_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,198}\.(?:jpe?g|png|gif|webp)$", re.I)
_MIME = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".webp": "image/webp",
}
_MAX_ROWS = 1000


class MigrationInputError(ValueError):
    """Unsafe or stale operator input."""


@dataclass(frozen=True)
class AvatarInventoryRow:
    subscriber_id: UUID
    old_url: str
    status: str
    filename: str | None = None
    sha256: str | None = None
    byte_count: int | None = None
    content_type: str | None = None
    reason: str | None = None


@dataclass(frozen=True)
class AvatarInventory:
    version: int
    source_dir: str
    legacy_url_prefix: str
    rows: tuple[AvatarInventoryRow, ...]


def _source_root(value: str) -> Path:
    root = Path(value)
    if not root.is_absolute() or root.is_symlink() or not root.is_dir():
        raise MigrationInputError(
            "source directory must be an existing absolute directory"
        )
    return root.resolve(strict=True)


def _legacy_prefix() -> str:
    prefix = settings.avatar_url_prefix.rstrip("/") + "/"
    if not prefix.startswith("/") or "?" in prefix or "#" in prefix or ".." in prefix:
        raise MigrationInputError("legacy avatar URL prefix is unsafe")
    return prefix


def _filename(old_url: str, prefix: str) -> str:
    if not old_url.startswith(prefix):
        raise MigrationInputError("URL does not use the configured legacy prefix")
    name = old_url[len(prefix) :]
    if (
        not _NAME.fullmatch(name)
        or ".." in name
        or "%" in name
        or "\\" in name
        or "/" in name
    ):
        raise MigrationInputError("legacy URL does not contain one safe image basename")
    return name


def _read_source(root: Path, name: str) -> tuple[bytes, str]:
    # O_NOFOLLOW plus one checked basename prevent symlink and traversal reads.
    if not hasattr(os, "O_NOFOLLOW"):
        raise MigrationInputError("platform cannot refuse symlink source files")
    flags = os.O_RDONLY | os.O_NOFOLLOW
    try:
        fd = os.open(root / name, flags)
    except FileNotFoundError as exc:
        raise MigrationInputError("source file missing") from exc
    except OSError as exc:
        raise MigrationInputError("source file cannot be opened safely") from exc
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise MigrationInputError("source is not a regular file")
        config = file_uploads.get_domain_config("avatars")
        limit = min(config.max_size_bytes, settings.avatar_max_size_bytes)
        if info.st_size > limit:
            raise MigrationInputError("source file exceeds avatar size policy")
        with os.fdopen(fd, "rb", closefd=False) as handle:
            data = handle.read(limit + 1)
        if len(data) > limit:
            raise MigrationInputError("source file exceeds avatar size policy")
    finally:
        os.close(fd)
    mime = _MIME[Path(name).suffix.lower()]
    if mime not in set(settings.avatar_allowed_types.split(",")):
        raise MigrationInputError("source MIME is disallowed by runtime policy")
    try:
        file_uploads.validate(
            config=file_uploads.get_domain_config("avatars"),
            filename=name,
            content_type=mime,
            data=data,
        )
    except FileValidationError as exc:
        raise MigrationInputError("source image signature or MIME invalid") from exc
    return data, mime


def inspect_avatar_source(
    *, subscriber_id: UUID, old_url: str, root: Path, prefix: str
) -> AvatarInventoryRow:
    try:
        name = _filename(old_url, prefix)
        data, mime = _read_source(root, name)
    except MigrationInputError as exc:
        return AvatarInventoryRow(
            subscriber_id=subscriber_id,
            old_url=old_url,
            status="missing" if str(exc) == "source file missing" else "unsafe",
            reason=str(exc),
        )
    return AvatarInventoryRow(
        subscriber_id=subscriber_id,
        old_url=old_url,
        status="ready",
        filename=name,
        sha256=hashlib.sha256(data).hexdigest(),
        byte_count=len(data),
        content_type=mime,
    )


def inventory(*, root: Path, prefix: str, limit: int) -> AvatarInventory:
    escaped = prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    with SessionLocal() as db:
        rows = db.execute(
            select(Subscriber.id, Subscriber.avatar_url)
            .where(Subscriber.avatar_url.like(f"{escaped}%", escape="\\"))
            .order_by(Subscriber.id)
            .limit(limit + 1)
        ).all()
        finish_read_transaction(db)
    if len(rows) > limit:
        raise MigrationInputError(
            "legacy URL count exceeds limit; review a bounded cohort"
        )
    return AvatarInventory(
        version=1,
        source_dir=str(root),
        legacy_url_prefix=prefix,
        rows=tuple(
            inspect_avatar_source(
                subscriber_id=subscriber_id,
                old_url=old_url,
                root=root,
                prefix=prefix,
            )
            for subscriber_id, old_url in rows
        ),
    )


def _load_manifest(path: Path, digest: str, root: Path, prefix: str) -> AvatarInventory:
    if path.is_symlink() or not path.is_file():
        raise MigrationInputError("manifest must be a regular, non-symlink file")
    if stat.S_IMODE(path.stat().st_mode) != 0o600:
        raise MigrationInputError("manifest must have mode 0600")
    raw = path.read_bytes()
    if len(raw) > 2_000_000 or hashlib.sha256(raw).hexdigest() != digest:
        raise MigrationInputError("manifest size or SHA-256 mismatch")
    try:
        payload = json.loads(raw)
        if (
            payload["version"] != 1
            or payload["source_dir"] != str(root)
            or payload["legacy_url_prefix"] != prefix
            or not isinstance(payload["rows"], list)
            or len(payload["rows"]) > _MAX_ROWS
        ):
            raise MigrationInputError("manifest scope or version mismatch")
        rows = tuple(
            AvatarInventoryRow(
                subscriber_id=UUID(item["subscriber_id"]),
                old_url=item["old_url"],
                status=item["status"],
                filename=item.get("filename"),
                sha256=item.get("sha256"),
                byte_count=item.get("byte_count"),
                content_type=item.get("content_type"),
                reason=item.get("reason"),
            )
            for item in payload["rows"]
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise MigrationInputError("manifest structure invalid") from exc
    if len({row.subscriber_id for row in rows}) != len(rows):
        raise MigrationInputError("manifest repeats a subscriber")
    for row in rows:
        if not isinstance(row.old_url, str) or not row.old_url.startswith(prefix):
            raise MigrationInputError("manifest row is outside the legacy URL prefix")
        if row.status not in {"ready", "missing", "unsafe"}:
            raise MigrationInputError("manifest has unknown row status")
        if row.status == "ready" and (
            row.filename != _filename(row.old_url, prefix)
            or not isinstance(row.sha256, str)
            or not re.fullmatch(r"[0-9a-f]{64}", row.sha256)
            or not isinstance(row.byte_count, int)
            or row.byte_count < 0
            or row.content_type != _MIME[Path(row.filename).suffix.lower()]
        ):
            raise MigrationInputError("manifest ready row has invalid source proof")
    return AvatarInventory(1, str(root), prefix, rows)


def _current_url(subscriber_id: UUID) -> str | None:
    with SessionLocal() as db:
        subscriber = db.get(Subscriber, subscriber_id)
        current = subscriber.avatar_url if subscriber is not None else None
        finish_read_transaction(db)
        return current


def _verify_selected(subscriber_id: UUID, url: str, digest: str) -> bool:
    file_id = avatar.file_id_from_avatar_url(url)
    if file_id is None:
        return False
    with SessionLocal() as db:
        record = avatar.resolve_public_avatar(db, file_id)
        if record is None or record.owner_subscriber_id != subscriber_id:
            finish_read_transaction(db)
            return False
        db.expunge(record)
        finish_read_transaction(db)
    stream = file_uploads.stream_file(record)
    calculated = hashlib.sha256()
    count = 0
    for chunk in stream.chunks:
        count += len(chunk)
        if count > settings.avatar_max_size_bytes:
            return False
        calculated.update(chunk)
    return calculated.hexdigest() == digest and count == record.file_size


def apply_inventory(
    manifest: AvatarInventory, *, root: Path, actor: str, reason: str
) -> tuple[dict[str, str], ...]:
    results: list[dict[str, str]] = []
    for row in manifest.rows:
        item = {"subscriber_id": str(row.subscriber_id), "status": row.status}
        if row.status != "ready":
            results.append(item)
            continue
        if not row.filename or not row.sha256 or not row.content_type:
            raise MigrationInputError("ready row is missing source proof")
        current = _current_url(row.subscriber_id)
        if current != row.old_url:
            item["status"] = (
                "already_migrated"
                if current
                and current.startswith(avatar.AVATAR_URL_PREFIX)
                and _verify_selected(row.subscriber_id, current, row.sha256)
                else "changed"
            )
            results.append(item)
            continue
        try:
            data, mime = _read_source(root, row.filename)
        except MigrationInputError:
            item["status"] = "source_changed"
            results.append(item)
            continue
        if (
            hashlib.sha256(data).hexdigest() != row.sha256
            or len(data) != row.byte_count
            or mime != row.content_type
        ):
            item["status"] = "source_changed"
            results.append(item)
            continue
        command_id = uuid4()
        command = avatar.UploadAvatarCommand(
            context=CommandContext(
                command_id=command_id,
                correlation_id=command_id,
                actor=actor,
                scope=f"subscriber:{row.subscriber_id}",
                reason=reason,
                idempotency_key=f"avatar-legacy:{row.subscriber_id}:{row.sha256}",
            ),
            subscriber_id=row.subscriber_id,
            filename=row.filename,
            content_type=mime,
            data=data,
            expected_legacy_url=row.old_url,
        )
        try:
            with SessionLocal() as db:
                outcome = avatar.upload_avatar(db, command)
        except avatar.AvatarError as exc:
            if exc.code != "customer.avatar.stale_legacy_url":
                raise
            item["status"] = "changed"
            results.append(item)
            continue
        if outcome.avatar_url is None:
            raise MigrationInputError("avatar owner returned no selected URL")
        item["avatar_url"] = outcome.avatar_url
        item["status"] = (
            "verified"
            if _verify_selected(row.subscriber_id, outcome.avatar_url, row.sha256)
            else "verification_failed"
        )
        results.append(item)
    return tuple(results)


def _emit(payload: object) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True, default=str))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", required=True)
    parser.add_argument("--limit", type=int, default=_MAX_ROWS)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--manifest-sha256")
    parser.add_argument("--actor")
    parser.add_argument("--reason")
    parser.add_argument("--allow-partial", action="store_true")
    args = parser.parse_args()
    if not 1 <= args.limit <= _MAX_ROWS:
        parser.error("--limit must be between 1 and 1000")
    if args.apply and not all(
        (args.manifest, args.manifest_sha256, args.actor, args.reason)
    ):
        parser.error(
            "--apply requires --manifest, --manifest-sha256, --actor, --reason"
        )
    root = _source_root(args.source_dir)
    prefix = _legacy_prefix()
    if not args.apply:
        result = inventory(root=root, prefix=prefix, limit=args.limit)
        _emit(asdict(result))
        return 0 if all(row.status == "ready" for row in result.rows) else 2
    manifest = _load_manifest(args.manifest, args.manifest_sha256, root, prefix)
    if not args.allow_partial and any(row.status != "ready" for row in manifest.rows):
        raise MigrationInputError(
            "manifest has unresolved files; review and pass --allow-partial to continue"
        )
    results = apply_inventory(manifest, root=root, actor=args.actor, reason=args.reason)
    _emit({"version": 1, "results": results})
    return (
        0
        if all(item["status"] in {"verified", "already_migrated"} for item in results)
        else 2
    )


if __name__ == "__main__":
    raise SystemExit(main())
