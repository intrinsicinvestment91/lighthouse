"""`lighthouse public-sources` — the operator's public-source check surface (LHP-INTEL-04B).

Four verbs: check, runs, history, show. `check` is the only surface in the fact-first pipeline that
reaches the network, so it is a deliberate, named command — never hidden inside `--module`.
These tests spend their weight on what the output must NOT say (no "disappeared"/"important"), on
the fail-closed gates (empty allowlist, dry run), and on the two CLI guarantees that are easy to
lose: errors go to stderr so `--json` stdout stays parseable, and a refusal creates no database.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

import config
import main
from modules import db
from modules import public_source_intake as intake


@pytest.fixture
def store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "state.db"
    monkeypatch.setattr(db, "DB_PATH", str(path))
    return path


@pytest.fixture
def run(monkeypatch: pytest.MonkeyPatch):
    def invoke(*argv: str) -> None:
        monkeypatch.setattr("sys.argv", ["lighthouse", "public-sources", *argv])
        main.cli_main()

    return invoke


class _FakeResponse:
    def __init__(self, status, *, body=b"", headers=None):
        self.status = status
        self._body = body
        self._pos = 0
        self._headers = {k.lower(): v for k, v in (headers or {}).items()}

    def getheader(self, name, default=None):
        return self._headers.get(name.lower(), default)

    def read(self, amt):
        chunk = self._body[self._pos : self._pos + amt]
        self._pos += len(chunk)
        return chunk

    def close(self):
        pass


def _arm(monkeypatch, *responses, hosts=("example.org",)):
    """Configure a host allowlist, a public resolver, and a fake transport queue."""
    monkeypatch.setattr(config.public_source_cfg, "allowed_hosts", list(hosts))
    monkeypatch.setattr(intake, "default_resolver", lambda host: ["8.8.8.8"])
    queue = list(responses)

    class _FakeTransport:
        def open(self, **kwargs):
            item = queue.pop(0)
            if isinstance(item, Exception):
                raise item
            return item

    monkeypatch.setattr(intake, "_GuardedHTTPSTransport", _FakeTransport)


def _targets(tmp_path, *locators):
    p = tmp_path / "targets.json"
    p.write_text(
        json.dumps(
            {
                "schema_version": "public_source_targets.v1",
                "targets": [{"locator": loc} for loc in locators],
            }
        )
    )
    return str(p)


def _tables(path: Path) -> set[str]:
    if not Path(path).exists():
        return set()
    conn = sqlite3.connect(path)
    try:
        return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    finally:
        conn.close()


# -- structure -------------------------------------------------------------------------------


def test_public_sources_and_its_actions_exist(run, capsys):
    for argv in (
        ("--help",),
        ("check", "--help"),
        ("runs", "--help"),
        ("history", "--help"),
        ("show", "--help"),
    ):
        with pytest.raises(SystemExit) as excinfo:
            run(*argv)
        assert excinfo.value.code == 0
        assert capsys.readouterr().out


def test_public_sources_without_an_action_names_them(run, capsys):
    with pytest.raises(SystemExit) as excinfo:
        run()
    assert excinfo.value.code == 1
    err = capsys.readouterr().err
    for action in ("check", "runs", "history", "show"):
        assert action in err


def test_no_module_flag_workaround_was_added():
    parser = main._build_parser()
    module_action = next(
        a for a in parser._actions if "--module" in getattr(a, "option_strings", [])
    )
    assert "public-sources" not in (module_action.choices or [])


# -- check: the fail-closed gates -------------------------------------------------------------


def test_check_with_an_empty_allowlist_exits_nonzero_and_creates_no_database(
    run, store, tmp_path, monkeypatch, capsys
):
    monkeypatch.setattr(config.public_source_cfg, "allowed_hosts", [])
    with pytest.raises(SystemExit) as excinfo:
        run("check", "--targets-file", _targets(tmp_path, "https://example.org/a"), "--json")
    assert excinfo.value.code == 1
    out, err = capsys.readouterr()
    assert err.strip()  # a safe error on stderr
    assert out.strip() == ""  # nothing on stdout
    assert not Path(store).exists()


def test_check_dry_run_writes_nothing(run, store, tmp_path, monkeypatch, capsys):
    _arm(monkeypatch, _FakeResponse(200, body=b"x"))
    run(
        "check",
        "--targets-file",
        _targets(tmp_path, "https://example.org/a"),
        "--dry-run",
        "--json",
    )
    result = json.loads(capsys.readouterr().out)
    assert result["dry_run"] is True
    assert _tables(store) == set()


def test_check_persists_and_reports_factual_outcomes(run, store, tmp_path, monkeypatch, capsys):
    _arm(monkeypatch, _FakeResponse(200, body=b"alpha"), _FakeResponse(404))
    run(
        "check",
        "--targets-file",
        _targets(tmp_path, "https://example.org/a", "https://example.org/b"),
        "--json",
    )
    result = json.loads(capsys.readouterr().out)
    assert result["dry_run"] is False
    outcomes = sorted(c["outcome"] for c in result["checks"])
    assert outcomes == ["available", "not_found"]
    assert "public_source_checks" in _tables(store)


def test_check_human_output_states_no_significance(run, store, tmp_path, monkeypatch, capsys):
    _arm(monkeypatch, _FakeResponse(404))
    run("check", "--targets-file", _targets(tmp_path, "https://example.org/a"))
    out = capsys.readouterr().out.lower()
    for banned in ("disappear", "suppress", "important", "preservation required", "score", "rank"):
        assert banned not in out


# -- reads -----------------------------------------------------------------------------------


def test_runs_on_empty_store_exits_zero_and_creates_nothing(run, store, capsys):
    run("runs", "--json")
    result = json.loads(capsys.readouterr().out)
    assert result["count"] == 0
    assert not Path(store).exists()


def test_runs_and_history_and_show_read_back_a_stored_run(
    run, store, tmp_path, monkeypatch, capsys
):
    _arm(monkeypatch, _FakeResponse(200, body=b"alpha"))
    run("check", "--targets-file", _targets(tmp_path, "https://example.org/a"), "--json")
    check_id = json.loads(capsys.readouterr().out)["checks"][0]["check_id"]

    run("runs", "--json")
    runs = json.loads(capsys.readouterr().out)
    assert runs["count"] == 1

    run("show", "--id", check_id, "--json")
    shown = json.loads(capsys.readouterr().out)
    assert shown["check"]["check_id"] == check_id
    assert shown["check"]["outcome"] == "available"

    run("history", "--content-digest", shown["check"]["content_digest"], "--json")
    history = json.loads(capsys.readouterr().out)
    assert history["count"] == 1


def test_history_requires_exactly_one_selector(run, store, capsys):
    with pytest.raises(SystemExit) as excinfo:
        run("history", "--json")
    assert excinfo.value.code != 0


def test_show_with_a_too_short_prefix_is_refused(run, store, tmp_path, monkeypatch, capsys):
    with pytest.raises(SystemExit) as excinfo:
        run("show", "--id", "psc_1234", "--json")
    assert excinfo.value.code == 1
    assert capsys.readouterr().err.strip()
