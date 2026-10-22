"""Unit tests for verified backup creation (LHP-PROFILE-02).

Creation writes to an explicit operator-chosen destination only, never overwrites unrelated
content, leaves the source byte-identical, and produces a self-consistent, independently
verified backup or fails closed. All fixtures are temporary.
"""

import json
import sqlite3
from pathlib import Path

import pytest

from modules.backup_create import create_backup
from modules.backup_manifest import BackupError, sha256_file, sqlite_integrity


def _make_sqlite(path: Path, rows: int = 3) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
        conn.executemany("INSERT INTO t (v) VALUES (?)", [(f"r{i}",) for i in range(rows)])
        conn.commit()
    finally:
        conn.close()


def _populate(data_dir: Path) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    _make_sqlite(data_dir / "state.db")
    (data_dir / "entities.json").write_text('{"entities": []}')
    (data_dir / "targets.json").write_text('{"schema_version": 1, "targets": []}')
    arch = data_dir / "archives"
    arch.mkdir()
    (arch / "a.html").write_text("<html>a</html>")
    (arch / "sub").mkdir()
    (arch / "sub" / "b.html").write_text("<html>b</html>")


def _create(data_dir: Path, dest: Path, **kw):
    return create_backup(
        data_dir=data_dir,
        destination=dest,
        db_path=data_dir / "state.db",
        boundary=data_dir,
        manifest_id="fixed",
        created_at="2026-07-10T00:00:00Z",
        **kw,
    )


# -- happy path --------------------------------------------------------------------------


def test_create_produces_deterministic_layout(tmp_path):
    dd = tmp_path / "data"
    _populate(dd)
    dest = tmp_path / "backup"
    report = _create(dd, dest)
    assert (dest / "manifest.json").is_file()
    assert (dest / "payload").is_dir()
    assert (dest / "reports" / "verification.json").is_file()
    assert report["status"] == "created"
    assert report["backup_copied"] is True
    assert report["backup_verified"] is True


def test_report_distinguishes_stages(tmp_path):
    dd = tmp_path / "data"
    _populate(dd)
    report = _create(dd, tmp_path / "backup")
    assert report["eligibility"] == "READY"
    assert report["manifest_generated"] is True
    assert report["backup_copied"] is True
    assert report["backup_verified"] is True
    assert report["restore_drill_completed"] is False
    assert report["profile_activation_authorized"] is False


def test_payload_matches_manifest(tmp_path):
    dd = tmp_path / "data"
    _populate(dd)
    dest = tmp_path / "backup"
    _create(dd, dest)
    manifest = json.loads((dest / "manifest.json").read_text())
    for entry in manifest["entries"]:
        payload_target = dest / "payload" / entry["relative_path"]
        assert payload_target.exists()
        if entry["entry_type"] == "file":
            assert sha256_file(payload_target) == entry["digest"]


def test_payload_sqlite_snapshot_valid(tmp_path):
    dd = tmp_path / "data"
    _populate(dd)
    dest = tmp_path / "backup"
    _create(dd, dest)
    assert sqlite_integrity(dest / "payload" / "state.db") == "ok"


def test_directory_children_copied(tmp_path):
    dd = tmp_path / "data"
    _populate(dd)
    dest = tmp_path / "backup"
    _create(dd, dest)
    assert (dest / "payload" / "archives" / "a.html").read_text() == "<html>a</html>"
    assert (dest / "payload" / "archives" / "sub" / "b.html").read_text() == "<html>b</html>"


# -- source is never mutated -------------------------------------------------------------


def test_source_files_byte_identical(tmp_path):
    dd = tmp_path / "data"
    _populate(dd)
    before = {p.name: sha256_file(p) for p in dd.rglob("*") if p.is_file()}
    _create(dd, tmp_path / "backup")
    after = {p.name: sha256_file(p) for p in dd.rglob("*") if p.is_file()}
    assert before == after


def test_source_sqlite_unchanged(tmp_path):
    dd = tmp_path / "data"
    _populate(dd)
    before = sha256_file(dd / "state.db")
    _create(dd, tmp_path / "backup")
    assert sha256_file(dd / "state.db") == before


# -- destination safety ------------------------------------------------------------------


def test_empty_destination_rejected(tmp_path):
    dd = tmp_path / "data"
    _populate(dd)
    with pytest.raises(BackupError):
        _create(dd, Path(""))


def test_source_destination_equality_rejected(tmp_path):
    dd = tmp_path / "data"
    _populate(dd)
    with pytest.raises(BackupError):
        _create(dd, dd)


def test_destination_nested_in_source_rejected(tmp_path):
    dd = tmp_path / "data"
    _populate(dd)
    with pytest.raises(BackupError):
        _create(dd, dd / "inner_backup")


def test_source_nested_in_destination_rejected(tmp_path):
    dest = tmp_path / "outer"
    dest.mkdir()
    dd = dest / "data"
    _populate(dd)
    with pytest.raises(BackupError):
        _create(dd, dest)


def test_existing_nonempty_destination_rejected_and_preserved(tmp_path):
    dd = tmp_path / "data"
    _populate(dd)
    dest = tmp_path / "backup"
    dest.mkdir()
    keep = dest / "important.txt"
    keep.write_text("do not delete")
    with pytest.raises(BackupError):
        _create(dd, dest)
    assert keep.read_text() == "do not delete"


def test_empty_existing_destination_allowed(tmp_path):
    dd = tmp_path / "data"
    _populate(dd)
    dest = tmp_path / "backup"
    dest.mkdir()
    report = _create(dd, dest)
    assert report["status"] == "created"


# -- fail-closed + cleanup ---------------------------------------------------------------


def test_manifest_generation_failure_prevents_backup(tmp_path):
    dd = tmp_path / "data"
    dd.mkdir()
    _make_sqlite(dd / "state.db")
    (dd / "entities.json").write_text("{}")
    # targets.json and archives/ missing -> manifest generation fails closed
    dest = tmp_path / "backup"
    with pytest.raises(BackupError):
        _create(dd, dest)
    # No finalized backup, no partial payload.
    assert not (dest / "manifest.json").exists()


def test_failed_creation_leaves_no_staging_dirs(tmp_path):
    dd = tmp_path / "data"
    dd.mkdir()
    _make_sqlite(dd / "state.db")
    (dd / "entities.json").write_text("{}")
    dest = tmp_path / "backup"
    with pytest.raises(BackupError):
        _create(dd, dest)
    leftovers = [p for p in tmp_path.iterdir() if p.name.startswith("backup") and p != dd]
    # No leftover staging siblings (e.g. backup.lhstage-*) remain.
    assert all(not p.name.startswith("backup.") for p in tmp_path.iterdir())
    assert leftovers in ([], [dest]) or not any(p.name.startswith("backup.") for p in leftovers)
