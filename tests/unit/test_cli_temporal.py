"""Unit tests for the `temporal` CLI command in main.py."""

import json
from unittest.mock import Mock

import main

# Fixed timestamps for deterministic testing
_T0 = 1_767_225_600  # 2026-01-01 UTC
_T1 = _T0 + 86_400


def _run_cli(monkeypatch, args):
    monkeypatch.setattr("sys.argv", ["lighthouse", *args])
    main.cli_main()


def _obs(callsign="PAT16", facility="DARPA HQ", source="adsb_sentinel", observed_at=_T0):
    return {
        "normalized_callsign": callsign,
        "source": source,
        "facility": facility,
        "observed_at": observed_at,
        "tags": [],
        "enrichment": {},
    }


def _summary_row(facility="DARPA HQ", total=2, unique=1, recurring=None, most_active="PAT16"):
    return {
        "facility": facility,
        "total_observations": total,
        "unique_callsigns": unique,
        "first_seen": "2026-01-01T00:00:00+00:00",
        "last_seen": "2026-01-02T00:00:00+00:00",
        "recurring_callsigns": recurring or ["PAT16"],
        "most_active_callsign": most_active,
    }


# ---------------------------------------------------------------------------
# --callsign
# ---------------------------------------------------------------------------


def test_temporal_callsign_human_output(monkeypatch, capsys):
    history = [_obs(observed_at=_T0), _obs(observed_at=_T1)]
    monkeypatch.setattr(main.db, "init_callsign_history_table", Mock())
    monkeypatch.setattr(main.db, "fetch_callsign_activity", Mock(return_value=history))

    _run_cli(monkeypatch, ["temporal", "--callsign", "PAT16"])

    out = capsys.readouterr().out
    assert "PAT16" in out
    assert "Total observations: 2" in out
    assert "Facilities seen:" in out


def test_temporal_callsign_json_output(monkeypatch, capsys):
    history = [_obs(observed_at=_T0), _obs(observed_at=_T1)]
    monkeypatch.setattr(main.db, "init_callsign_history_table", Mock())
    monkeypatch.setattr(main.db, "fetch_callsign_activity", Mock(return_value=history))

    _run_cli(monkeypatch, ["temporal", "--callsign", "PAT16", "--json"])

    payload = json.loads(capsys.readouterr().out)
    assert "metrics" in payload
    assert "recent" in payload
    assert "windows" in payload
    assert payload["metrics"]["total_observations"] == 2


def test_temporal_callsign_empty_history(monkeypatch, capsys):
    monkeypatch.setattr(main.db, "init_callsign_history_table", Mock())
    monkeypatch.setattr(main.db, "fetch_callsign_activity", Mock(return_value=[]))

    _run_cli(monkeypatch, ["temporal", "--callsign", "UNKN1"])

    out = capsys.readouterr().out
    assert "Total observations: 0" in out


def test_temporal_callsign_fetch_called_with_correct_args(monkeypatch, capsys):
    fetch_mock = Mock(return_value=[])
    monkeypatch.setattr(main.db, "init_callsign_history_table", Mock())
    monkeypatch.setattr(main.db, "fetch_callsign_activity", fetch_mock)

    _run_cli(monkeypatch, ["temporal", "--callsign", "RCH123"])

    fetch_mock.assert_called_once_with("RCH123", limit=500)


def test_temporal_callsign_json_windows_sorted(monkeypatch, capsys):
    history = [
        _obs(observed_at=_T1),
        _obs(observed_at=_T0),
    ]
    monkeypatch.setattr(main.db, "init_callsign_history_table", Mock())
    monkeypatch.setattr(main.db, "fetch_callsign_activity", Mock(return_value=history))

    _run_cli(monkeypatch, ["temporal", "--callsign", "PAT16", "--json"])

    payload = json.loads(capsys.readouterr().out)
    windows = payload["windows"]
    if len(windows) > 1:
        assert windows[0]["date"] <= windows[-1]["date"]


# ---------------------------------------------------------------------------
# --facility
# ---------------------------------------------------------------------------


def test_temporal_facility_human_output(monkeypatch, capsys):
    observations = [_obs(observed_at=_T0), _obs(observed_at=_T1)]
    monkeypatch.setattr(main.db, "init_callsign_history_table", Mock())
    monkeypatch.setattr(main.db, "fetch_facility_activity", Mock(return_value=observations))

    _run_cli(monkeypatch, ["temporal", "--facility", "DARPA HQ"])

    out = capsys.readouterr().out
    assert "Facility: DARPA HQ" in out
    assert "Total observations: 2" in out


def test_temporal_facility_json_output(monkeypatch, capsys):
    observations = [_obs(observed_at=_T0)]
    monkeypatch.setattr(main.db, "init_callsign_history_table", Mock())
    monkeypatch.setattr(main.db, "fetch_facility_activity", Mock(return_value=observations))

    _run_cli(monkeypatch, ["temporal", "--facility", "DARPA HQ", "--json"])

    payload = json.loads(capsys.readouterr().out)
    assert "summary" in payload
    assert "windows" in payload
    assert payload["summary"]["facility"] == "DARPA HQ"


