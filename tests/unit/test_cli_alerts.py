"""Unit tests for the `alerts` CLI command in main.py."""

import json
from unittest.mock import Mock

import main


def _run_cli_with_args(monkeypatch, args):
    monkeypatch.setattr("sys.argv", ["main.py", *args])
    main.cli_main()


def _mock_alert_record(content="mocked-alert"):
    return [{"alert": {"message": content, "score": 0.9, "score_label": "high"}}]


def test_alerts_default_calls_fetch_alerts(monkeypatch, capsys):
    fetch_alerts = Mock(return_value=_mock_alert_record("default"))
    fetch_by_source = Mock(return_value=[])
    fetch_min_score = Mock(return_value=[])
    fetch_by_label = Mock(return_value=[])

    monkeypatch.setattr(main.db, "fetch_alerts", fetch_alerts)
    monkeypatch.setattr(main.db, "fetch_alerts_by_source", fetch_by_source)
    monkeypatch.setattr(main.db, "fetch_alerts_min_score", fetch_min_score)
    monkeypatch.setattr(main.db, "fetch_alerts_by_label", fetch_by_label)

    _run_cli_with_args(monkeypatch, ["alerts"])

    fetch_alerts.assert_called_once_with(limit=10, since_days=None)
    fetch_by_source.assert_not_called()
    fetch_min_score.assert_not_called()
    fetch_by_label.assert_not_called()
    out = capsys.readouterr().out
    assert "default" in out


def test_alerts_limit_calls_fetch_alerts_with_limit(monkeypatch, capsys):
    fetch_alerts = Mock(return_value=_mock_alert_record("limit-five"))
    fetch_by_source = Mock(return_value=[])
    fetch_min_score = Mock(return_value=[])
    fetch_by_label = Mock(return_value=[])

    monkeypatch.setattr(main.db, "fetch_alerts", fetch_alerts)
    monkeypatch.setattr(main.db, "fetch_alerts_by_source", fetch_by_source)
    monkeypatch.setattr(main.db, "fetch_alerts_min_score", fetch_min_score)
    monkeypatch.setattr(main.db, "fetch_alerts_by_label", fetch_by_label)

    _run_cli_with_args(monkeypatch, ["alerts", "--limit", "5"])

    fetch_alerts.assert_called_once_with(limit=5, since_days=None)
    fetch_by_source.assert_not_called()
    fetch_min_score.assert_not_called()
    fetch_by_label.assert_not_called()
    out = capsys.readouterr().out
    assert "limit-five" in out


def test_alerts_source_calls_fetch_alerts_by_source(monkeypatch, capsys):
    fetch_alerts = Mock(return_value=[])
    fetch_by_source = Mock(return_value=_mock_alert_record("source"))
    fetch_min_score = Mock(return_value=[])
    fetch_by_label = Mock(return_value=[])

    monkeypatch.setattr(main.db, "fetch_alerts", fetch_alerts)
    monkeypatch.setattr(main.db, "fetch_alerts_by_source", fetch_by_source)
    monkeypatch.setattr(main.db, "fetch_alerts_min_score", fetch_min_score)
    monkeypatch.setattr(main.db, "fetch_alerts_by_label", fetch_by_label)

    _run_cli_with_args(monkeypatch, ["alerts", "--source", "velocity_tracker"])

    fetch_by_source.assert_called_once_with("velocity_tracker", limit=10, since_days=None)
    fetch_alerts.assert_not_called()
    fetch_min_score.assert_not_called()
    fetch_by_label.assert_not_called()
    out = capsys.readouterr().out
    assert "source" in out


def test_alerts_min_score_calls_fetch_alerts_min_score(monkeypatch, capsys):
    fetch_alerts = Mock(return_value=[])
    fetch_by_source = Mock(return_value=[])
    fetch_min_score = Mock(return_value=_mock_alert_record("min-score"))
    fetch_by_label = Mock(return_value=[])

    monkeypatch.setattr(main.db, "fetch_alerts", fetch_alerts)
    monkeypatch.setattr(main.db, "fetch_alerts_by_source", fetch_by_source)
    monkeypatch.setattr(main.db, "fetch_alerts_min_score", fetch_min_score)
    monkeypatch.setattr(main.db, "fetch_alerts_by_label", fetch_by_label)

    _run_cli_with_args(monkeypatch, ["alerts", "--min-score", "0.75"])

    fetch_min_score.assert_called_once_with(0.75, limit=10, since_days=None)
    fetch_alerts.assert_not_called()
    fetch_by_source.assert_not_called()
    fetch_by_label.assert_not_called()
    out = capsys.readouterr().out
    assert "min-score" in out


