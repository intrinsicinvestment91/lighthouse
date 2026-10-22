import json
from unittest.mock import Mock

from modules import graph_backfill
from modules.correlations import CorrelationRecord
from modules.graph_builder import build_graph_from_correlation


def _sample_row(**overrides):
    row = {
        "id": 1,
        "event_id": "evt-123",
        "event_type": "sec_adsb",
        "entity": "Tesla",
        "score": 8.0,
        "signals": ["sec", "adsb"],
        "evidence": {"sec": {"accession_no": "0001"}},
        "observed_at": 1_777_000_000,
    }
    row.update(overrides)
    return row


def _sample_graph():
    record = CorrelationRecord(
        event_id="evt-123",
        event_type="sec_adsb",
        entity="Tesla",
        score=8.0,
        signals=["sec", "adsb"],
        evidence={"sec": {"accession_no": "0001"}},
    )
    return build_graph_from_correlation(record)


def test_correlation_row_to_record_basic():
    record = graph_backfill.correlation_row_to_record(_sample_row())

    assert record.event_id == "evt-123"
    assert record.event_type == "sec_adsb"
    assert record.entity == "Tesla"
    assert record.score == 8.0
    assert set(record.signals) == {"sec", "adsb"}
    assert record.evidence == {"sec": {"accession_no": "0001"}}
    assert record.observed_at == "1777000000"


def test_correlation_row_to_record_parses_json_fallbacks():
    row = {
        "id": 2,
        "event_id": "evt-json",
        "event_type": "sec_flight",
        "score": 6.5,
        "signals_json": json.dumps(["sec", "flight"]),
        "evidence_json": json.dumps({"sec": {"company": "Acme"}}),
        "entities_json": json.dumps(["Acme Corp"]),
    }
    record = graph_backfill.correlation_row_to_record(row)

    assert record.event_id == "evt-json"
    assert record.entity == "Acme Corp"
    assert set(record.signals) == {"sec", "flight"}
    assert record.evidence == {"sec": {"company": "Acme"}}


def test_correlation_row_to_record_fallback_event_id():
    row = _sample_row(event_id="", id=42)
    record = graph_backfill.correlation_row_to_record(row)

    assert record.event_id == "correlation:42"


def test_persist_graph_dry_run_validates_without_upsert(monkeypatch):
    init_graph_tables = Mock()
    upsert_node = Mock()
    upsert_edge = Mock()
    monkeypatch.setattr(graph_backfill.db, "init_graph_tables", init_graph_tables)
    monkeypatch.setattr(graph_backfill.db, "upsert_graph_node", upsert_node)
    monkeypatch.setattr(graph_backfill.db, "upsert_graph_edge", upsert_edge)

    counts = graph_backfill.persist_graph(_sample_graph(), dry_run=True)

    assert counts == {"nodes": 4, "edges": 3}
    init_graph_tables.assert_not_called()
    upsert_node.assert_not_called()
    upsert_edge.assert_not_called()


def test_persist_graph_upserts_nodes_and_edges(monkeypatch):
    init_graph_tables = Mock()
    upsert_node = Mock()
    upsert_edge = Mock()
    monkeypatch.setattr(graph_backfill.db, "init_graph_tables", init_graph_tables)
    monkeypatch.setattr(graph_backfill.db, "upsert_graph_node", upsert_node)
    monkeypatch.setattr(graph_backfill.db, "upsert_graph_edge", upsert_edge)

    counts = graph_backfill.persist_graph(_sample_graph(), dry_run=False)

    assert counts == {"nodes": 4, "edges": 3}
    init_graph_tables.assert_called_once_with()
    assert upsert_node.call_count == 4
    assert upsert_edge.call_count == 3


def test_backfill_correlation_graphs_success(monkeypatch):
    rows = [_sample_row(), _sample_row(id=2, event_id="evt-456", entity="Lockheed Martin")]
    fetch = Mock(return_value=rows)
    monkeypatch.setattr(graph_backfill.db, "fetch_correlations", fetch)

    result = graph_backfill.backfill_correlation_graphs(limit=10, dry_run=True)

    fetch.assert_called_once_with(limit=10)
    assert result["correlations_seen"] == 2
    assert result["correlations_backfilled"] == 2
    assert result["correlations_failed"] == 0
    assert result["nodes"] == 8
    assert result["edges"] == 6
    assert result["errors"] == []
    assert result["dry_run"] is True


def test_backfill_correlation_graphs_fault_isolated(monkeypatch):
    good_row = _sample_row(id=2, event_id="evt-good")
    bad_row = _sample_row(id=1, event_id="evt-bad", score=99.0)
    fetch = Mock(return_value=[bad_row, good_row])
    monkeypatch.setattr(graph_backfill.db, "fetch_correlations", fetch)

    real_convert = graph_backfill.correlation_row_to_record

    def _convert(row):
        if row["event_id"] == "evt-bad":
            raise ValueError("invalid row")
        return real_convert(row)

    monkeypatch.setattr(graph_backfill, "correlation_row_to_record", _convert)

    result = graph_backfill.backfill_correlation_graphs(limit=10, dry_run=True)

    assert result["correlations_seen"] == 2
    assert result["correlations_backfilled"] == 1
    assert result["correlations_failed"] == 1
    assert len(result["errors"]) == 1
    assert result["errors"][0]["correlation"] == "evt-bad"
    assert "invalid row" in result["errors"][0]["error"]


def test_backfill_correlation_graphs_dry_run(monkeypatch):
    rows = [_sample_row()]
    monkeypatch.setattr(graph_backfill.db, "fetch_correlations", Mock(return_value=rows))
    init_graph_tables = Mock()
    upsert_node = Mock()
    upsert_edge = Mock()
    monkeypatch.setattr(graph_backfill.db, "init_graph_tables", init_graph_tables)
    monkeypatch.setattr(graph_backfill.db, "upsert_graph_node", upsert_node)
    monkeypatch.setattr(graph_backfill.db, "upsert_graph_edge", upsert_edge)

    result = graph_backfill.backfill_correlation_graphs(limit=5, dry_run=True)

    assert result["correlations_backfilled"] == 1
    assert result["nodes"] == 4
    assert result["edges"] == 3
    init_graph_tables.assert_not_called()
    upsert_node.assert_not_called()
    upsert_edge.assert_not_called()


def test_backfill_with_callsign_evidence_produces_callsign_nodes(monkeypatch):
    # PAT is a known military prefix: callsign:PAT16 + facility:DARPA HQ + prefix:PAT nodes
    # Edges: participated_in + observed_via:flight + observed_near + has_prefix = 4 extra edges
    row = _sample_row(
        event_id="evt-cs",
        entity="Lockheed Martin",
        signals=["sec", "flight"],
        evidence={
            "sec": {"accession_no": "001"},
            "flight": [{"callsign": "PAT16", "facility": "DARPA HQ"}],
            "adsb": [],
        },
    )
    monkeypatch.setattr(graph_backfill.db, "fetch_correlations", Mock(return_value=[row]))

    result = graph_backfill.backfill_correlation_graphs(limit=1, dry_run=True)

    assert result["correlations_backfilled"] == 1
    # entity + event + signal:sec + signal:flight + callsign:PAT16 + facility:DARPA HQ + prefix:PAT
    assert result["nodes"] == 7
    # involved_in + contributed_to×2 + participated_in + observed_via + observed_near + has_prefix
    assert result["edges"] == 7
