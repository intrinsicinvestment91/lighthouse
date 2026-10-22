"""Unit tests for the `tempo` CLI command in main.py."""

import json
from unittest.mock import Mock

import main

# Fixed timestamps (2026-01-05 UTC = Monday)
_T_MON = 1_767_571_200
_T_TUE = _T_MON + 25 * 3600
_T_WED = _T_TUE + 1800
_T_MON2 = _T_MON + 7 * 86_400


def _run_cli(monkeypatch, args):
    monkeypatch.setattr("sys.argv", ["lighthouse", *args])
    main.cli_main()


def _obs(callsign="PAT16", facility="DARPA HQ", source="adsb_sentinel", observed_at=_T_MON):
    return {
        "normalized_callsign": callsign,
        "source": source,
        "facility": facility,
        "observed_at": observed_at,
    }


def _recurring_row(callsign="PAT16", obs=8, days=5, first=_T_MON, last=_T_MON2, military=1):
    return {
        "normalized_callsign": callsign,
        "obs_count": obs,
        "day_count": days,
        "first_ts": first,
        "last_ts": last,
        "any_military": military,
    }


# ---------------------------------------------------------------------------
# --callsign
# ---------------------------------------------------------------------------


def test_tempo_callsign_human_output(monkeypatch, capsys):
    history = [_obs(observed_at=_T_MON), _obs(observed_at=_T_TUE), _obs(observed_at=_T_WED)]
    monkeypatch.setattr(main.db, "init_callsign_history_table", Mock())
    monkeypatch.setattr(main.db, "fetch_callsign_activity", Mock(return_value=history))

    _run_cli(monkeypatch, ["tempo", "--callsign", "PAT16"])

    out = capsys.readouterr().out
    assert "PAT16" in out
    assert "Total observations: 3" in out
    assert "Avg interval:" in out


def test_tempo_callsign_json_output(monkeypatch, capsys):
    history = [_obs(observed_at=_T_MON), _obs(observed_at=_T_TUE)]
    monkeypatch.setattr(main.db, "init_callsign_history_table", Mock())
    monkeypatch.setattr(main.db, "fetch_callsign_activity", Mock(return_value=history))

    _run_cli(monkeypatch, ["tempo", "--callsign", "PAT16", "--json"])

    payload = json.loads(capsys.readouterr().out)
    assert "metrics" in payload
    assert "clusters" in payload
    assert payload["metrics"]["total_observations"] == 2
    assert payload["metrics"]["avg_interval_hours"] == 25.0


def test_tempo_callsign_empty_history(monkeypatch, capsys):
    monkeypatch.setattr(main.db, "init_callsign_history_table", Mock())
    monkeypatch.setattr(main.db, "fetch_callsign_activity", Mock(return_value=[]))

    _run_cli(monkeypatch, ["tempo", "--callsign", "UNKN1"])

    out = capsys.readouterr().out
    assert "Total observations: 0" in out


def test_tempo_callsign_recurring_facilities_shown(monkeypatch, capsys):
    history = [
        _obs(facility="DARPA HQ", observed_at=_T_MON),
        _obs(facility="DARPA HQ", observed_at=_T_TUE),
        _obs(facility="Langley", observed_at=_T_WED),
    ]
    monkeypatch.setattr(main.db, "init_callsign_history_table", Mock())
    monkeypatch.setattr(main.db, "fetch_callsign_activity", Mock(return_value=history))

    _run_cli(monkeypatch, ["tempo", "--callsign", "PAT16"])

    out = capsys.readouterr().out
    assert "DARPA HQ" in out


# ---------------------------------------------------------------------------
# --facility
# ---------------------------------------------------------------------------


def test_tempo_facility_human_output(monkeypatch, capsys):
    obs = [_obs(observed_at=_T_MON), _obs(observed_at=_T_TUE)]
    monkeypatch.setattr(main.db, "init_callsign_history_table", Mock())
    monkeypatch.setattr(main.db, "fetch_facility_activity", Mock(return_value=obs))

    _run_cli(monkeypatch, ["tempo", "--facility", "DARPA HQ"])

    out = capsys.readouterr().out
    assert "DARPA HQ" in out
    assert "Total observations:" in out


def test_tempo_facility_json_output(monkeypatch, capsys):
    obs = [_obs(observed_at=_T_MON), _obs(observed_at=_T_TUE)]
    monkeypatch.setattr(main.db, "init_callsign_history_table", Mock())
    monkeypatch.setattr(main.db, "fetch_facility_activity", Mock(return_value=obs))

    _run_cli(monkeypatch, ["tempo", "--facility", "DARPA HQ", "--json"])

    payload = json.loads(capsys.readouterr().out)
    assert payload["total_observations"] == 2
    assert "unique_callsigns" in payload
    assert "recurring_callsigns" in payload


def test_tempo_facility_empty(monkeypatch, capsys):
    monkeypatch.setattr(main.db, "init_callsign_history_table", Mock())
    monkeypatch.setattr(main.db, "fetch_facility_activity", Mock(return_value=[]))

    _run_cli(monkeypatch, ["tempo", "--facility", "Unknown Base"])

    out = capsys.readouterr().out
    assert "Total observations:" in out


# ---------------------------------------------------------------------------
# --recurring
# ---------------------------------------------------------------------------


def test_tempo_recurring_human_output(monkeypatch, capsys):
    rows = [_recurring_row()]
    monkeypatch.setattr(main.db, "init_callsign_history_table", Mock())
    monkeypatch.setattr(main.db, "fetch_recurring_callsigns_summary", Mock(return_value=rows))

    _run_cli(monkeypatch, ["tempo", "--recurring"])

    out = capsys.readouterr().out
    assert "PAT16" in out
    assert "obs" in out


def test_tempo_recurring_json_output(monkeypatch, capsys):
    rows = [_recurring_row(), _recurring_row(callsign="RCH100", military=0)]
    monkeypatch.setattr(main.db, "init_callsign_history_table", Mock())
    monkeypatch.setattr(main.db, "fetch_recurring_callsigns_summary", Mock(return_value=rows))

    _run_cli(monkeypatch, ["tempo", "--recurring", "--json"])

    payload = json.loads(capsys.readouterr().out)
    assert isinstance(payload, list)
    assert len(payload) == 2
    assert payload[0]["normalized_callsign"] == "PAT16"


def test_tempo_recurring_empty_result(monkeypatch, capsys):
    monkeypatch.setattr(main.db, "init_callsign_history_table", Mock())
    monkeypatch.setattr(main.db, "fetch_recurring_callsigns_summary", Mock(return_value=[]))

    _run_cli(monkeypatch, ["tempo", "--recurring"])

    out = capsys.readouterr().out
    assert "No callsigns" in out


def test_tempo_recurring_min_observations_flag(monkeypatch, capsys):
    mock_fn = Mock(return_value=[])
    monkeypatch.setattr(main.db, "init_callsign_history_table", Mock())
    monkeypatch.setattr(main.db, "fetch_recurring_callsigns_summary", mock_fn)

    _run_cli(monkeypatch, ["tempo", "--recurring", "--min-observations", "10", "--min-days", "7"])

    mock_fn.assert_called_once_with(min_observations=10, min_days=7, limit=50)
