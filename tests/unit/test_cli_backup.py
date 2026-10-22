"""Unit tests for the backup/restore CLI commands (LHP-PROFILE-02).

Covers `backup-manifest`, `backup-create`, `backup-verify`, and `restore-drill`: help text,
JSON output, exit codes (zero on success, nonzero on failure), the manifest-only-does-not-copy
guarantee, unsafe-destination rejection, and the invariant that no command claims profile
activation is authorized. All fixtures are temporary.
"""

import json
import sqlite3
from pathlib import Path

import pytest

import main


def _run(monkeypatch, args):
    monkeypatch.setattr("sys.argv", ["main.py", *args])
    main.cli_main()


def _make_sqlite(path: Path) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
        conn.execute("INSERT INTO t (v) VALUES ('x')")
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


def _create_backup(monkeypatch, dd: Path, backup: Path) -> None:
    _run(
        monkeypatch,
        [
            "backup-create",
            "--data-dir",
            str(dd),
            "--destination",
            str(backup),
            "--boundary",
            str(dd),
        ],
    )


# -- backup-manifest ---------------------------------------------------------------------


def test_backup_manifest_writes_output(monkeypatch, capsys, tmp_path):
    dd = tmp_path / "data"
    _populate(dd)
    out = tmp_path / "manifest.json"
    _run(
        monkeypatch,
        ["backup-manifest", "--data-dir", str(dd), "--output", str(out), "--boundary", str(dd)],
    )
    assert out.is_file()
    manifest = json.loads(out.read_text())
    assert manifest["manifest_schema_version"] == 1
    assert manifest["entries"]


def test_backup_manifest_does_not_copy_data(monkeypatch, tmp_path):
    dd = tmp_path / "data"
    _populate(dd)
    out = tmp_path / "manifest.json"
    _run(
        monkeypatch,
        ["backup-manifest", "--data-dir", str(dd), "--output", str(out), "--boundary", str(dd)],
    )
    # Manifest-only writes ONLY the manifest file — no payload directory anywhere.
    assert not (tmp_path / "payload").exists()
    assert list(tmp_path.glob("**/payload")) == []


def test_backup_manifest_json_output(monkeypatch, capsys, tmp_path):
    dd = tmp_path / "data"
    _populate(dd)
    out = tmp_path / "manifest.json"
    _run(
        monkeypatch,
        [
            "backup-manifest",
            "--data-dir",
            str(dd),
            "--output",
            str(out),
            "--boundary",
            str(dd),
            "--json",
        ],
    )
    parsed = json.loads(capsys.readouterr().out)
    assert parsed["manifest_generated"] is True


def test_backup_manifest_fails_nonzero_on_incomplete_state(monkeypatch, tmp_path):
    dd = tmp_path / "data"
    dd.mkdir()
    _make_sqlite(dd / "state.db")
    (dd / "entities.json").write_text("{}")  # targets.json + archives missing
    out = tmp_path / "manifest.json"
    with pytest.raises(SystemExit) as exc:
        _run(
            monkeypatch,
            ["backup-manifest", "--data-dir", str(dd), "--output", str(out), "--boundary", str(dd)],
        )
    assert exc.value.code != 0
    assert not out.exists()


# -- backup-create -----------------------------------------------------------------------


def test_backup_create_succeeds(monkeypatch, capsys, tmp_path):
    dd = tmp_path / "data"
    _populate(dd)
    backup = tmp_path / "backup"
    _create_backup(monkeypatch, dd, backup)
    assert (backup / "manifest.json").is_file()
    assert (backup / "payload" / "state.db").is_file()
    out = capsys.readouterr().out.lower()
    assert "backup" in out


def test_backup_create_output_never_claims_activation(monkeypatch, capsys, tmp_path):
    dd = tmp_path / "data"
    _populate(dd)
    _create_backup(monkeypatch, dd, tmp_path / "backup")
    out = capsys.readouterr().out.lower()
    assert "authorized: true" not in out
    assert "does not authorize" in out or "not authorize" in out


def test_backup_create_fails_nonzero_on_bad_destination(monkeypatch, tmp_path):
    dd = tmp_path / "data"
    _populate(dd)
    with pytest.raises(SystemExit) as exc:
        _run(
            monkeypatch,
            [
                "backup-create",
                "--data-dir",
                str(dd),
                "--destination",
                str(dd),
                "--boundary",
                str(dd),
            ],
        )
    assert exc.value.code != 0


# -- backup-verify -----------------------------------------------------------------------


def test_backup_verify_succeeds(monkeypatch, capsys, tmp_path):
    dd = tmp_path / "data"
    _populate(dd)
    backup = tmp_path / "backup"
    _create_backup(monkeypatch, dd, backup)
    capsys.readouterr()
    _run(monkeypatch, ["backup-verify", "--backup", str(backup), "--json"])
    parsed = json.loads(capsys.readouterr().out)
    assert parsed["status"] == "verified"


