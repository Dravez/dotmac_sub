"""Prevent the removed local-avatar writer from returning."""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_avatar_owner_has_no_local_file_mutation() -> None:
    source = (ROOT / "app/services/avatar.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    forbidden = {"open", "mkdir", "write_bytes", "unlink", "remove"}
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and (
            (isinstance(node.func, ast.Name) and node.func.id in forbidden)
            or (isinstance(node.func, ast.Attribute) and node.func.attr in forbidden)
        )
    ]
    assert not calls, "Avatar storage must never write or delete local files"
    assert "file_uploads.prepare_upload(" in source
    assert "file_uploads.stage_prepared_upload(" in source
    assert "file_uploads.stage_soft_delete(" in source


def test_profile_has_no_precommit_avatar_delete() -> None:
    source = (ROOT / "app/services/user_profile.py").read_text(encoding="utf-8")
    assert "avatar_service.delete_avatar(" not in source
    assert "avatar_service.upload_avatar(" in source
    assert "avatar_service.remove_avatar(" in source


def test_legacy_file_service_cannot_create_an_avatar_writer() -> None:
    source = (ROOT / "app/services/file_upload.py").read_text(encoding="utf-8")
    assert "get_avatar_upload" not in source
    assert "AVATAR_UPLOAD_DIR" not in source
