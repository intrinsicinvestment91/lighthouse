"""Unit tests for modules/local_gateway.py"""

from unittest.mock import MagicMock

import modules.local_gateway as gw

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_T_MON = 1_767_571_200  # 2026-01-05 00:00:00 UTC (Monday)


def _fake_db(
    *,
    callsign_activity=None,
    facility_activity=None,
    correlations=None,
    recurring_summary=None,
    graph_node=None,
    traverse=None,
):
    mock = MagicMock()
    mock.fetch_callsign_activity.return_value = callsign_activity or []
    mock.fetch_facility_activity.return_value = facility_activity or []
    mock.fetch_correlations.return_value = correlations or []
    mock.fetch_recurring_callsigns_summary.return_value = recurring_summary or []
    mock.fetch_graph_node.return_value = graph_node
    mock.traverse_graph.return_value = traverse or {"nodes": [], "edges": []}
    return mock


def _obs(callsign="PAT16", facility="DARPA HQ", observed_at=_T_MON):
    return {"normalized_callsign": callsign, "facility": facility, "observed_at": observed_at}


# ---------------------------------------------------------------------------
# dispatch — method validation
# ---------------------------------------------------------------------------


def test_dispatch_unknown_method():
    result = gw.dispatch("not.a.method", {})
    assert result["status"] == "error"
    assert "unknown method" in result["error"]


def test_dispatch_empty_method():
    result = gw.dispatch("", {})
    assert result["status"] == "error"


# ---------------------------------------------------------------------------
# callsign.summary
# ---------------------------------------------------------------------------


def test_callsign_summary_missing_callsign(monkeypatch):
    monkeypatch.setattr(gw, "db", _fake_db())
    result = gw.dispatch("callsign.summary", {})
    assert result["status"] == "error"
    assert "callsign" in result["error"]


def test_callsign_summary_valid(monkeypatch):
    history = [_obs(observed_at=_T_MON), _obs(observed_at=_T_MON + 86400)]
    mock_db = _fake_db(callsign_activity=history)
    monkeypatch.setattr(gw, "db", mock_db)
    result = gw.dispatch("callsign.summary", {"callsign": "PAT16"})
    assert result["status"] == "ok"
    assert result["method"] == "callsign.summary"
    assert "metrics" in result["result"]
    assert "clusters" in result["result"]
    assert result["result"]["callsign"] == "PAT16"
    mock_db.fetch_callsign_activity.assert_called_once_with("PAT16", limit=500)


def test_callsign_summary_empty_history(monkeypatch):
    monkeypatch.setattr(gw, "db", _fake_db())
    result = gw.dispatch("callsign.summary", {"callsign": "PAT16"})
    assert result["status"] == "ok"
    assert result["result"]["metrics"]["total_observations"] == 0


# ---------------------------------------------------------------------------
# facility.summary
# ---------------------------------------------------------------------------


def test_facility_summary_missing_facility(monkeypatch):
    monkeypatch.setattr(gw, "db", _fake_db())
    result = gw.dispatch("facility.summary", {})
    assert result["status"] == "error"
    assert "facility" in result["error"]


def test_facility_summary_valid(monkeypatch):
    obs = [_obs(facility="DARPA HQ", observed_at=_T_MON)]
    mock_db = _fake_db(facility_activity=obs)
    monkeypatch.setattr(gw, "db", mock_db)
    result = gw.dispatch("facility.summary", {"facility": "DARPA HQ"})
    assert result["status"] == "ok"
    assert "total_observations" in result["result"]
    mock_db.fetch_facility_activity.assert_called_once_with("DARPA HQ", limit=500)


# ---------------------------------------------------------------------------
# correlations.recent
# ---------------------------------------------------------------------------


def test_correlations_recent_default_limit(monkeypatch):
    mock_db = _fake_db()
    monkeypatch.setattr(gw, "db", mock_db)
    gw.dispatch("correlations.recent", {})
    mock_db.fetch_correlations.assert_called_once_with(limit=10)


def test_correlations_recent_limit_capped(monkeypatch):
    mock_db = _fake_db()
    monkeypatch.setattr(gw, "db", mock_db)
    gw.dispatch("correlations.recent", {"limit": 9999})
    mock_db.fetch_correlations.assert_called_once_with(limit=gw.MAX_LIMIT)


def test_correlations_recent_stripped(monkeypatch):
    full_row = {
        "id": 1,
        "entity": "test",
        "entities": ["test"],
        "signals": ["adsb_sentinel"],
        "score": 7.5,
        "detected_at": "2026-01-01T00:00:00",
        "event_type": "aviation",
        "evidence_json": '{"large": "blob"}',
        "entities_json": '["test"]',
        "signals_json": '["adsb_sentinel"]',
        "evidence": {"large": "blob"},
    }
    monkeypatch.setattr(gw, "db", _fake_db(correlations=[full_row]))
    result = gw.dispatch("correlations.recent", {})
    corr = result["result"]["correlations"][0]
    assert "evidence_json" not in corr
    assert "entities_json" not in corr
    assert "evidence" not in corr
    assert corr["score"] == 7.5
    assert corr["signals"] == ["adsb_sentinel"]


# ---------------------------------------------------------------------------
# graph.summary
# ---------------------------------------------------------------------------


def test_graph_summary_missing_entity(monkeypatch):
    monkeypatch.setattr(gw, "db", _fake_db())
    result = gw.dispatch("graph.summary", {})
    assert result["status"] == "error"
    assert "entity" in result["error"]


def test_graph_summary_depth_clamped(monkeypatch):
    mock_db = _fake_db()
    monkeypatch.setattr(gw, "db", mock_db)
    gw.dispatch("graph.summary", {"entity": "PAT16", "depth": 99})
    call_kwargs = mock_db.traverse_graph.call_args
    assert call_kwargs.kwargs["depth"] == gw.MAX_DEPTH


def test_graph_summary_valid(monkeypatch):
    mock_db = _fake_db(traverse={"nodes": [{"node_id": "entity:test"}], "edges": []})
    monkeypatch.setattr(gw, "db", mock_db)
    result = gw.dispatch("graph.summary", {"entity": "test"})
    assert result["status"] == "ok"
    assert "node_id" in result["result"]
    assert "nodes" in result["result"]
    assert "edges" in result["result"]


# ---------------------------------------------------------------------------
# intelligence.daily_brief
# ---------------------------------------------------------------------------


def test_intelligence_daily_brief(monkeypatch):
    mock_db = _fake_db(
        recurring_summary=[{"normalized_callsign": "PAT16", "obs_count": 10}],
    )
    monkeypatch.setattr(gw, "db", mock_db)
    result = gw.dispatch("intelligence.daily_brief", {})
    assert result["status"] == "ok"
    assert "recurring_callsigns" in result["result"]
    assert "recent_correlations" in result["result"]
    mock_db.fetch_recurring_callsigns_summary.assert_called_once_with(
        min_observations=5, min_days=3, limit=5
    )


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


def test_db_exception_returns_error_not_raise(monkeypatch):
    mock_db = MagicMock()
    mock_db.fetch_callsign_activity.side_effect = RuntimeError("DB connection lost")
    monkeypatch.setattr(gw, "db", mock_db)
    result = gw.dispatch("callsign.summary", {"callsign": "PAT16"})
    assert result["status"] == "error"
    assert result["error"] == "internal error"
