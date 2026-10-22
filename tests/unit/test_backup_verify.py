"""Unit tests for independent backup verification and the isolated restore drill (LHP-PROFILE-02).

Verification recomputes integrity independently of creation; the restore drill copies a backup
into an isolated, non-production destination and proves (or fails to prove) that the backup can
be restored and checked. Neither touches production `/data`; both fail closed. All fixtures are
temporary.
"""

import json
import sqlite3
from pathlib import Path

import pytest

from modules.backup_create import create_backup
from modules.backup_manifest import sha256_file, sqlite_integrity
from modules.backup_verify import (
    RestoreDrillError,
    VerificationError,
    restore_drill,
    verify_backup,
)


def _make_sqlite(path: Path, rows: int = 3) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
        conn.executemany("INSERT INTO t (v) VALUES (?)", [(f"r{i}",) for i in range(rows)])
        conn.commit()
    finally:
        conn.close()


def _populate(dd: Path) -> None:
    dd.mkdir(parents=True, exist_ok=True)
    _make_sqlite(dd / "state.db")
    (dd / "entities.json").write_text('{"entities": []}')
    (dd / "targets.json").write_text('{"schema_version": 1, "targets": []}')
    (dd / "archives").mkdir()
    (dd / "archives" / "a.html").write_text("<html>a</html>")


def _make_backup(tmp_path: Path) -> tuple[Path, Path]:
    dd = tmp_path / "data"
    _populate(dd)
    backup = tmp_path / "backup"
    create_backup(
        data_dir=dd,
        destination=backup,
        db_path=dd / "state.db",
        boundary=dd,
        manifest_id="fixed",
        created_at="2026-07-10T00:00:00Z",
    )
    return dd, backup


def _tree_hashes(root: Path) -> dict[str, str]:
    return {
        str(p.relative_to(root)): sha256_file(p) for p in sorted(root.rglob("*")) if p.is_file()
    }


# -- verify_backup: happy path -----------------------------------------------------------


def test_valid_backup_verifies(tmp_path):
    _dd, backup = _make_backup(tmp_path)
    report = verify_backup(backup)
    assert report["status"] == "verified"
    assert report["blocking_failures"] == []


def test_verify_performs_no_mutation(tmp_path):
    _dd, backup = _make_backup(tmp_path)
    before = _tree_hashes(backup)
    verify_backup(backup)
    assert _tree_hashes(backup) == before


# -- verify_backup: content failures -----------------------------------------------------


def test_missing_payload_entry_fails(tmp_path):
    _dd, backup = _make_backup(tmp_path)
    (backup / "payload" / "entities.json").unlink()
    assert verify_backup(backup)["status"] == "failed"


def test_modified_payload_entry_fails(tmp_path):
    _dd, backup = _make_backup(tmp_path)
    (backup / "payload" / "entities.json").write_text("tampered")
    assert verify_backup(backup)["status"] == "failed"


def test_extra_payload_file_fails(tmp_path):
    _dd, backup = _make_backup(tmp_path)
    (backup / "payload" / "surprise.txt").write_text("unexpected")
    assert verify_backup(backup)["status"] == "failed"


def test_sqlite_corruption_fails(tmp_path):
    _dd, backup = _make_backup(tmp_path)
    (backup / "payload" / "state.db").write_bytes(b"not a database")
    assert verify_backup(backup)["status"] == "failed"


# -- verify_backup: structural / manifest failures ---------------------------------------


def test_corrupted_manifest_raises(tmp_path):
    _dd, backup = _make_backup(tmp_path)
    (backup / "manifest.json").write_text("{ this is not json")
    with pytest.raises(VerificationError):
        verify_backup(backup)


def test_missing_manifest_raises(tmp_path):
    _dd, backup = _make_backup(tmp_path)
    (backup / "manifest.json").unlink()
    with pytest.raises(VerificationError):
        verify_backup(backup)


def _rewrite_manifest(backup: Path, mutate) -> None:
    d = json.loads((backup / "manifest.json").read_text())
    mutate(d)
    (backup / "manifest.json").write_text(json.dumps(d))


def test_unsupported_schema_version_fails(tmp_path):
    _dd, backup = _make_backup(tmp_path)
    _rewrite_manifest(backup, lambda d: d.update(manifest_schema_version=99))
    assert verify_backup(backup)["status"] == "failed"


def test_unsupported_digest_algorithm_fails(tmp_path):
    _dd, backup = _make_backup(tmp_path)

    def mutate(d):
        d["entries"][0]["digest_algorithm"] = "md5"

    _rewrite_manifest(backup, mutate)
    assert verify_backup(backup)["status"] == "failed"


