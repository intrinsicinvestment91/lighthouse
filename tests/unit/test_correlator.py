"""Unit tests for modules/correlator.py"""

import json
from unittest.mock import Mock

import modules.correlator as correlator_module
from modules import db
from modules.correlator import correlate, run_correlator
from modules.state_db import init_db


def _write_entities(tmp_path, monkeypatch) -> None:
    entities_path = tmp_path / "entities.json"
    entities_path.write_text(
        json.dumps(
            [
                {
                    "name": "Lockheed Martin",
                    "assignee_patterns": ["Lockheed"],
                    "facilities": ["DARPA HQ"],
                }
            ]
        ),
        encoding="utf-8",
    )
    import config

    monkeypatch.setattr(config.correlator_cfg, "entities_path", str(entities_path))


def _lockheed_multi_signal_inputs():
    sec = [{"company": "Lockheed Martin Corp", "filed_at": "2026-04-01", "accession_no": "1"}]
    flight = [{"facility": "DARPA HQ", "icao24": "abc", "callsign": "EXEC1"}]
    adsb = [{"facility": "DARPA HQ", "hex": "AE1234", "callsign": "SPAR1", "military": True}]
    return sec, [], flight, adsb


def test_correlate_generates_event_for_high_signal_core(tmp_path, monkeypatch):
    entities_path = tmp_path / "entities.json"
    entities_path.write_text(
        json.dumps(
            [
                {
                    "name": "Lockheed Martin",
                    "assignee_patterns": ["Lockheed"],
                    "facilities": ["DARPA HQ"],
                }
            ]
        ),
        encoding="utf-8",
    )

    import config

    monkeypatch.setattr(config.correlator_cfg, "entities_path", str(entities_path))

    events = correlate(
        sec_alerts=[
            {"company": "Lockheed Martin Corp", "filed_at": "2026-04-01", "accession_no": "1"}
        ],
        narrative_alerts=[],
        flight_alerts=[{"facility": "DARPA HQ", "icao24": "abc", "callsign": "EXEC1"}],
        adsb_alerts=[
            {"facility": "DARPA HQ", "hex": "AE1234", "callsign": "SPAR1", "military": True}
        ],
    )
    assert len(events) == 1
    assert events[0]["event_type"].startswith("sec_")
    assert events[0]["score"] >= 6.0
    assert "sec" in events[0]["signals"]
    assert "adsb" in events[0]["signals"]


async def test_run_correlator_persists_and_dedups(tmp_db_path, tmp_path, monkeypatch):
    conn = init_db(tmp_db_path)
    monkeypatch.setattr(db, "DB_PATH", tmp_db_path)
    entities_path = tmp_path / "entities.json"
    entities_path.write_text(
        json.dumps(
            [
                {
                    "name": "Lockheed Martin",
                    "assignee_patterns": ["Lockheed"],
                    "facilities": ["DARPA HQ"],
                }
            ]
        ),
        encoding="utf-8",
    )
    import config

    monkeypatch.setattr(config.correlator_cfg, "entities_path", str(entities_path))
    sec = [{"company": "Lockheed Martin Corp", "filed_at": "2026-04-01", "accession_no": "1"}]
    flight = [{"facility": "DARPA HQ", "icao24": "abc", "callsign": "EXEC1"}]
    adsb = [{"facility": "DARPA HQ", "hex": "AE1234", "callsign": "SPAR1", "military": True}]
    narrative = []

    events1 = await run_correlator(
        conn,
        sec_alerts=sec,
        narrative_alerts=narrative,
        flight_alerts=flight,
        adsb_alerts=adsb,
        dry_run=False,
    )
    events2 = await run_correlator(
        conn,
        sec_alerts=sec,
        narrative_alerts=narrative,
        flight_alerts=flight,
        adsb_alerts=adsb,
        dry_run=False,
    )
    rows = db.fetch_correlations(limit=10)
    assert len(events1) == 1
    assert len(events2) == 1
    assert len(rows) == 1
    assert rows[0]["event_type"] == events1[0]["event_type"]
    assert rows[0]["entity"] == "Lockheed Martin"
    assert rows[0]["signals"] == sorted(events1[0]["signals"])
    assert rows[0]["event_id"] == events1[0]["event_id"]


