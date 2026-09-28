"""Canonical subscriber avatar commands and public asset resolution."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models.stored_file import StoredFile
from app.models.subscriber import Subscriber
from app.services.domain_errors import DomainError
from app.services.events import emit_event
from app.services.events.types import EventType
from app.services.file_storage import FileValidationError, file_uploads
from app.services.owner_commands import (
    CommandContext,
    OwnerCommandDefinition,
    execute_owner_command,
)

AVATAR_URL_PREFIX = "/avatars/"
_ENTITY_TYPE = "subscriber_avatar"
_MIME_BY_EXTENSION = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".webp": "image/webp",
}


def require_compatible_avatar_policy() -> None:
    """Fail startup when legacy overrides exceed the durable avatar contract."""
    domain = file_uploads.get_domain_config("avatars")
    configured_types = settings.avatar_allowed_types.split(",")
    if (
        settings.avatar_max_size_bytes < 1
        or settings.avatar_max_size_bytes > domain.max_size_bytes
        or not configured_types
        or any(value not in domain.allowed_mime_types for value in configured_types)
        or settings.avatar_url_prefix != "/static/avatars"
    ):
        raise RuntimeError(
            "AVATAR_* policy is incompatible with the durable avatar domain; "
            "review docs/storage_s3.md before startup"
        )


_UPLOAD = OwnerCommandDefinition(
    owner="customer.avatar",
    concern="subscriber avatar selection and durable metadata",
    name="upload_subscriber_avatar",
)
_REMOVE = OwnerCommandDefinition(
    owner="customer.avatar",
    concern="subscriber avatar selection and durable metadata",
    name="remove_subscriber_avatar",
)


class AvatarError(DomainError):
    """Avatar command or public-resolution failure."""


@dataclass(frozen=True)
class UploadAvatarCommand:
    context: CommandContext
    subscriber_id: UUID
    filename: str
    content_type: str | None
    data: bytes
    expected_legacy_url: str | None = None
    uploaded_by: UUID | None = None


@dataclass(frozen=True)
class RemoveAvatarCommand:
    context: CommandContext
    subscriber_id: UUID


@dataclass(frozen=True)
class AvatarOutcome:
    avatar_url: str | None


def avatar_url_for_file(file_id: UUID) -> str:
    return f"{AVATAR_URL_PREFIX}{file_id}"


def file_id_from_avatar_url(value: str | None) -> UUID | None:
    if not value or not value.startswith(AVATAR_URL_PREFIX):
        return None
    try:
        return UUID(value[len(AVATAR_URL_PREFIX) :])
    except ValueError:
        return None


def _locked_subscriber(db: Session, subscriber_id: UUID) -> Subscriber:
    subscriber = db.scalar(
        select(Subscriber).where(Subscriber.id == subscriber_id).with_for_update()
    )
    if subscriber is None:
        raise AvatarError(
            code="customer.avatar.subscriber_missing", message="User not found"
        )
    return subscriber


def _retire_previous(db: Session, avatar_url: str | None, subscriber_id: UUID) -> None:
    file_id = file_id_from_avatar_url(avatar_url)
    if file_id is None:
        return  # Legacy /static/avatars URLs stay readable during migration.
    record = db.get(StoredFile, file_id)
    if (
        record is not None
        and not record.is_deleted
        and record.entity_type == _ENTITY_TYPE
        and record.entity_id == str(subscriber_id)
        and record.owner_subscriber_id == subscriber_id
    ):
        file_uploads.stage_soft_delete(db=db, file=record)


def upload_avatar(db: Session, command: UploadAvatarCommand) -> AvatarOutcome:
    return execute_owner_command(
        db,
        definition=_UPLOAD,
        context=command.context,
        operation=lambda: _upload_avatar(db, command),
    )


def _upload_avatar(db: Session, command: UploadAvatarCommand) -> AvatarOutcome:
    # Object I/O precedes the first database query. The deterministic object key
    # is safe to retry; orphan cleanup belongs to the storage reconciler.
    expected_mime = _MIME_BY_EXTENSION.get(Path(command.filename).suffix.lower())
    allowed_mime = set(settings.avatar_allowed_types.split(","))
    if (
        expected_mime is None
        or command.content_type != expected_mime
        or command.content_type not in allowed_mime
        or len(command.data) > settings.avatar_max_size_bytes
    ):
        raise AvatarError(
            code="customer.avatar.invalid_file",
            message="Avatar file does not meet the configured image policy",
        )
    try:
        prepared = file_uploads.prepare_upload(
            domain="avatars",
            entity_type=_ENTITY_TYPE,
            entity_id=str(command.subscriber_id),
            original_filename=command.filename,
            content_type=command.content_type,
            data=command.data,
            uploaded_by=str(command.uploaded_by) if command.uploaded_by else None,
            owner_subscriber_id=command.subscriber_id,
        )
    except FileValidationError as exc:
        raise AvatarError(
            code="customer.avatar.invalid_file", message=str(exc)
        ) from exc
    subscriber = _locked_subscriber(db, command.subscriber_id)
    if (
        command.expected_legacy_url is not None
        and subscriber.avatar_url != command.expected_legacy_url
    ):
        raise AvatarError(
            code="customer.avatar.stale_legacy_url",
            message="Avatar changed since migration inventory",
        )
    record = file_uploads.stage_prepared_upload(db=db, prepared=prepared)
    _retire_previous(db, subscriber.avatar_url, command.subscriber_id)
    subscriber.avatar_url = avatar_url_for_file(record.id)
    db.flush()
    _stage_avatar_changed(db, subscriber, command.context)
    return AvatarOutcome(avatar_url=subscriber.avatar_url)


def remove_avatar(db: Session, command: RemoveAvatarCommand) -> AvatarOutcome:
    return execute_owner_command(
        db,
        definition=_REMOVE,
        context=command.context,
        operation=lambda: _remove_avatar(db, command),
    )


def _remove_avatar(db: Session, command: RemoveAvatarCommand) -> AvatarOutcome:
    subscriber = _locked_subscriber(db, command.subscriber_id)
    _retire_previous(db, subscriber.avatar_url, command.subscriber_id)
    subscriber.avatar_url = None
    db.flush()
    _stage_avatar_changed(db, subscriber, command.context)
    return AvatarOutcome(avatar_url=None)


def _stage_avatar_changed(
    db: Session, subscriber: Subscriber, context: CommandContext
) -> None:
    emit_event(
        db,
        EventType.subscriber_updated,
        {
            "schema_version": 1,
            "subscriber_id": str(subscriber.id),
            "subscriber_number": subscriber.subscriber_number,
            "updated_fields": ["avatar_url"],
        },
        actor=context.actor,
        subscriber_id=subscriber.id,
        dispatch_after_commit=False,
    )


def resolve_public_avatar(db: Session, file_id: UUID) -> StoredFile | None:
    """Expose only the current avatar of the subscriber recorded as its owner."""
    record = db.get(StoredFile, file_id)
    if (
        record is None
        or record.is_deleted
        or record.storage_provider != "s3"
        or record.entity_type != _ENTITY_TYPE
        or record.owner_subscriber_id is None
        or record.entity_id != str(record.owner_subscriber_id)
        or record.content_type
        not in file_uploads.get_domain_config("avatars").allowed_mime_types
        or _MIME_BY_EXTENSION.get(Path(record.original_filename).suffix.lower())
        != record.content_type
    ):
        return None
    subscriber = db.get(Subscriber, record.owner_subscriber_id)
    if subscriber is None or subscriber.avatar_url != avatar_url_for_file(file_id):
        return None
    return record
