"""Unit tests for the read-only `backup-readiness` CLI command in main.py."""

import json

import main


def _run_cli_with_args(monkeypatch, args):
    monkeypatch.setattr("sys.argv", ["main.py", *args])
    main.cli_main()


def test_backup_readiness_runs(monkeypatch, capsys):
    _run_cli_with_args(monkeypatch, ["backup-readiness"])
    out = capsys.readouterr().out
    assert "Backup readiness:" in out
    assert "Boundary: /data" in out
    # Eligibility-vs-completed-backup distinction is always surfaced.
    assert "does NOT assert" in out


def test_backup_readiness_json(monkeypatch, capsys):
    _run_cli_with_args(monkeypatch, ["backup-readiness", "--json"])
    parsed = json.loads(capsys.readouterr().out)
    assert parsed["status"] in {"READY", "NOT_READY", "UNKNOWN"}
    assert "checks" in parsed
    assert parsed["backup_performed"] is False


def test_backup_readiness_ready_scenario(monkeypatch, capsys, tmp_path):
    dd = tmp_path / "data"
    dd.mkdir()
    for name in ("state.db", "entities.json", "targets.json"):
        (dd / name).write_text("x")
    (dd / "archives").mkdir()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("LIGHTHOUSE_DATA_DIR", str(dd))
    monkeypatch.setattr("modules.db.DB_PATH", dd / "state.db")

    _run_cli_with_args(monkeypatch, ["backup-readiness", "--boundary", str(tmp_path), "--json"])
    parsed = json.loads(capsys.readouterr().out)
    assert parsed["status"] == "READY"


def test_backup_readiness_is_deterministic(monkeypatch, capsys):
    _run_cli_with_args(monkeypatch, ["backup-readiness", "--json"])
    first = json.loads(capsys.readouterr().out)["status"]
    _run_cli_with_args(monkeypatch, ["backup-readiness", "--json"])
    second = json.loads(capsys.readouterr().out)["status"]
    assert first == second
