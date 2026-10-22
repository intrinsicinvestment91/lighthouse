"""Unit tests for the read-only backup-manifest contract (LHP-PROFILE-02).

These tests exercise deterministic inventory, digest policy, safe SQLite snapshotting, and
the fail-closed hazards (symlink escape, traversal, absolute paths, special files, external
authoritative state). Everything runs against temporary fixtures only — no real `data/` and
no developer `state.db`.
"""

import hashlib
import json
import os
import sqlite3
from pathlib import Path

import pytest

from modules.backup_manifest import (
    BACKUP_MANIFEST_SCHEMA_VERSION,
    DIGEST_ALGORITHM,
    ENTRY_TYPE_DIRECTORY,
    ENTRY_TYPE_FILE,
    ENTRY_TYPE_SQLITE,
    BackupManifest,
    ManifestError,
    directory_digest,
    generate_manifest,
    sha256_file,
    snapshot_sqlite,
    sqlite_integrity,
    validate_manifest_paths,
)

# -- fixtures ----------------------------------------------------------------------------


def _make_sqlite(path: Path, *, wal: bool = False, rows: int = 3) -> None:
    conn = sqlite3.connect(path)
    try:
        if wal:
            conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
        conn.executemany("INSERT INTO t (v) VALUES (?)", [(f"row-{i}",) for i in range(rows)])
        conn.commit()
    finally:
        conn.close()


def _populate_authoritative(data_dir: Path) -> None:
    """Create a complete, safe authoritative inventory under `data_dir`."""
    data_dir.mkdir(parents=True, exist_ok=True)
    _make_sqlite(data_dir / "state.db")
    (data_dir / "entities.json").write_text('{"entities": []}', encoding="utf-8")
    (data_dir / "targets.json").write_text('{"schema_version": 1, "targets": []}', encoding="utf-8")
    archives = data_dir / "archives"
    archives.mkdir()
    (archives / "a.html").write_text("<html>one</html>", encoding="utf-8")
    (archives / "b.html").write_text("<html>two</html>", encoding="utf-8")


def _gen(data_dir: Path, boundary: Path, **kw) -> BackupManifest:
    return generate_manifest(
        data_dir=data_dir,
        db_path=data_dir / "state.db",
        boundary=boundary,
        manifest_id="fixed-id",
        created_at="2026-07-10T00:00:00Z",
        **kw,
    )


# -- digest helpers ----------------------------------------------------------------------


def test_sha256_file_matches_hashlib(tmp_path):
    f = tmp_path / "x.bin"
    f.write_bytes(b"hello world")
    assert sha256_file(f) == hashlib.sha256(b"hello world").hexdigest()


def test_directory_digest_is_deterministic(tmp_path):
    d = tmp_path / "d"
    d.mkdir()
    (d / "b.txt").write_text("bbb")
    (d / "a.txt").write_text("aaa")
    first = directory_digest(d, tmp_path)
    second = directory_digest(d, tmp_path)
    assert first == second


def test_directory_digest_changes_with_content(tmp_path):
    d = tmp_path / "d"
    d.mkdir()
    (d / "a.txt").write_text("aaa")
    before = directory_digest(d, tmp_path)
    (d / "a.txt").write_text("changed")
    assert directory_digest(d, tmp_path) != before


def test_empty_directory_digest_is_stable(tmp_path):
    d = tmp_path / "empty"
    d.mkdir()
    assert directory_digest(d, tmp_path) == directory_digest(d, tmp_path)


# -- sqlite snapshot + integrity ----------------------------------------------------------


def test_snapshot_sqlite_produces_valid_copy(tmp_path):
    src = tmp_path / "state.db"
    _make_sqlite(src)
    dst = tmp_path / "snap.db"
    result = snapshot_sqlite(src, dst)
    assert result == "ok"
    assert dst.exists()
    assert sqlite_integrity(dst) == "ok"


