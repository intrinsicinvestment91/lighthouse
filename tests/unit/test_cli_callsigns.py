"""Unit tests for the `callsigns` CLI command in main.py."""

import json
from unittest.mock import Mock

import main


def _run_cli_with_args(monkeypatch, args):
    monkeypatch.setattr("sys.argv", ["main.py", *args])
    main.cli_main()


def _summary_row(callsign: str, count: int = 1) -> dict:
    return {
        "normalized_callsign": callsign,
        "prefix": "PAT",
        "category": "priority_air_transport",
        "military_style": 1,
        "count": count,
        "first_seen": 1_700_000_000,
        "last_seen": 1_700_000_100,
        "latest_facility": "DARPA HQ",
        "latest_source": "adsb_sentinel",
    }


def _history_row() -> dict:
    return {
        "normalized_callsign": "PAT16",
        "source": "adsb_sentinel",
        "facility": "DARPA HQ",
        "icao_hex": "ae1234",
        "military_style": 1,
        "observed_at": 1_700_000_000,
        "tags": ["callsign"],
        "enrichment": {"normalized_callsign": "PAT16"},
    }


def test_callsigns_summary_human_output(monkeypatch, capsys):
    fetch_summary = Mock(return_value=[_summary_row("PAT16", count=3)])
    fetch_history = Mock(return_value=[])

    monkeypatch.setattr(main.db, "fetch_callsign_summary", fetch_summary)
    monkeypatch.setattr(main.db, "fetch_callsign_history", fetch_history)
    monkeypatch.setattr(main.db, "init_callsign_history_table", Mock())

    _run_cli_with_args(monkeypatch, ["callsigns"])

    fetch_summary.assert_called_once_with(limit=20)
    fetch_history.assert_not_called()
    out = capsys.readouterr().out
    assert "Callsign Summary" in out
    assert "PAT16" in out
    assert "3 observations" in out


def test_callsigns_summary_json_output(monkeypatch, capsys):
    rows = [_summary_row("MUSL")]
    fetch_summary = Mock(return_value=rows)
    fetch_history = Mock(return_value=[])

    monkeypatch.setattr(main.db, "fetch_callsign_summary", fetch_summary)
    monkeypatch.setattr(main.db, "fetch_callsign_history", fetch_history)
    monkeypatch.setattr(main.db, "init_callsign_history_table", Mock())

    _run_cli_with_args(monkeypatch, ["callsigns", "--json"])

    payload = json.loads(capsys.readouterr().out)
    assert payload == rows


def test_callsigns_history_human_output(monkeypatch, capsys):
    fetch_summary = Mock(return_value=[])
    fetch_history = Mock(return_value=[_history_row()])

    monkeypatch.setattr(main.db, "fetch_callsign_summary", fetch_summary)
    monkeypatch.setattr(main.db, "fetch_callsign_history", fetch_history)
    monkeypatch.setattr(main.db, "init_callsign_history_table", Mock())

    _run_cli_with_args(monkeypatch, ["callsigns", "--callsign", "pat16"])

    fetch_history.assert_called_once_with("pat16", limit=20)
    fetch_summary.assert_not_called()
    out = capsys.readouterr().out
    assert "History for PAT16" in out
    assert "adsb_sentinel" in out
    assert "military_style=True" in out


def test_callsigns_history_json_output(monkeypatch, capsys):
    rows = [_history_row()]
    fetch_summary = Mock(return_value=[])
    fetch_history = Mock(return_value=rows)

    monkeypatch.setattr(main.db, "fetch_callsign_summary", fetch_summary)
    monkeypatch.setattr(main.db, "fetch_callsign_history", fetch_history)
    monkeypatch.setattr(main.db, "init_callsign_history_table", Mock())

    _run_cli_with_args(monkeypatch, ["callsigns", "--callsign", "PAT16", "--json"])

    payload = json.loads(capsys.readouterr().out)
    assert payload == rows


def test_callsigns_limit_forwarded(monkeypatch, capsys):
    fetch_summary = Mock(return_value=[])
    fetch_history = Mock(return_value=[])

    monkeypatch.setattr(main.db, "fetch_callsign_summary", fetch_summary)
    monkeypatch.setattr(main.db, "fetch_callsign_history", fetch_history)
    monkeypatch.setattr(main.db, "init_callsign_history_table", Mock())

    _run_cli_with_args(monkeypatch, ["callsigns", "--limit", "5"])

    fetch_summary.assert_called_once_with(limit=5)