async def test_run_correlator_dry_run_skips_db_writes(tmp_db_path, tmp_path, monkeypatch):
    conn = init_db(tmp_db_path)
    monkeypatch.setattr(db, "DB_PATH", tmp_db_path)
    entities_path = tmp_path / "entities.json"
    entities_path.write_text(
        json.dumps(
            [
                {
                    "name": "Lockheed Martin",
                    "assignee_patterns": ["Lockheed"],
                    "facilities": ["DARPA HQ"],
                }
            ]
        ),
        encoding="utf-8",
    )
    import config

    monkeypatch.setattr(config.correlator_cfg, "entities_path", str(entities_path))
    events = await run_correlator(
        conn,
        sec_alerts=[{"company": "Lockheed Martin Corp", "filed_at": "2026-04-01"}],
        narrative_alerts=[],
        flight_alerts=[{"facility": "DARPA HQ", "icao24": "abc", "callsign": "EXEC1"}],
        adsb_alerts=[],
        dry_run=True,
    )
    db.init_correlation_table()
    count = len(db.fetch_correlations(limit=10))
    assert len(events) == 1
    assert count == 0


async def test_run_correlator_sec_flight_visible_via_db_fetch(tmp_db_path, tmp_path, monkeypatch):
    conn = init_db(tmp_db_path)
    monkeypatch.setattr(db, "DB_PATH", tmp_db_path)
    entities_path = tmp_path / "entities.json"
    entities_path.write_text(
        json.dumps(
            [
                {
                    "name": "Lockheed Martin",
                    "assignee_patterns": ["Lockheed"],
                    "facilities": ["DARPA HQ"],
                }
            ]
        ),
        encoding="utf-8",
    )
    import config

    monkeypatch.setattr(config.correlator_cfg, "entities_path", str(entities_path))

    events = await run_correlator(
        conn,
        sec_alerts=[
            {
                "company": "Lockheed Martin Corp",
                "filed_at": "2026-04-01",
                "accession_no": "0000936340-26-000042",
            }
        ],
        narrative_alerts=[],
        flight_alerts=[{"facility": "DARPA HQ", "icao24": "abc", "callsign": "EXEC1"}],
        adsb_alerts=[],
        dry_run=False,
    )

    rows = db.fetch_correlations(limit=5)
    assert len(events) == 1
    assert rows
    assert rows[0]["event_id"] == events[0]["event_id"]
    assert rows[0]["event_type"] == "sec_flight"
    assert rows[0]["entities"] == ["Lockheed Martin"]
    assert rows[0]["signals"] == ["flight", "sec"]


async def test_run_correlator_survival_mode_when_witness_attach_fails(
    tmp_db_path, tmp_path, monkeypatch
):
    conn = init_db(tmp_db_path)
    monkeypatch.setattr(db, "DB_PATH", tmp_db_path)
    entities_path = tmp_path / "entities.json"
    entities_path.write_text(
        json.dumps(
            [
                {
                    "name": "Lockheed Martin",
                    "assignee_patterns": ["Lockheed"],
                    "facilities": ["DARPA HQ"],
                }
            ]
        ),
        encoding="utf-8",
    )
    import config

    monkeypatch.setattr(config.correlator_cfg, "entities_path", str(entities_path))

    def _boom(_event):
        raise RuntimeError("witness path unavailable")

    monkeypatch.setattr(correlator_module, "attach_witness", _boom)

    events = await run_correlator(
        conn,
        sec_alerts=[
            {"company": "Lockheed Martin Corp", "filed_at": "2026-04-01", "accession_no": "1"}
        ],
        narrative_alerts=[],
        flight_alerts=[{"facility": "DARPA HQ", "icao24": "abc", "callsign": "EXEC1"}],
        adsb_alerts=[],
        dry_run=False,
    )

    assert len(events) == 1
    rows = db.fetch_correlations(limit=1)
    assert len(rows) == 1
    assert rows[0]["witness_hash"]
    assert rows[0]["witness_signature"] is None