def test_snapshot_sqlite_leaves_source_unchanged(tmp_path):
    src = tmp_path / "state.db"
    _make_sqlite(src)
    before = sha256_file(src)
    snapshot_sqlite(src, tmp_path / "snap.db")
    assert sha256_file(src) == before


def test_snapshot_sqlite_handles_wal_fixture(tmp_path):
    src = tmp_path / "wal.db"
    _make_sqlite(src, wal=True)
    dst = tmp_path / "snap.db"
    assert snapshot_sqlite(src, dst) == "ok"
    assert sqlite_integrity(dst) == "ok"


def test_corrupted_database_fails_integrity(tmp_path):
    bad = tmp_path / "bad.db"
    bad.write_bytes(b"this is not a sqlite database at all")
    assert sqlite_integrity(bad) != "ok"


def test_snapshot_of_corrupted_source_fails_closed(tmp_path):
    bad = tmp_path / "bad.db"
    bad.write_bytes(b"not sqlite")
    with pytest.raises(ManifestError):
        snapshot_sqlite(bad, tmp_path / "snap.db")


# -- generate_manifest: happy path --------------------------------------------------------


def test_valid_authoritative_inventory_accepted(tmp_path):
    dd = tmp_path / "data"
    _populate_authoritative(dd)
    manifest = _gen(dd, tmp_path)
    logical = {e.logical_name for e in manifest.entries}
    assert {"state_db", "entities_json", "targets_json", "archives_dir"} <= logical


def test_manifest_identity_fields_present(tmp_path):
    dd = tmp_path / "data"
    _populate_authoritative(dd)
    d = _gen(dd, tmp_path).to_dict()
    for key in (
        "manifest_schema_version",
        "manifest_id",
        "created_at",
        "source_boundary",
        "source_data_root",
        "generator_version",
        "inventory_complete",
        "entries",
        "summary",
    ):
        assert key in d, key
    assert d["manifest_schema_version"] == BACKUP_MANIFEST_SCHEMA_VERSION


def test_per_entry_metadata_present(tmp_path):
    dd = tmp_path / "data"
    _populate_authoritative(dd)
    entry = next(e for e in _gen(dd, tmp_path).entries if e.logical_name == "entities_json")
    d = entry.to_dict()
    for key in (
        "entry_id",
        "state_category",
        "logical_name",
        "relative_path",
        "entry_type",
        "required",
        "size_bytes",
        "digest",
        "digest_algorithm",
        "inclusion_reason",
    ):
        assert key in d, key
    assert d["digest_algorithm"] == DIGEST_ALGORITHM


def test_entry_records_absolute_source_path(tmp_path):
    dd = tmp_path / "data"
    _populate_authoritative(dd)
    entry = next(e for e in _gen(dd, tmp_path).entries if e.logical_name == "entities_json")
    assert entry.source_path == str(dd / "entities.json")
    assert Path(entry.source_path).is_absolute()


def test_file_size_and_digest_correct(tmp_path):
    dd = tmp_path / "data"
    _populate_authoritative(dd)
    entry = next(e for e in _gen(dd, tmp_path).entries if e.logical_name == "entities_json")
    raw = (dd / "entities.json").read_bytes()
    assert entry.size_bytes == len(raw)
    assert entry.digest == hashlib.sha256(raw).hexdigest()
    assert entry.entry_type == ENTRY_TYPE_FILE


def test_sqlite_entry_typed_and_integrity_ok(tmp_path):
    dd = tmp_path / "data"
    _populate_authoritative(dd)
    entry = next(e for e in _gen(dd, tmp_path).entries if e.logical_name == "state_db")
    assert entry.entry_type == ENTRY_TYPE_SQLITE
    assert entry.sqlite_integrity == "ok"
    assert entry.digest


def test_archives_directory_entry(tmp_path):
    dd = tmp_path / "data"
    _populate_authoritative(dd)
    entry = next(e for e in _gen(dd, tmp_path).entries if e.logical_name == "archives_dir")
    assert entry.entry_type == ENTRY_TYPE_DIRECTORY
    assert entry.digest