def test_temporal_facility_empty_observations(monkeypatch, capsys):
    monkeypatch.setattr(main.db, "init_callsign_history_table", Mock())
    monkeypatch.setattr(main.db, "fetch_facility_activity", Mock(return_value=[]))

    _run_cli(monkeypatch, ["temporal", "--facility", "Unknown Place"])

    out = capsys.readouterr().out
    assert "Facility: Unknown Place" in out
    assert "Total observations: 0" in out


def test_temporal_facility_fetch_called_with_correct_args(monkeypatch, capsys):
    fetch_mock = Mock(return_value=[])
    monkeypatch.setattr(main.db, "init_callsign_history_table", Mock())
    monkeypatch.setattr(main.db, "fetch_facility_activity", fetch_mock)

    _run_cli(monkeypatch, ["temporal", "--facility", "DARPA HQ"])

    fetch_mock.assert_called_once_with("DARPA HQ", limit=500)


# ---------------------------------------------------------------------------
# --recent
# ---------------------------------------------------------------------------


def test_temporal_recent_human_output(monkeypatch, capsys):
    observations = [_obs(observed_at=_T0), _obs(callsign="RCH1", observed_at=_T1)]
    monkeypatch.setattr(main.db, "init_callsign_history_table", Mock())
    monkeypatch.setattr(main.db, "fetch_recent_activity", Mock(return_value=observations))

    _run_cli(monkeypatch, ["temporal", "--recent", "7"])

    out = capsys.readouterr().out
    assert "Recent activity" in out
    assert "Total observations: 2" in out


def test_temporal_recent_json_output(monkeypatch, capsys):
    observations = [_obs(observed_at=_T0)]
    monkeypatch.setattr(main.db, "init_callsign_history_table", Mock())
    monkeypatch.setattr(main.db, "fetch_recent_activity", Mock(return_value=observations))

    _run_cli(monkeypatch, ["temporal", "--recent", "7", "--json"])

    payload = json.loads(capsys.readouterr().out)
    assert payload["window_days"] == 7
    assert payload["total_observations"] == 1
    assert "callsigns" in payload
    assert "facilities" in payload


def test_temporal_recent_default_window(monkeypatch, capsys):
    fetch_mock = Mock(return_value=[])
    monkeypatch.setattr(main.db, "init_callsign_history_table", Mock())
    monkeypatch.setattr(main.db, "fetch_recent_activity", fetch_mock)

    # No --recent flag => defaults to 7
    _run_cli(monkeypatch, ["temporal"])

    fetch_mock.assert_called_once_with(since_days=7, limit=500)


def test_temporal_recent_custom_window_forwarded(monkeypatch, capsys):
    fetch_mock = Mock(return_value=[])
    monkeypatch.setattr(main.db, "init_callsign_history_table", Mock())
    monkeypatch.setattr(main.db, "fetch_recent_activity", fetch_mock)

    _run_cli(monkeypatch, ["temporal", "--recent", "30"])

    fetch_mock.assert_called_once_with(since_days=30, limit=500)


def test_temporal_recent_empty(monkeypatch, capsys):
    monkeypatch.setattr(main.db, "init_callsign_history_table", Mock())
    monkeypatch.setattr(main.db, "fetch_recent_activity", Mock(return_value=[]))

    _run_cli(monkeypatch, ["temporal", "--recent", "7"])

    out = capsys.readouterr().out
    assert "Total observations: 0" in out


def test_temporal_recent_multiple_callsigns_sorted_by_count(monkeypatch, capsys):
    observations = [
        _obs(callsign="RCH1", observed_at=_T0),
        _obs(callsign="PAT16", observed_at=_T0),
        _obs(callsign="PAT16", observed_at=_T1),
        _obs(callsign="PAT16", observed_at=_T1 + 3600),
    ]
    monkeypatch.setattr(main.db, "init_callsign_history_table", Mock())
    monkeypatch.setattr(main.db, "fetch_recent_activity", Mock(return_value=observations))

    _run_cli(monkeypatch, ["temporal", "--recent", "7", "--json"])

    payload = json.loads(capsys.readouterr().out)
    callsigns = payload["callsigns"]
    # PAT16 has 3 observations, RCH1 has 1 → PAT16 first
    assert callsigns[0]["callsign"] == "PAT16"
    assert callsigns[0]["total_observations"] == 3


# ---------------------------------------------------------------------------
# Mutual exclusivity: --callsign, --facility, --recent
# ---------------------------------------------------------------------------


def test_temporal_callsign_and_facility_are_mutually_exclusive(monkeypatch):
    monkeypatch.setattr(
        "sys.argv",
        ["lighthouse", "temporal", "--callsign", "PAT16", "--facility", "DARPA HQ"],
    )
    import pytest

    with pytest.raises(SystemExit):
        main.cli_main()


def test_temporal_callsign_and_recent_are_mutually_exclusive(monkeypatch):
    monkeypatch.setattr(
        "sys.argv",
        ["lighthouse", "temporal", "--callsign", "PAT16", "--recent", "7"],
    )
    import pytest

    with pytest.raises(SystemExit):
        main.cli_main()