def test_alerts_label_calls_fetch_alerts_by_label(monkeypatch, capsys):
    fetch_alerts = Mock(return_value=[])
    fetch_by_source = Mock(return_value=[])
    fetch_min_score = Mock(return_value=[])
    fetch_by_label = Mock(return_value=_mock_alert_record("high"))

    monkeypatch.setattr(main.db, "fetch_alerts", fetch_alerts)
    monkeypatch.setattr(main.db, "fetch_alerts_by_source", fetch_by_source)
    monkeypatch.setattr(main.db, "fetch_alerts_min_score", fetch_min_score)
    monkeypatch.setattr(main.db, "fetch_alerts_by_label", fetch_by_label)

    _run_cli_with_args(monkeypatch, ["alerts", "--label", "high"])

    fetch_by_label.assert_called_once_with("high", limit=10, since_days=None)
    fetch_alerts.assert_not_called()
    fetch_by_source.assert_not_called()
    fetch_min_score.assert_not_called()
    out = capsys.readouterr().out
    assert "high" in out


def test_alerts_entity_calls_find_related_alerts(monkeypatch, capsys):
    fetch_alerts = Mock(return_value=[])
    fetch_by_source = Mock(return_value=[])
    fetch_min_score = Mock(return_value=[])
    fetch_by_label = Mock(return_value=[])
    find_related = Mock(return_value=_mock_alert_record("tesla-related"))

    monkeypatch.setattr(main.db, "fetch_alerts", fetch_alerts)
    monkeypatch.setattr(main.db, "fetch_alerts_by_source", fetch_by_source)
    monkeypatch.setattr(main.db, "fetch_alerts_min_score", fetch_min_score)
    monkeypatch.setattr(main.db, "fetch_alerts_by_label", fetch_by_label)
    monkeypatch.setattr(main.db, "find_related_alerts", find_related)

    _run_cli_with_args(monkeypatch, ["alerts", "--entity", "Tesla"])

    find_related.assert_called_once_with("Tesla", limit=10, since_days=None)
    fetch_alerts.assert_not_called()
    fetch_by_source.assert_not_called()
    fetch_min_score.assert_not_called()
    fetch_by_label.assert_not_called()
    out = capsys.readouterr().out
    assert "tesla-related" in out


def test_alerts_entity_takes_priority_over_source(monkeypatch, capsys):
    fetch_alerts = Mock(return_value=[])
    fetch_by_source = Mock(return_value=_mock_alert_record("source"))
    fetch_min_score = Mock(return_value=[])
    fetch_by_label = Mock(return_value=[])
    find_related = Mock(return_value=_mock_alert_record("entity-priority"))

    monkeypatch.setattr(main.db, "fetch_alerts", fetch_alerts)
    monkeypatch.setattr(main.db, "fetch_alerts_by_source", fetch_by_source)
    monkeypatch.setattr(main.db, "fetch_alerts_min_score", fetch_min_score)
    monkeypatch.setattr(main.db, "fetch_alerts_by_label", fetch_by_label)
    monkeypatch.setattr(main.db, "find_related_alerts", find_related)

    _run_cli_with_args(monkeypatch, ["alerts", "--entity", "Tesla", "--source", "velocity_tracker"])

    find_related.assert_called_once_with("Tesla", limit=10, since_days=None)
    fetch_alerts.assert_not_called()
    fetch_by_source.assert_not_called()
    fetch_min_score.assert_not_called()
    fetch_by_label.assert_not_called()
    out = capsys.readouterr().out
    assert "entity-priority" in out