def test_entry_ordering_is_stable(tmp_path):
    dd = tmp_path / "data"
    _populate_authoritative(dd)
    a = [e.relative_path for e in _gen(dd, tmp_path).entries]
    b = [e.relative_path for e in _gen(dd, tmp_path).entries]
    assert a == b == sorted(a)


def test_content_digests_stable_for_unchanged_input(tmp_path):
    dd = tmp_path / "data"
    _populate_authoritative(dd)
    a = {e.logical_name: e.digest for e in _gen(dd, tmp_path).entries}
    b = {e.logical_name: e.digest for e in _gen(dd, tmp_path).entries}
    assert a == b


def test_generate_leaves_source_sqlite_unchanged(tmp_path):
    dd = tmp_path / "data"
    _populate_authoritative(dd)
    before = sha256_file(dd / "state.db")
    _gen(dd, tmp_path)
    assert sha256_file(dd / "state.db") == before


# -- inclusion / exclusion policy ---------------------------------------------------------


def test_runtime_ephemeral_excluded_by_default(tmp_path):
    dd = tmp_path / "data"
    _populate_authoritative(dd)
    (dd / "logs").mkdir()
    (dd / "logs" / "run.log").write_text("noise")
    names = {e.logical_name for e in _gen(dd, tmp_path).entries}
    assert "logs_dir" not in names
    paths = {e.relative_path for e in _gen(dd, tmp_path).entries}
    assert not any("log" in p for p in paths)


def test_reconstructable_excluded_by_default(tmp_path):
    dd = tmp_path / "data"
    _populate_authoritative(dd)
    (dd / "patents").mkdir()
    (dd / "patents" / "p.json").write_text("[]")
    names = {e.logical_name for e in _gen(dd, tmp_path).entries}
    assert "patents_dir" not in names


def test_reconstructable_included_when_requested(tmp_path):
    dd = tmp_path / "data"
    _populate_authoritative(dd)
    (dd / "patents").mkdir()
    (dd / "patents" / "p.json").write_text("[]")
    entry = next(
        (
            e
            for e in _gen(dd, tmp_path, include_reconstructable=True).entries
            if e.logical_name == "patents_dir"
        ),
        None,
    )
    assert entry is not None
    assert entry.state_category == "reconstructable_persistent"
    assert entry.required is False


# -- fail-closed hazards ------------------------------------------------------------------


def test_missing_required_state_rejected(tmp_path):
    dd = tmp_path / "data"
    dd.mkdir()
    _make_sqlite(dd / "state.db")
    (dd / "entities.json").write_text("{}")
    # targets.json and archives/ missing
    with pytest.raises(ManifestError):
        _gen(dd, tmp_path)


def test_external_authoritative_db_rejected(tmp_path):
    dd = tmp_path / "data"
    _populate_authoritative(dd)
    outside = tmp_path / "outside.db"
    _make_sqlite(outside)
    with pytest.raises(ManifestError):
        generate_manifest(
            data_dir=dd, db_path=outside, boundary=dd, manifest_id="x", created_at="t"
        )


def test_symlink_escape_rejected(tmp_path):
    dd = tmp_path / "data"
    _populate_authoritative(dd)
    outside = tmp_path / "secret"
    outside.mkdir()
    (outside / "leak.txt").write_text("secret")
    (dd / "archives" / "link.html").symlink_to(outside / "leak.txt")
    with pytest.raises(ManifestError):
        _gen(dd, tmp_path)


def test_broken_authoritative_symlink_rejected(tmp_path):
    dd = tmp_path / "data"
    _populate_authoritative(dd)
    (dd / "archives" / "dangling.html").symlink_to(dd / "archives" / "nope.html")
    with pytest.raises(ManifestError):
        _gen(dd, tmp_path)


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="no mkfifo on platform")
def test_special_file_rejected(tmp_path):
    dd = tmp_path / "data"
    _populate_authoritative(dd)
    os.mkfifo(dd / "archives" / "pipe")
    with pytest.raises(ManifestError):
        _gen(dd, tmp_path)