async def test_duplicate_sec_alert_inserts_do_not_duplicate_correlations(
    tmp_db_path, tmp_path, monkeypatch
):
    conn = init_db(tmp_db_path)
    monkeypatch.setattr(db, "DB_PATH", tmp_db_path)
    db.init_alerts_table()
    db.init_correlation_table()

    entities_path = tmp_path / "entities.json"
    entities_path.write_text(
        json.dumps(
            [
                {
                    "name": "Lockheed Martin",
                    "assignee_patterns": ["Lockheed"],
                    "facilities": ["DARPA HQ"],
                }
            ]
        ),
        encoding="utf-8",
    )
    import config

    monkeypatch.setattr(config.correlator_cfg, "entities_path", str(entities_path))

    dedup_key = "sec:0000936395-26-000042:lockheed martin corp:sec_8k"
    sec_alert_a = {
        "company": "Lockheed Martin Corp",
        "filed_at": "2026-04-01",
        "accession_no": "0000936395-26-000042",
        "items": "1.01",
    }
    sec_alert_b = {**sec_alert_a, "items": "1.01,7.01"}
    flight = [{"facility": "DARPA HQ", "icao24": "abc", "callsign": "EXEC1"}]
    adsb = [{"facility": "DARPA HQ", "hex": "AE1234", "callsign": "SPAR1", "military": True}]

    assert db.insert_alert("sec_sentinel", sec_alert_a, dedup_key=dedup_key) is True
    assert db.insert_alert("sec_sentinel", sec_alert_b, dedup_key=dedup_key) is False
    assert len(db.fetch_alerts_by_source("sec_sentinel", limit=10)) == 1

    sec = [
        {
            "company": "Lockheed Martin Corp",
            "filed_at": "2026-04-01",
            "accession_no": "0000936395-26-000042",
        }
    ]
    await run_correlator(
        conn,
        sec_alerts=sec,
        narrative_alerts=[],
        flight_alerts=flight,
        adsb_alerts=adsb,
        dry_run=False,
    )
    await run_correlator(
        conn,
        sec_alerts=sec,
        narrative_alerts=[],
        flight_alerts=flight,
        adsb_alerts=adsb,
        dry_run=False,
    )

    rows = db.fetch_correlations(limit=10)
    assert len(rows) == 1


async def test_run_correlator_calls_graph_builder_on_persist(tmp_db_path, tmp_path, monkeypatch):
    conn = init_db(tmp_db_path)
    monkeypatch.setattr(db, "DB_PATH", tmp_db_path)
    _write_entities(tmp_path, monkeypatch)
    sec, narrative, flight, adsb = _lockheed_multi_signal_inputs()

    build_graph = Mock(side_effect=correlator_module.build_graph_from_correlation)
    monkeypatch.setattr(correlator_module, "build_graph_from_correlation", build_graph)

    events = await run_correlator(
        conn,
        sec_alerts=sec,
        narrative_alerts=narrative,
        flight_alerts=flight,
        adsb_alerts=adsb,
        dry_run=False,
    )

    assert len(events) == 1
    build_graph.assert_called_once()
    record = build_graph.call_args[0][0]
    assert record.event_id == events[0]["event_id"]
    assert record.entity == "Lockheed Martin"
    assert set(record.signals) == set(events[0]["signals"])


async def test_run_correlator_persists_graph_nodes_and_edges(tmp_db_path, tmp_path, monkeypatch):
    conn = init_db(tmp_db_path)
    monkeypatch.setattr(db, "DB_PATH", tmp_db_path)
    _write_entities(tmp_path, monkeypatch)
    sec, narrative, flight, adsb = _lockheed_multi_signal_inputs()

    upserted_nodes: list = []
    upserted_edges: list = []
    real_upsert_node = db.upsert_graph_node
    real_upsert_edge = db.upsert_graph_edge

    def _track_node(node):
        upserted_nodes.append(node)
        return real_upsert_node(node)

    def _track_edge(edge):
        upserted_edges.append(edge)
        return real_upsert_edge(edge)

    monkeypatch.setattr(db, "upsert_graph_node", _track_node)
    monkeypatch.setattr(db, "upsert_graph_edge", _track_edge)

    await run_correlator(
        conn,
        sec_alerts=sec,
        narrative_alerts=narrative,
        flight_alerts=flight,
        adsb_alerts=adsb,
        dry_run=False,
    )

    # entity, event, signal:sec, signal:flight, signal:adsb,
    # callsign:EXEC1, callsign:SPAR1, facility:DARPA HQ
    assert len(upserted_nodes) == 8
    # involved_in + contributed_to×3 + participated_in×2 + observed_via×2 + observed_near×2
    assert len(upserted_edges) == 10
    node_types = {n.node_type for n in upserted_nodes}
    assert node_types == {"entity", "event", "signal", "callsign", "facility"}


async def test_run_correlator_graph_failure_does_not_block_correlation(
    tmp_db_path, tmp_path, monkeypatch
):
    conn = init_db(tmp_db_path)
    monkeypatch.setattr(db, "DB_PATH", tmp_db_path)
    _write_entities(tmp_path, monkeypatch)
    sec, narrative, flight, adsb = _lockheed_multi_signal_inputs()

    def _boom(_record):
        raise RuntimeError("graph build failed")

    monkeypatch.setattr(correlator_module, "build_graph_from_correlation", _boom)

    events = await run_correlator(
        conn,
        sec_alerts=sec,
        narrative_alerts=narrative,
        flight_alerts=flight,
        adsb_alerts=adsb,
        dry_run=False,
    )

    assert len(events) == 1
    rows = db.fetch_correlations(limit=5)
    assert len(rows) == 1
    assert rows[0]["event_id"] == events[0]["event_id"]


