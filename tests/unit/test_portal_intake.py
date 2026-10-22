"""Unit tests for zk intake metadata endpoint logic."""

import hashlib

import pytest

pytest.importorskip("fastapi")

import portal.app as app_module


async def test_intake_stores_only_hash_metadata(tmp_db, monkeypatch):
    monkeypatch.setattr(app_module, "_db", tmp_db)

    payload = "sensitive witness statement"
    result = await app_module.intake(submission_type="text", content=payload)

    assert result["submission_type"] == "text"
    assert result["content_hash"] == hashlib.sha256(payload.encode("utf-8")).hexdigest()
    assert result["ipfs_cid"] is None

    row = tmp_db.execute(
        "SELECT id, submission_type, content_hash, ipfs_cid "
        "FROM zk_intake_submissions WHERE id = ?",
        (result["id"],),
    ).fetchone()
    assert row is not None
    assert row[0] == result["id"]
    assert row[1] == "text"
    assert row[2] == result["content_hash"]
    assert row[3] is None


async def test_intake_rejects_empty_content(tmp_db, monkeypatch):
    monkeypatch.setattr(app_module, "_db", tmp_db)
    with pytest.raises(app_module.HTTPException):
        await app_module.intake(submission_type="text", content="   ")