def test_no_file_contents_or_secrets_embedded(tmp_path):
    dd = tmp_path / "data"
    _populate_authoritative(dd)
    secret = "SUPER_SECRET_TOKEN_ABC123"
    (dd / "entities.json").write_text(json.dumps({"token": secret}))
    blob = json.dumps(_gen(dd, tmp_path).to_dict())
    assert secret not in blob


# -- manifest validation (schema, categories, path safety) --------------------------------


def test_unsupported_manifest_schema_rejected(tmp_path):
    dd = tmp_path / "data"
    _populate_authoritative(dd)
    d = _gen(dd, tmp_path).to_dict()
    d["manifest_schema_version"] = BACKUP_MANIFEST_SCHEMA_VERSION + 1
    with pytest.raises(ManifestError):
        BackupManifest.from_dict(d).validate()


def test_unknown_state_category_rejected(tmp_path):
    dd = tmp_path / "data"
    _populate_authoritative(dd)
    d = _gen(dd, tmp_path).to_dict()
    d["entries"][0]["state_category"] = "totally_unknown_category"
    with pytest.raises(ManifestError):
        BackupManifest.from_dict(d).validate()


def test_absolute_manifest_path_rejected():
    with pytest.raises(ManifestError):
        validate_manifest_paths(["state.db", "/absolute/path"])


def test_traversal_manifest_path_rejected():
    with pytest.raises(ManifestError):
        validate_manifest_paths(["state.db", "../../etc/passwd"])
    with pytest.raises(ManifestError):
        validate_manifest_paths(["payload/../../../tmp/file"])


def test_duplicate_normalized_path_rejected():
    with pytest.raises(ManifestError):
        validate_manifest_paths(["a/b", "a/./b"])


# -- provenance detection helpers (LHP-PROFILE-14A) ----------------------------------------


def test_detect_lighthouse_version_queries_the_real_distribution(monkeypatch):
    import modules.backup_manifest as bm

    seen = []

    def fake_version(name):
        seen.append(name)
        return "0.1.0"

    monkeypatch.setattr("importlib.metadata.version", fake_version)
    assert bm.detect_lighthouse_version() == "0.1.0"
    # "lighthouse" is only the console-script name; the DISTRIBUTION is pattern-match-sentinel.
    assert seen == ["pattern-match-sentinel"]


def test_detect_lighthouse_version_missing_distribution_is_none(monkeypatch):
    from importlib.metadata import PackageNotFoundError

    import modules.backup_manifest as bm

    def raise_missing(name):
        raise PackageNotFoundError(name)

    monkeypatch.setattr("importlib.metadata.version", raise_missing)
    assert bm.detect_lighthouse_version() is None


def test_detect_repository_commit_is_best_effort(monkeypatch):
    import modules.backup_manifest as bm

    commit = bm.detect_repository_commit()
    assert commit is None or (len(commit) == 40 and set(commit) <= set("0123456789abcdef"))

    def no_git(*args, **kwargs):
        raise OSError("git unavailable")

    monkeypatch.setattr("subprocess.run", no_git)
    assert bm.detect_repository_commit() is None


def test_generate_manifest_stamps_provenance_defaults(tmp_path, monkeypatch):
    import modules.backup_manifest as bm

    monkeypatch.setattr(bm, "detect_lighthouse_version", lambda: "9.9.9")
    monkeypatch.setattr(bm, "detect_repository_commit", lambda: "b" * 40)
    data_dir = tmp_path / "data"
    _populate_authoritative(data_dir)
    defaulted = _gen(data_dir, tmp_path)
    assert defaulted.lighthouse_version == "9.9.9"
    assert defaulted.repository_commit == "b" * 40
    # Explicit caller-supplied values still win over detection.
    explicit = _gen(data_dir, tmp_path, lighthouse_version="1.0.0", repository_commit="a" * 40)
    assert explicit.lighthouse_version == "1.0.0"
    assert explicit.repository_commit == "a" * 40