async def test_run_correlator_rerun_does_not_repeat_graph_persistence(
    tmp_db_path, tmp_path, monkeypatch
):
    conn = init_db(tmp_db_path)
    monkeypatch.setattr(db, "DB_PATH", tmp_db_path)
    _write_entities(tmp_path, monkeypatch)
    sec, narrative, flight, adsb = _lockheed_multi_signal_inputs()

    build_graph = Mock(side_effect=correlator_module.build_graph_from_correlation)
    monkeypatch.setattr(correlator_module, "build_graph_from_correlation", build_graph)

    await run_correlator(
        conn,
        sec_alerts=sec,
        narrative_alerts=narrative,
        flight_alerts=flight,
        adsb_alerts=adsb,
        dry_run=False,
    )
    await run_correlator(
        conn,
        sec_alerts=sec,
        narrative_alerts=narrative,
        flight_alerts=flight,
        adsb_alerts=adsb,
        dry_run=False,
    )

    build_graph.assert_called_once()


def test_correlate_sec_flight_still_creates_event(tmp_path, monkeypatch):
    _write_entities(tmp_path, monkeypatch)
    events = correlate(
        sec_alerts=[
            {"company": "Lockheed Martin Corp", "filed_at": "2026-04-01", "accession_no": "1"}
        ],
        narrative_alerts=[],
        flight_alerts=[{"facility": "DARPA HQ", "icao24": "abc", "callsign": "EXEC1"}],
        adsb_alerts=[],
    )
    assert len(events) == 1
    assert events[0]["score"] >= 6.0
    assert 0 <= events[0]["score"] <= 10


def test_correlate_multi_signal_military_scores_higher_than_sec_flight(tmp_path, monkeypatch):
    _write_entities(tmp_path, monkeypatch)
    sec = [{"company": "Lockheed Martin Corp", "filed_at": "2026-04-01", "accession_no": "1"}]
    flight = [{"facility": "DARPA HQ", "icao24": "abc", "callsign": "EXEC1"}]
    sec_flight = correlate(
        sec_alerts=sec,
        narrative_alerts=[],
        flight_alerts=flight,
        adsb_alerts=[],
    )
    sec_flight_adsb = correlate(
        sec_alerts=sec,
        narrative_alerts=[],
        flight_alerts=flight,
        adsb_alerts=[
            {
                "facility": "DARPA HQ",
                "hex": "AE1234",
                "callsign": "PAT16",
                "military": True,
                "military_style_callsign": True,
            }
        ],
    )
    assert len(sec_flight) == 1
    assert len(sec_flight_adsb) == 1
    assert sec_flight_adsb[0]["score"] > sec_flight[0]["score"]


def test_correlate_sec_only_stays_below_publishable_threshold(tmp_path, monkeypatch):
    _write_entities(tmp_path, monkeypatch)
    events = correlate(
        sec_alerts=[
            {"company": "Lockheed Martin Corp", "filed_at": "2026-04-01", "accession_no": "1"}
        ],
        narrative_alerts=[],
        flight_alerts=[],
        adsb_alerts=[],
        score_threshold=0.0,
    )
    assert len(events) == 1
    assert events[0]["score"] < 7.0


def test_correlate_score_is_deterministic(tmp_path, monkeypatch):
    _write_entities(tmp_path, monkeypatch)
    kwargs = {
        "sec_alerts": [
            {"company": "Lockheed Martin Corp", "filed_at": "2026-04-01", "accession_no": "1"}
        ],
        "narrative_alerts": [],
        "flight_alerts": [{"facility": "DARPA HQ", "icao24": "abc", "callsign": "EXEC1"}],
        "adsb_alerts": [
            {"facility": "DARPA HQ", "hex": "AE1234", "callsign": "SPAR1", "military": True}
        ],
    }
    first = correlate(**kwargs)
    second = correlate(**kwargs)
    assert first[0]["score"] == second[0]["score"]


async def test_run_correlator_persists_weighted_score(tmp_db_path, tmp_path, monkeypatch):
    conn = init_db(tmp_db_path)
    monkeypatch.setattr(db, "DB_PATH", tmp_db_path)
    _write_entities(tmp_path, monkeypatch)
    sec, narrative, flight, adsb = _lockheed_multi_signal_inputs()

    events = await run_correlator(
        conn,
        sec_alerts=sec,
        narrative_alerts=narrative,
        flight_alerts=flight,
        adsb_alerts=adsb,
        dry_run=False,
    )
    rows = db.fetch_correlations(limit=5)
    assert len(rows) == 1
    assert rows[0]["score"] == events[0]["score"]
    assert 0 <= rows[0]["score"] <= 10
