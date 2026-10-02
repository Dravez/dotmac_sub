"""Thin public reader for current subscriber avatars."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.db import finish_read_transaction, get_db
from app.services import avatar
from app.services.file_storage import file_uploads
from app.services.object_storage import ObjectNotFoundError

router = APIRouter(prefix="/avatars", tags=["web-public-avatars"])


@router.get("/{file_id}", name="public_subscriber_avatar")
def public_subscriber_avatar(
    file_id: UUID, db: Session = Depends(get_db)
) -> StreamingResponse:
    record = avatar.resolve_public_avatar(db, file_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Avatar not found")
    # Detach the complete metadata snapshot and return the read connection to
    # the pool before S3 GetObject or a slow client can hold it open.
    db.expunge(record)
    finish_read_transaction(db)
    try:
        stream = file_uploads.stream_file(record)
    except ObjectNotFoundError:
        raise HTTPException(status_code=404, detail="Avatar not found") from None
    if stream.content_type != record.content_type:
        raise HTTPException(status_code=404, detail="Avatar not found")
    headers = {
        "Cache-Control": "public, max-age=300",
        "X-Content-Type-Options": "nosniff",
    }
    if stream.content_length is not None:
        headers["Content-Length"] = str(stream.content_length)
    return StreamingResponse(
        stream.chunks,
        media_type=record.content_type,
        headers=headers,
    )