def test_alerts_json_flag_outputs_json_array(monkeypatch, capsys):
    fetch_alerts = Mock(return_value=_mock_alert_record("json-output"))
    monkeypatch.setattr(main.db, "fetch_alerts", fetch_alerts)
    monkeypatch.setattr(main.db, "fetch_alerts_by_source", Mock(return_value=[]))
    monkeypatch.setattr(main.db, "fetch_alerts_min_score", Mock(return_value=[]))
    monkeypatch.setattr(main.db, "fetch_alerts_by_label", Mock(return_value=[]))

    _run_cli_with_args(monkeypatch, ["alerts", "--json"])

    out = capsys.readouterr().out
    parsed = json.loads(out)
    assert isinstance(parsed, list)
    assert parsed[0]["message"] == "json-output"


def test_alerts_since_flag_passed_to_db(monkeypatch, capsys):
    fetch_alerts = Mock(return_value=_mock_alert_record("since-test"))
    monkeypatch.setattr(main.db, "fetch_alerts", fetch_alerts)
    monkeypatch.setattr(main.db, "fetch_alerts_by_source", Mock(return_value=[]))
    monkeypatch.setattr(main.db, "fetch_alerts_min_score", Mock(return_value=[]))
    monkeypatch.setattr(main.db, "fetch_alerts_by_label", Mock(return_value=[]))

    _run_cli_with_args(monkeypatch, ["alerts", "--since", "7"])

    fetch_alerts.assert_called_once_with(limit=10, since_days=7)


# ---------------------------------------------------------------------------
# status command
# ---------------------------------------------------------------------------


def test_status_command_shows_db_and_key_info(monkeypatch, capsys, tmp_path):
    test_db = tmp_path / "state.db"
    monkeypatch.setattr(main.db, "DB_PATH", test_db)
    monkeypatch.setattr("main.db.init_alerts_table", Mock())
    monkeypatch.setattr("main.db.fetchall", Mock(return_value=[]))

    import config as cfg_mod

    monkeypatch.setattr(cfg_mod.app_cfg, "state_db_path", str(test_db))
    monkeypatch.setattr(cfg_mod.nostr_cfg, "private_key_hex", "")
    monkeypatch.setattr(cfg_mod.narrative_cfg, "anthropic_api_key", "")

    _run_cli_with_args(monkeypatch, ["status"])

    out = capsys.readouterr().out
    assert "DB:" in out
    assert "Schema:" in out
    assert "NOSTR key:" in out
    assert "not set" in out


def test_status_command_shows_alert_sources(monkeypatch, capsys, tmp_path):
    test_db = tmp_path / "state.db"
    monkeypatch.setattr(main.db, "DB_PATH", test_db)
    monkeypatch.setattr("main.db.init_alerts_table", Mock())

    fake_rows = [
        {"source": "sec_sentinel", "count": 5, "last_seen": "2026-04-27T10:00:00"},
        {"source": "velocity_tracker", "count": 2, "last_seen": "2026-04-26T08:00:00"},
    ]
    monkeypatch.setattr("main.db.fetchall", Mock(return_value=fake_rows))

    import config as cfg_mod

    monkeypatch.setattr(cfg_mod.app_cfg, "state_db_path", str(test_db))
    monkeypatch.setattr(cfg_mod.nostr_cfg, "private_key_hex", "")
    monkeypatch.setattr(cfg_mod.narrative_cfg, "anthropic_api_key", "")

    _run_cli_with_args(monkeypatch, ["status"])

    out = capsys.readouterr().out
    assert "sec_sentinel" in out
    assert "velocity_tracker" in out
    assert "5" in out
    assert "2" in out


def test_status_command_no_alerts_shows_placeholder(monkeypatch, capsys, tmp_path):
    test_db = tmp_path / "state.db"
    monkeypatch.setattr(main.db, "DB_PATH", test_db)
    monkeypatch.setattr("main.db.init_alerts_table", Mock())
    monkeypatch.setattr("main.db.fetchall", Mock(return_value=[]))

    import config as cfg_mod

    monkeypatch.setattr(cfg_mod.app_cfg, "state_db_path", str(test_db))
    monkeypatch.setattr(cfg_mod.nostr_cfg, "private_key_hex", "")
    monkeypatch.setattr(cfg_mod.narrative_cfg, "anthropic_api_key", "")

    _run_cli_with_args(monkeypatch, ["status"])

    out = capsys.readouterr().out
    assert "no alerts yet" in out
