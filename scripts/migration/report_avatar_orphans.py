#!/usr/bin/env python3
"""Bounded, read-only evidence for avatar S3 objects lacking active metadata.

This tool never deletes an object. Content-addressed keys may be shared by
several StoredFile rows or an upload whose database transaction has not yet
committed; a report entry is not deletion authority.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select

from app.db import SessionLocal, finish_read_transaction
from app.models.stored_file import StoredFile
from app.services.object_storage import get_s3_storage


@dataclass(frozen=True)
class AvatarObjectEvidence:
    key_sha256: str
    age_hours: float | None
    active_references: int
    inactive_references: int
    disposition: str


def report_avatar_orphans(
    *, max_keys: int, min_age_hours: int
) -> tuple[tuple[AvatarObjectEvidence, ...], bool]:
    if not 1 <= max_keys <= 1000 or min_age_hours < 24:
        raise ValueError("max_keys must be 1..1000 and min_age_hours at least 24")
    storage = get_s3_storage()
    objects: list[tuple[str, datetime | None]] = []
    token: str | None = None
    truncated = False
    while len(objects) < max_keys:
        request: dict[str, object] = {
            "Bucket": storage.bucket_name,
            "Prefix": "avatars/",
            "MaxKeys": min(1000, max_keys - len(objects)),
        }
        if token:
            request["ContinuationToken"] = token
        page = storage.client.list_objects_v2(**request)
        for item in page.get("Contents", ()):
            key = item.get("Key")
            if isinstance(key, str) and key.startswith("avatars/"):
                modified = item.get("LastModified")
                objects.append(
                    (key, modified if isinstance(modified, datetime) else None)
                )
        truncated = bool(page.get("IsTruncated"))
        token = page.get("NextContinuationToken")
        if not truncated or not isinstance(token, str):
            break
    keys = tuple(key for key, _ in objects)
    references: dict[str, tuple[int, int]] = dict.fromkeys(keys, (0, 0))
    if keys:
        with SessionLocal() as db:
            rows = db.execute(
                select(
                    StoredFile.storage_key_or_relative_path,
                    func.count().filter(StoredFile.is_deleted.is_(False)),
                    func.count().filter(StoredFile.is_deleted.is_(True)),
                )
                .where(StoredFile.storage_key_or_relative_path.in_(keys))
                .group_by(StoredFile.storage_key_or_relative_path)
            ).all()
            finish_read_transaction(db)
        for key, active, inactive in rows:
            references[key] = (active, inactive)
    now = datetime.now(UTC)
    evidence: list[AvatarObjectEvidence] = []
    for key, modified in objects:
        age = None
        if modified is not None and modified.tzinfo is not None:
            age = max(0.0, (now - modified.astimezone(UTC)).total_seconds() / 3600)
        active, inactive = references[key]
        disposition = (
            "active_reference"
            if active
            else "investigate_shared_or_inflight_key"
            if age is not None and age >= min_age_hours
            else "too_recent_or_unaged"
        )
        evidence.append(
            AvatarObjectEvidence(
                key_sha256=hashlib.sha256(key.encode("utf-8")).hexdigest(),
                age_hours=round(age, 2) if age is not None else None,
                active_references=active,
                inactive_references=inactive,
                disposition=disposition,
            )
        )
    return tuple(evidence), truncated


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-keys", type=int, default=1000)
    parser.add_argument("--min-age-hours", type=int, default=24)
    args = parser.parse_args()
    evidence, truncated = report_avatar_orphans(
        max_keys=args.max_keys, min_age_hours=args.min_age_hours
    )
    print(
        json.dumps(
            {
                "version": 1,
                "prefix": "avatars/",
                "list_truncated": truncated,
                "objects": [asdict(item) for item in evidence],
                "deletion_authorized": False,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