def test_backup_verify_fails_nonzero_on_tamper(monkeypatch, capsys, tmp_path):
    dd = tmp_path / "data"
    _populate(dd)
    backup = tmp_path / "backup"
    _create_backup(monkeypatch, dd, backup)
    (backup / "payload" / "entities.json").write_text("tampered")
    with pytest.raises(SystemExit) as exc:
        _run(monkeypatch, ["backup-verify", "--backup", str(backup)])
    assert exc.value.code != 0


# -- restore-drill -----------------------------------------------------------------------


def test_restore_drill_succeeds(monkeypatch, capsys, tmp_path):
    dd = tmp_path / "data"
    _populate(dd)
    backup = tmp_path / "backup"
    _create_backup(monkeypatch, dd, backup)
    capsys.readouterr()
    dest = tmp_path / "restore"
    _run(
        monkeypatch,
        ["restore-drill", "--backup", str(backup), "--destination", str(dest), "--json"],
    )
    parsed = json.loads(capsys.readouterr().out)
    assert parsed["status"] == "completed"
    assert parsed["profile_activation_authorized"] is False


def test_restore_drill_rejects_data_boundary_nonzero(monkeypatch, tmp_path):
    dd = tmp_path / "data"
    _populate(dd)
    backup = tmp_path / "backup"
    _create_backup(monkeypatch, dd, backup)
    with pytest.raises(SystemExit) as exc:
        _run(monkeypatch, ["restore-drill", "--backup", str(backup), "--destination", "/data"])
    assert exc.value.code != 0


def test_restore_drill_fails_nonzero_on_corrupt_backup(monkeypatch, tmp_path):
    dd = tmp_path / "data"
    _populate(dd)
    backup = tmp_path / "backup"
    _create_backup(monkeypatch, dd, backup)
    (backup / "payload" / "state.db").write_bytes(b"garbage")
    with pytest.raises(SystemExit) as exc:
        _run(
            monkeypatch,
            ["restore-drill", "--backup", str(backup), "--destination", str(tmp_path / "restore")],
        )
    assert exc.value.code != 0


def test_restore_drill_writes_output(monkeypatch, tmp_path):
    dd = tmp_path / "data"
    _populate(dd)
    backup = tmp_path / "backup"
    _create_backup(monkeypatch, dd, backup)
    dest = tmp_path / "restore"
    out = tmp_path / "restore.json"
    _run(
        monkeypatch,
        [
            "restore-drill",
            "--backup",
            str(backup),
            "--destination",
            str(dest),
            "--output",
            str(out),
        ],
    )
    assert out.is_file()
    parsed = json.loads(out.read_text(encoding="utf-8"))
    assert parsed["status"] == "completed"
    assert parsed["profile_activation_authorized"] is False
    assert parsed["manifest_valid"] is True
    assert parsed["payload_verified"] is True
    assert parsed["backup_root"]
    assert parsed["manifest_id"]


def test_restore_drill_writes_output_on_failed_report(monkeypatch, tmp_path):
    dd = tmp_path / "data"
    _populate(dd)
    backup = tmp_path / "backup"
    _create_backup(monkeypatch, dd, backup)
    (backup / "payload" / "state.db").write_bytes(b"garbage")
    dest = tmp_path / "restore"
    out = tmp_path / "restore-failed.json"
    with pytest.raises(SystemExit) as exc:
        _run(
            monkeypatch,
            [
                "restore-drill",
                "--backup",
                str(backup),
                "--destination",
                str(dest),
                "--output",
                str(out),
            ],
        )
    assert exc.value.code != 0
    assert out.is_file()
    parsed = json.loads(out.read_text(encoding="utf-8"))
    assert parsed["status"] == "failed"
    assert parsed["profile_activation_authorized"] is False
    assert parsed["backup_root"]
    assert parsed["manifest_id"]
    assert parsed["sqlite_integrity_verified"] is False or parsed["blocking_failures"]


def test_restore_drill_output_not_written_on_restore_drill_error(monkeypatch, tmp_path):
    dd = tmp_path / "data"
    _populate(dd)
    backup = tmp_path / "backup"
    _create_backup(monkeypatch, dd, backup)
    out = tmp_path / "should-not-exist.json"
    with pytest.raises(SystemExit) as exc:
        _run(
            monkeypatch,
            [
                "restore-drill",
                "--backup",
                str(backup),
                "--destination",
                "/data",
                "--output",
                str(out),
            ],
        )
    assert exc.value.code != 0
    assert not out.exists()