def test_absolute_manifest_path_fails(tmp_path):
    _dd, backup = _make_backup(tmp_path)

    def mutate(d):
        d["entries"][0]["relative_path"] = "/etc/passwd"

    _rewrite_manifest(backup, mutate)
    assert verify_backup(backup)["status"] == "failed"


def test_traversal_manifest_path_fails(tmp_path):
    _dd, backup = _make_backup(tmp_path)

    def mutate(d):
        d["entries"][0]["relative_path"] = "../../etc/passwd"

    _rewrite_manifest(backup, mutate)
    assert verify_backup(backup)["status"] == "failed"


def test_malformed_entry_type_fails(tmp_path):
    _dd, backup = _make_backup(tmp_path)

    def mutate(d):
        d["entries"][0]["entry_type"] = "block_device"

    _rewrite_manifest(backup, mutate)
    assert verify_backup(backup)["status"] == "failed"


# -- restore_drill: happy path -----------------------------------------------------------


def test_restore_drill_into_empty_destination_succeeds(tmp_path):
    _dd, backup = _make_backup(tmp_path)
    dest = tmp_path / "restore"
    report = restore_drill(backup, dest)
    assert report["status"] == "completed"
    assert report["manifest_valid"] is True
    assert report["payload_verified"] is True
    assert report["restored_digests_verified"] is True
    assert report["sqlite_integrity_verified"] is True
    assert report["source_unchanged"] is True
    assert report["production_restore_performed"] is False
    assert report["profile_activation_authorized"] is False
    assert report["unproven_guarantees"]


def test_restored_files_match_and_db_valid(tmp_path):
    _dd, backup = _make_backup(tmp_path)
    dest = tmp_path / "restore"
    restore_drill(backup, dest)
    assert (dest / "entities.json").read_text() == '{"entities": []}'
    assert sqlite_integrity(dest / "state.db") == "ok"


def test_restore_drill_leaves_backup_and_source_unchanged(tmp_path):
    dd, backup = _make_backup(tmp_path)
    backup_before = _tree_hashes(backup)
    source_before = _tree_hashes(dd)
    restore_drill(backup, tmp_path / "restore")
    assert _tree_hashes(backup) == backup_before
    assert _tree_hashes(dd) == source_before


# -- restore_drill: destination guards ---------------------------------------------------


def test_restore_drill_rejects_data_boundary(tmp_path):
    _dd, backup = _make_backup(tmp_path)
    with pytest.raises(RestoreDrillError):
        restore_drill(backup, "/data")


def test_restore_drill_rejects_under_data_boundary(tmp_path):
    _dd, backup = _make_backup(tmp_path)
    with pytest.raises(RestoreDrillError):
        restore_drill(backup, "/data/restore_here")


def test_restore_drill_rejects_nonempty_destination(tmp_path):
    _dd, backup = _make_backup(tmp_path)
    dest = tmp_path / "restore"
    dest.mkdir()
    (dest / "existing.txt").write_text("keep")
    with pytest.raises(RestoreDrillError):
        restore_drill(backup, dest)


def test_restore_drill_rejects_overlap_with_backup(tmp_path):
    _dd, backup = _make_backup(tmp_path)
    with pytest.raises(RestoreDrillError):
        restore_drill(backup, backup / "nested")


def test_restore_drill_rejects_overlap_with_source(tmp_path):
    dd, backup = _make_backup(tmp_path)
    with pytest.raises(RestoreDrillError):
        restore_drill(backup, dd / "nested", source_data_root=dd)


def test_restore_drill_rejects_relative_destination(tmp_path):
    _dd, backup = _make_backup(tmp_path)
    with pytest.raises(RestoreDrillError):
        restore_drill(backup, "relative/path")


# -- restore_drill: content failures -----------------------------------------------------


def test_restore_drill_missing_payload_fails(tmp_path):
    _dd, backup = _make_backup(tmp_path)
    (backup / "payload" / "entities.json").unlink()
    report = restore_drill(backup, tmp_path / "restore")
    assert report["status"] == "failed"


def test_restore_drill_altered_payload_fails(tmp_path):
    _dd, backup = _make_backup(tmp_path)
    (backup / "payload" / "entities.json").write_text("tampered")
    report = restore_drill(backup, tmp_path / "restore")
    assert report["status"] == "failed"
    assert report["restored_digests_verified"] is False


def test_restore_drill_corrupted_sqlite_fails(tmp_path):
    _dd, backup = _make_backup(tmp_path)
    (backup / "payload" / "state.db").write_bytes(b"not a database")
    report = restore_drill(backup, tmp_path / "restore")
    assert report["status"] == "failed"
    assert report["sqlite_integrity_verified"] is False
