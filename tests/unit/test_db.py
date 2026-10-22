from datetime import UTC, datetime, timedelta

from modules import db
from modules.alerts import ALERT_SCHEMA_VERSION
from modules.callsigns import enrich_callsign
from modules.graph import GraphEdge, GraphNode


def test_db_connection(tmp_path, monkeypatch):
    test_db_path = tmp_path / "state.db"
    monkeypatch.setattr(db, "DB_PATH", test_db_path)
    conn = db.get_connection()
    assert conn is not None


def test_fetch_alerts_and_source_filter(tmp_path, monkeypatch):
    test_db_path = tmp_path / "state.db"
    monkeypatch.setattr(db, "DB_PATH", test_db_path)

    db.init_alerts_table()
    expected_source = "unit-test-source"
    expected_alert = {"kind": "velocity_spike", "severity": "high"}
    db.insert_alert(expected_source, expected_alert)

    alerts = db.fetch_alerts()
    assert len(alerts) >= 1
    latest = alerts[0]
    assert isinstance(latest["alert"], dict)
    assert latest["alert"] == expected_alert
    assert latest["source"] == expected_source

    filtered_alerts = db.fetch_alerts_by_source(expected_source)
    assert len(filtered_alerts) >= 1
    assert all(alert["source"] == expected_source for alert in filtered_alerts)


def test_fetch_alerts_score_helpers(tmp_path, monkeypatch):
    test_db_path = tmp_path / "state.db"
    monkeypatch.setattr(db, "DB_PATH", test_db_path)

    db.init_alerts_table()
    db.insert_alert("scoring-source", {"score": 0.2, "score_label": "low"})
    db.insert_alert("scoring-source", {"score": 0.6, "score_label": "medium"})
    db.insert_alert("scoring-source", {"score": 0.9, "score_label": "high"})
    db.insert_alert("scoring-source", {"score": 0.95, "score_label": "high"})

    high_by_score = db.fetch_alerts_min_score(0.75)
    assert len(high_by_score) >= 1
    assert all(float(alert["alert"].get("score", 0.0)) >= 0.75 for alert in high_by_score)

    high_by_label = db.fetch_alerts_by_label("high")
    assert len(high_by_label) == 2
    assert all(alert["alert"].get("score_label") == "high" for alert in high_by_label)

    limited_high_by_score = db.fetch_alerts_min_score(0.75, limit=1)
    assert len(limited_high_by_score) == 1

    limited_high_by_label = db.fetch_alerts_by_label("high", limit=1)
    assert len(limited_high_by_label) == 1


def test_fetch_alerts_score_helpers_since_days(tmp_path, monkeypatch):
    test_db_path = tmp_path / "state.db"
    monkeypatch.setattr(db, "DB_PATH", test_db_path)
    db.init_alerts_table()

    old_time = (datetime.now(UTC) - timedelta(days=25)).isoformat()
    with db.get_connection() as conn:
        conn.execute(
            """
            INSERT INTO alerts (
                source,
                created_at,
                alert_json,
                score,
                score_label,
                entity,
                alert_type,
                schema_version
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "scoring-source",
                old_time,
                '{"score": 0.99, "score_label": "high", "kind": "old"}',
                0.99,
                "high",
                "OldCorp",
                "old_type",
                ALERT_SCHEMA_VERSION,
            ),
        )

    db.insert_alert("scoring-source", {"score": 0.9, "score_label": "high", "kind": "recent"})
    db.insert_alert("scoring-source", {"score": 0.6, "score_label": "medium", "kind": "recent-low"})

    recent_by_score = db.fetch_alerts_min_score(0.75, since_days=7)
    assert len(recent_by_score) == 1
    assert all(float(alert["alert"].get("score", 0.0)) >= 0.75 for alert in recent_by_score)
    assert recent_by_score[0]["alert"]["kind"] == "recent"

    recent_by_label = db.fetch_alerts_by_label("high", since_days=7)
    assert len(recent_by_label) == 1
    assert all(alert["alert"].get("score_label") == "high" for alert in recent_by_label)
    assert recent_by_label[0]["alert"]["kind"] == "recent"


def test_fetch_alerts_since_days_filters_old(tmp_path, monkeypatch):
    test_db_path = tmp_path / "state.db"
    monkeypatch.setattr(db, "DB_PATH", test_db_path)
    db.init_alerts_table()

    old_time = (datetime.now(UTC) - timedelta(days=25)).isoformat()
    with db.get_connection() as conn:
        conn.execute(
            "INSERT INTO alerts (source, created_at, alert_json) VALUES (?, ?, ?)",
            ("src", old_time, '{"msg": "old"}'),
        )

    db.insert_alert("src", {"msg": "recent"})

    all_alerts = db.fetch_alerts(limit=100)
    assert len(all_alerts) == 2

    recent_only = db.fetch_alerts(since_days=7)
    assert len(recent_only) == 1
    assert recent_only[0]["alert"]["msg"] == "recent"


def test_fetch_alerts_by_source_since_days(tmp_path, monkeypatch):
    test_db_path = tmp_path / "state.db"
    monkeypatch.setattr(db, "DB_PATH", test_db_path)
    db.init_alerts_table()

    old_time = (datetime.now(UTC) - timedelta(days=25)).isoformat()
    with db.get_connection() as conn:
        conn.execute(
            "INSERT INTO alerts (source, created_at, alert_json) VALUES (?, ?, ?)",
            ("src", old_time, '{"msg": "old"}'),
        )

    db.insert_alert("src", {"msg": "recent"})

    recent_only = db.fetch_alerts_by_source("src", since_days=7)
    assert len(recent_only) == 1
    assert recent_only[0]["alert"]["msg"] == "recent"


def test_find_related_alerts_cross_source(tmp_path, monkeypatch):
    test_db_path = tmp_path / "state.db"
    monkeypatch.setattr(db, "DB_PATH", test_db_path)

    db.init_alerts_table()
    db.insert_alert("sec", {"company": "tesla", "form_type": "8-K"})
    db.insert_alert("velocity", {"assignee": "Tesla", "kind": "velocity_spike"})
    db.insert_alert("sec", {"company": "Teslarati", "form_type": "8-K"})
    db.insert_alert("arxiv", {"researcher": "Other", "metadata": {"topic": "fusion"}})

    results = db.find_related_alerts("Tesla")
    assert len(results) == 2
    assert any(alert["alert"].get("company") == "tesla" for alert in results)
    assert any(alert["alert"].get("assignee") == "Tesla" for alert in results)
    assert all(alert["source"] != "arxiv" for alert in results)
    assert all("Teslarati" not in str(r["alert"]) for r in results)

    limited_results = db.find_related_alerts("Tesla", limit=1)
    assert len(limited_results) == 1


def test_find_related_alerts_exact_entity_matching(tmp_path, monkeypatch):
    test_db_path = tmp_path / "state.db"
    monkeypatch.setattr(db, "DB_PATH", test_db_path)
    db.init_alerts_table()

    db.insert_alert("sec", {"company": "tesla", "form_type": "8-K"})
    db.insert_alert("sec", {"company": "Teslarati", "form_type": "8-K"})

    results = db.find_related_alerts("Tesla")
    assert len(results) == 1
    assert results[0]["alert"].get("company") == "tesla"


def test_insert_alert_populates_structured_columns_and_keeps_json(tmp_path, monkeypatch):
    test_db_path = tmp_path / "state.db"
    monkeypatch.setattr(db, "DB_PATH", test_db_path)
    db.init_alerts_table()

    velocity_alert = {
        "type": "spike",
        "assignee": "Tesla",
        "score": 0.9,
        "score_label": "high",
    }
    sec_alert = {
        "form_type": "8-K",
        "company": "LOCKHEED MARTIN CORP",
        "score": 0.8,
        "score_label": "high",
    }

    db.insert_alert("velocity_tracker", velocity_alert)
    db.insert_alert("sec_sentinel", sec_alert)

    velocity_row = db.fetchone(
        "SELECT source, score, score_label, entity, alert_type, schema_version "
        "FROM alerts WHERE source = ?",
        ("velocity_tracker",),
    )
    assert velocity_row is not None
    assert velocity_row["score"] == 0.9
    assert velocity_row["score_label"] == "high"
    assert velocity_row["entity"] == "Tesla"
    assert velocity_row["alert_type"] == "spike"
    assert velocity_row["schema_version"] == ALERT_SCHEMA_VERSION

    sec_row = db.fetchone(
        "SELECT source, score, score_label, entity, alert_type, schema_version "
        "FROM alerts WHERE source = ?",
        ("sec_sentinel",),
    )
    assert sec_row is not None
    assert sec_row["score"] == 0.8
    assert sec_row["score_label"] == "high"
    assert sec_row["entity"] == "LOCKHEED MARTIN CORP"
    assert sec_row["alert_type"] == "8-K"
    assert sec_row["schema_version"] == ALERT_SCHEMA_VERSION

    fetched = db.fetch_alerts(limit=10)
    assert any(r["alert"] == velocity_alert for r in fetched)
    assert any(r["alert"] == sec_alert for r in fetched)


def test_schema_version_defaults_and_version_query_helper(tmp_path, monkeypatch):
    test_db_path = tmp_path / "state.db"
    monkeypatch.setattr(db, "DB_PATH", test_db_path)
    db.init_alerts_table()

    db.insert_alert("v1-source", {"type": "spike", "score": 0.9})
    db.insert_alert(
        "v2-source",
        {"type": "spike", "score": 0.95, "schema_version": 2},
    )

    default_row = db.fetchone(
        "SELECT schema_version FROM alerts WHERE source = ?",
        ("v1-source",),
    )
    assert default_row is not None
    assert default_row["schema_version"] == ALERT_SCHEMA_VERSION

    v1_alerts = db.fetch_alerts_by_schema_version(ALERT_SCHEMA_VERSION)
    assert len(v1_alerts) == 1
    assert v1_alerts[0]["source"] == "v1-source"
    assert v1_alerts[0]["alert"].get("schema_version") is None

    v2_alerts = db.fetch_alerts_by_schema_version(2)
    assert len(v2_alerts) == 1
    assert v2_alerts[0]["source"] == "v2-source"
    assert v2_alerts[0]["alert"]["schema_version"] == 2


def test_init_alerts_table_adds_missing_columns_for_existing_table(tmp_path, monkeypatch):
    test_db_path = tmp_path / "state.db"
    monkeypatch.setattr(db, "DB_PATH", test_db_path)

    with db.get_connection() as conn:
        conn.execute(
            """
            CREATE TABLE alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source TEXT,
                created_at TEXT,
                alert_json TEXT
            )
            """
        )

    db.init_alerts_table()
    columns = db._get_table_columns("alerts")

    assert "score" in columns
    assert "score_label" in columns
    assert "entity" in columns
    assert "alert_type" in columns
    assert "schema_version" in columns
    assert "dedup_hash" in columns


def test_insert_alert_deduplication_skips_identical_alert(tmp_path, monkeypatch):
    test_db_path = tmp_path / "state.db"
    monkeypatch.setattr(db, "DB_PATH", test_db_path)
    db.init_alerts_table()

    alert = {"type": "spike", "ipc_class": "H01M", "week": "2026-W17", "score": 0.8}
    first = db.insert_alert("velocity_tracker", alert)
    second = db.insert_alert("velocity_tracker", alert)

    assert first is True
    assert second is False

    rows = db.fetch_alerts(limit=100)
    assert len(rows) == 1


def test_insert_alert_different_content_not_deduped(tmp_path, monkeypatch):
    test_db_path = tmp_path / "state.db"
    monkeypatch.setattr(db, "DB_PATH", test_db_path)
    db.init_alerts_table()

    db.insert_alert("velocity_tracker", {"type": "spike", "ipc_class": "H01M", "score": 0.8})
    db.insert_alert("velocity_tracker", {"type": "freeze", "ipc_class": "H01M", "score": 0.6})

    rows = db.fetch_alerts(limit=100)
    assert len(rows) == 2


def test_insert_alert_different_sources_not_deduped(tmp_path, monkeypatch):
    test_db_path = tmp_path / "state.db"
    monkeypatch.setattr(db, "DB_PATH", test_db_path)
    db.init_alerts_table()

    alert = {"type": "spike", "ipc_class": "H01M", "score": 0.8}
    db.insert_alert("source_a", alert)
    db.insert_alert("source_b", alert)

    rows = db.fetch_alerts(limit=100)
    assert len(rows) == 2


def test_insert_alert_provenance_excluded_from_dedup_hash(tmp_path, monkeypatch):
    test_db_path = tmp_path / "state.db"
    monkeypatch.setattr(db, "DB_PATH", test_db_path)
    db.init_alerts_table()

    base = {"type": "spike", "ipc_class": "H01M", "score": 0.8}
    alert_with_prov = {**base, "provenance": {"retrieved_at": "2026-01-01T00:00:00"}}
    alert_diff_prov = {**base, "provenance": {"retrieved_at": "2026-04-27T12:00:00"}}

    first = db.insert_alert("velocity_tracker", alert_with_prov)
    second = db.insert_alert("velocity_tracker", alert_diff_prov)

    assert first is True
    assert second is False
    rows = db.fetch_alerts(limit=100)
    assert len(rows) == 1


def test_insert_alert_sec_dedup_key_skips_duplicate(tmp_path, monkeypatch):
    test_db_path = tmp_path / "state.db"
    monkeypatch.setattr(db, "DB_PATH", test_db_path)
    db.init_alerts_table()

    dedup_key = "sec:0000936395-26-000042:lockheed martin corp:sec_8k"
    alert_a = {
        "accession_no": "0000936395-26-000042",
        "company": "LOCKHEED MARTIN CORP",
        "alert_type": "sec_8k",
        "items": "1.01",
        "description": "8-K items: 1.01",
        "score": 0.9,
        "score_label": "high",
        "provenance": {"retrieved_at": "2026-01-01T00:00:00"},
    }
    alert_b = {
        **alert_a,
        "items": "1.01,7.01",
        "description": "8-K items: 1.01,7.01",
        "provenance": {"retrieved_at": "2026-04-27T12:00:00"},
    }

    first = db.insert_alert("sec_sentinel", alert_a, dedup_key=dedup_key)
    second = db.insert_alert("sec_sentinel", alert_b, dedup_key=dedup_key)

    assert first is True
    assert second is False
    rows = db.fetch_alerts_by_source("sec_sentinel", limit=100)
    assert len(rows) == 1


def test_insert_alert_sec_dedup_key_different_filings_both_persist(tmp_path, monkeypatch):
    test_db_path = tmp_path / "state.db"
    monkeypatch.setattr(db, "DB_PATH", test_db_path)
    db.init_alerts_table()

    db.insert_alert(
        "sec_sentinel",
        {"accession_no": "0000936395-26-000042", "company": "Lockheed", "alert_type": "sec_8k"},
        dedup_key="sec:0000936395-26-000042:lockheed:sec_8k",
    )
    db.insert_alert(
        "sec_sentinel",
        {"accession_no": "0000936395-26-000043", "company": "Lockheed", "alert_type": "sec_8k"},
        dedup_key="sec:0000936395-26-000043:lockheed:sec_8k",
    )

    rows = db.fetch_alerts_by_source("sec_sentinel", limit=100)
    assert len(rows) == 2


def test_insert_alert_without_dedup_key_non_sec_unaffected(tmp_path, monkeypatch):
    test_db_path = tmp_path / "state.db"
    monkeypatch.setattr(db, "DB_PATH", test_db_path)
    db.init_alerts_table()

    db.insert_alert("velocity_tracker", {"type": "spike", "ipc_class": "H01M", "score": 0.8})
    db.insert_alert("velocity_tracker", {"type": "freeze", "ipc_class": "H01M", "score": 0.6})

    rows = db.fetch_alerts_by_source("velocity_tracker", limit=100)
    assert len(rows) == 2


def test_init_correlation_table_creates_expected_columns(tmp_path, monkeypatch):
    test_db_path = tmp_path / "state.db"
    monkeypatch.setattr(db, "DB_PATH", test_db_path)

    db.init_correlation_table()
    columns = db._get_table_columns("correlation_events")
    assert {
        "id",
        "event_id",
        "event_type",
        "entity",
        "entities_json",
        "signals_json",
        "evidence_json",
        "score",
        "detected_at",
        "observed_at",
        "sec_filed_at",
        "nostr_event_id",
        "dedup_hash",
        "verification_status",
        "source_trust_score",
        "is_single_source",
        "probation_expires_at",
        "promoted_at",
        "witness_hash",
        "witness_signature",
    } <= columns


def test_insert_correlation_supports_event_fields_and_deduplicates(tmp_path, monkeypatch):
    test_db_path = tmp_path / "state.db"
    monkeypatch.setattr(db, "DB_PATH", test_db_path)
    db.init_correlation_table()

    first = db.insert_correlation(
        event_id="evt-1",
        event_type="sec_flight",
        entity="Lockheed Martin",
        entities=["Lockheed Martin"],
        signals=["sec", "flight"],
        evidence={"sec": {"company": "LOCKHEED MARTIN CORP"}},
        score=6.5,
        sec_filed_at="2026-04-01",
        observed_at=1_777_000_000,
    )
    second = db.insert_correlation(
        event_id="evt-1",
        event_type="sec_flight",
        entity="Lockheed Martin",
        entities=["Lockheed Martin"],
        signals=["sec", "flight"],
        evidence={"sec": {"company": "LOCKHEED MARTIN CORP"}},
        score=6.5,
        sec_filed_at="2026-04-01",
        observed_at=1_777_000_000,
    )

    assert first is True
    assert second is False
    rows = db.fetch_correlations(limit=10)
    assert len(rows) == 1
    assert rows[0]["event_id"] == "evt-1"
    assert rows[0]["entity"] == "Lockheed Martin"
    assert rows[0]["signals"] == ["flight", "sec"]
    assert rows[0]["sec_filed_at"] == "2026-04-01"
    assert rows[0]["observed_at"] == 1_777_000_000


def test_fetch_correlation_by_id_supports_int_and_event_id(tmp_path, monkeypatch):
    test_db_path = tmp_path / "state.db"
    monkeypatch.setattr(db, "DB_PATH", test_db_path)
    db.init_correlation_table()
    db.insert_correlation(
        event_id="evt-lookup",
        event_type="sec_flight",
        entities=["Lockheed Martin"],
        signals=["sec", "flight"],
        evidence={"sec": {"company": "LOCKHEED MARTIN CORP"}},
        score=6.5,
    )

    rows = db.fetch_correlations(limit=1)
    by_int = db.fetch_correlation_by_id(rows[0]["id"])
    by_event_id = db.fetch_correlation_by_id("evt-lookup")

    assert by_int is not None
    assert by_event_id is not None
    assert by_int["event_id"] == "evt-lookup"
    assert by_event_id["event_id"] == "evt-lookup"


def test_init_graph_tables_creates_tables(tmp_path, monkeypatch):
    test_db_path = tmp_path / "state.db"
    monkeypatch.setattr(db, "DB_PATH", test_db_path)

    db.init_graph_tables()
    columns_nodes = db._get_table_columns("graph_nodes")
    columns_edges = db._get_table_columns("graph_edges")

    assert {"node_id", "node_type", "label", "properties_json", "schema_version"} <= columns_nodes
    assert {
        "edge_id",
        "source_id",
        "target_id",
        "relation",
        "weight",
        "properties_json",
    } <= columns_edges


def test_upsert_graph_node_and_fetch_graph_node(tmp_path, monkeypatch):
    test_db_path = tmp_path / "state.db"
    monkeypatch.setattr(db, "DB_PATH", test_db_path)
    db.init_graph_tables()

    node = GraphNode(
        node_id="entity:tesla",
        node_type="entity",
        label="Tesla",
        properties={"sector": "energy"},
    )
    db.upsert_graph_node(node)

    fetched = db.fetch_graph_node("entity:tesla")
    assert fetched is not None
    assert fetched["node_id"] == "entity:tesla"
    assert fetched["node_type"] == "entity"
    assert fetched["label"] == "Tesla"
    assert fetched["properties"] == {"sector": "energy"}


def test_upsert_graph_edge_persists_row(tmp_path, monkeypatch):
    test_db_path = tmp_path / "state.db"
    monkeypatch.setattr(db, "DB_PATH", test_db_path)
    db.init_graph_tables()

    edge = GraphEdge(
        edge_id="entity:tesla->event:evt1",
        source_id="entity:tesla",
        target_id="event:evt1",
        relation="involved_in",
        weight=0.7,
        properties={"kind": "correlation"},
    )
    db.upsert_graph_edge(edge)

    row = db.fetchone("SELECT * FROM graph_edges WHERE edge_id = ?", (edge.edge_id,))
    assert row is not None
    assert row["source_id"] == "entity:tesla"
    assert row["target_id"] == "event:evt1"
    assert row["relation"] == "involved_in"
    assert row["weight"] == 0.7


def test_upsert_graph_node_updates_existing_row(tmp_path, monkeypatch):
    test_db_path = tmp_path / "state.db"
    monkeypatch.setattr(db, "DB_PATH", test_db_path)
    db.init_graph_tables()

    db.upsert_graph_node(
        GraphNode(node_id="entity:tesla", node_type="entity", label="Tesla", properties={"a": 1})
    )
    db.upsert_graph_node(
        GraphNode(
            node_id="entity:tesla",
            node_type="entity",
            label="Tesla Inc",
            properties={"a": 2},
        )
    )

    fetched = db.fetch_graph_node("entity:tesla")
    assert fetched is not None
    assert fetched["label"] == "Tesla Inc"
    assert fetched["properties"] == {"a": 2}


def test_fetch_graph_neighbors_returns_connected_edges(tmp_path, monkeypatch):
    test_db_path = tmp_path / "state.db"
    monkeypatch.setattr(db, "DB_PATH", test_db_path)
    db.init_graph_tables()

    db.upsert_graph_edge(
        GraphEdge(
            edge_id="entity:tesla->event:evt1",
            source_id="entity:tesla",
            target_id="event:evt1",
            relation="involved_in",
            weight=0.8,
            properties={"p": 1},
        )
    )
    db.upsert_graph_edge(
        GraphEdge(
            edge_id="signal:sec->event:evt1",
            source_id="signal:sec",
            target_id="event:evt1",
            relation="contributed_to",
            weight=1.0,
            properties={"p": 2},
        )
    )
    db.upsert_graph_edge(
        GraphEdge(
            edge_id="entity:other->event:evt2",
            source_id="entity:other",
            target_id="event:evt2",
            relation="involved_in",
            weight=0.5,
        )
    )

    neighbors = db.fetch_graph_neighbors("event:evt1")
    edge_ids = {edge["edge_id"] for edge in neighbors}

    assert "entity:tesla->event:evt1" in edge_ids
    assert "signal:sec->event:evt1" in edge_ids
    assert "entity:other->event:evt2" not in edge_ids
    assert all(isinstance(edge["properties"], dict) for edge in neighbors)


def test_upsert_graph_validation_errors_bubble_up(tmp_path, monkeypatch):
    test_db_path = tmp_path / "state.db"
    monkeypatch.setattr(db, "DB_PATH", test_db_path)
    db.init_graph_tables()

    invalid_node = GraphNode(node_id="", node_type="entity", label="Bad")
    invalid_edge = GraphEdge(
        edge_id="bad",
        source_id="source",
        target_id="target",
        relation="rel",
        weight=1.5,
    )

    try:
        db.upsert_graph_node(invalid_node)
        raise AssertionError("Expected ValueError for invalid node")
    except ValueError:
        pass

    try:
        db.upsert_graph_edge(invalid_edge)
        raise AssertionError("Expected ValueError for invalid edge")
    except ValueError:
        pass


def test_fetch_graph_neighbors_nodes_returns_nodes(tmp_path, monkeypatch):
    test_db_path = tmp_path / "state.db"
    monkeypatch.setattr(db, "DB_PATH", test_db_path)
    db.init_graph_tables()

    db.upsert_graph_node(GraphNode(node_id="event:evt1", node_type="event", label="evt1"))
    db.upsert_graph_node(GraphNode(node_id="entity:tesla", node_type="entity", label="Tesla"))
    db.upsert_graph_node(GraphNode(node_id="signal:sec", node_type="signal", label="sec"))

    db.upsert_graph_edge(
        GraphEdge(
            edge_id="entity:tesla->event:evt1",
            source_id="entity:tesla",
            target_id="event:evt1",
            relation="involved_in",
            weight=0.8,
        )
    )
    db.upsert_graph_edge(
        GraphEdge(
            edge_id="signal:sec->event:evt1",
            source_id="signal:sec",
            target_id="event:evt1",
            relation="contributed_to",
            weight=1.0,
        )
    )

    neighbors = db.fetch_graph_neighbors_nodes("event:evt1")
    neighbor_ids = {n["node_id"] for n in neighbors}

    assert neighbor_ids == {"entity:tesla", "signal:sec"}
    assert all(isinstance(n["properties"], dict) for n in neighbors)


def test_fetch_graph_edges_between_bidirectional(tmp_path, monkeypatch):
    test_db_path = tmp_path / "state.db"
    monkeypatch.setattr(db, "DB_PATH", test_db_path)
    db.init_graph_tables()

    db.upsert_graph_edge(
        GraphEdge(
            edge_id="a->b",
            source_id="a",
            target_id="b",
            relation="r1",
            weight=0.5,
        )
    )
    db.upsert_graph_edge(
        GraphEdge(
            edge_id="b->a",
            source_id="b",
            target_id="a",
            relation="r2",
            weight=0.6,
        )
    )
    db.upsert_graph_edge(
        GraphEdge(
            edge_id="a->c",
            source_id="a",
            target_id="c",
            relation="r3",
            weight=0.7,
        )
    )

    edges = db.fetch_graph_edges_between("a", "b")
    edge_ids = {e["edge_id"] for e in edges}
    assert edge_ids == {"a->b", "b->a"}


def test_traverse_graph_depth_1(tmp_path, monkeypatch):
    test_db_path = tmp_path / "state.db"
    monkeypatch.setattr(db, "DB_PATH", test_db_path)
    db.init_graph_tables()

    for node_id in ("n1", "n2", "n3"):
        db.upsert_graph_node(GraphNode(node_id=node_id, node_type="entity", label=node_id))

    db.upsert_graph_edge(
        GraphEdge(edge_id="n1->n2", source_id="n1", target_id="n2", relation="linked", weight=0.5)
    )
    db.upsert_graph_edge(
        GraphEdge(edge_id="n2->n3", source_id="n2", target_id="n3", relation="linked", weight=0.5)
    )

    result = db.traverse_graph("n1", depth=1)
    node_ids = {n["node_id"] for n in result["nodes"]}
    edge_ids = {e["edge_id"] for e in result["edges"]}

    assert node_ids == {"n1", "n2"}
    assert edge_ids == {"n1->n2"}


def test_traverse_graph_depth_2_expands(tmp_path, monkeypatch):
    test_db_path = tmp_path / "state.db"
    monkeypatch.setattr(db, "DB_PATH", test_db_path)
    db.init_graph_tables()

    for node_id in ("n1", "n2", "n3", "n4"):
        db.upsert_graph_node(GraphNode(node_id=node_id, node_type="entity", label=node_id))

    db.upsert_graph_edge(
        GraphEdge(edge_id="n1->n2", source_id="n1", target_id="n2", relation="linked", weight=0.5)
    )
    db.upsert_graph_edge(
        GraphEdge(edge_id="n2->n3", source_id="n2", target_id="n3", relation="linked", weight=0.5)
    )
    db.upsert_graph_edge(
        GraphEdge(edge_id="n3->n4", source_id="n3", target_id="n4", relation="linked", weight=0.5)
    )

    result = db.traverse_graph("n1", depth=2)
    node_ids = {n["node_id"] for n in result["nodes"]}
    edge_ids = {e["edge_id"] for e in result["edges"]}

    assert node_ids == {"n1", "n2", "n3"}
    assert "n1->n2" in edge_ids
    assert "n2->n3" in edge_ids
    assert "n3->n4" not in edge_ids
    assert len(node_ids) == len(result["nodes"])


def test_init_callsign_history_table_creates_table(tmp_path, monkeypatch):
    test_db_path = tmp_path / "state.db"
    monkeypatch.setattr(db, "DB_PATH", test_db_path)
    db.init_callsign_history_table()

    columns = db._get_table_columns("callsign_observations")
    assert "normalized_callsign" in columns
    assert "observed_at" in columns

    indexes = db.fetchall(
        "SELECT name FROM sqlite_master WHERE type = 'index' AND tbl_name = 'callsign_observations'"
    )
    index_names = {row["name"] for row in indexes}
    assert "idx_callsign_observations_callsign" in index_names
    assert "idx_callsign_observations_observed_at" in index_names


def test_insert_callsign_observation_persists_row(tmp_path, monkeypatch):
    test_db_path = tmp_path / "state.db"
    monkeypatch.setattr(db, "DB_PATH", test_db_path)
    enrichment = enrich_callsign("PAT16").to_dict()

    row_id = db.insert_callsign_observation(
        callsign_enrichment=enrichment,
        source="adsb_sentinel",
        facility="DARPA HQ",
        icao_hex="ae1234",
        lat=37.1,
        lon=-121.1,
        observed_at=1_700_000_000,
    )

    assert row_id is not None
    history = db.fetch_callsign_history("PAT16", limit=10)
    assert len(history) == 1
    assert history[0]["normalized_callsign"] == "PAT16"
    assert history[0]["facility"] == "DARPA HQ"
    assert history[0]["source"] == "adsb_sentinel"


def test_insert_callsign_observation_without_normalized_callsign_returns_none(
    tmp_path, monkeypatch
):
    test_db_path = tmp_path / "state.db"
    monkeypatch.setattr(db, "DB_PATH", test_db_path)

    row_id = db.insert_callsign_observation(
        callsign_enrichment={"normalized_callsign": None, "tags": []},
        source="adsb_sentinel",
    )

    assert row_id is None
    assert db.fetch_recent_callsigns() == []


def test_fetch_callsign_history_returns_newest_first(tmp_path, monkeypatch):
    test_db_path = tmp_path / "state.db"
    monkeypatch.setattr(db, "DB_PATH", test_db_path)
    enrichment = enrich_callsign("PAT16").to_dict()

    db.insert_callsign_observation(
        callsign_enrichment=enrichment,
        source="adsb_sentinel",
        observed_at=100,
    )
    db.insert_callsign_observation(
        callsign_enrichment=enrichment,
        source="flight_tracker",
        observed_at=200,
    )

    history = db.fetch_callsign_history("pat16", limit=10)
    assert [row["observed_at"] for row in history] == [200, 100]
    assert history[0]["source"] == "flight_tracker"


def test_fetch_recent_callsigns_returns_rows(tmp_path, monkeypatch):
    test_db_path = tmp_path / "state.db"
    monkeypatch.setattr(db, "DB_PATH", test_db_path)
    enrichment = enrich_callsign("MUSL").to_dict()
    db.insert_callsign_observation(
        callsign_enrichment=enrichment,
        source="adsb_sentinel",
        observed_at=50,
    )

    recent = db.fetch_recent_callsigns(limit=5)
    assert len(recent) == 1
    assert recent[0]["normalized_callsign"] == "MUSL"


def test_fetch_callsign_summary_groups_counts(tmp_path, monkeypatch):
    test_db_path = tmp_path / "state.db"
    monkeypatch.setattr(db, "DB_PATH", test_db_path)
    pat = enrich_callsign("PAT16").to_dict()
    musl = enrich_callsign("MUSL").to_dict()

    db.insert_callsign_observation(callsign_enrichment=pat, source="adsb_sentinel", observed_at=100)
    db.insert_callsign_observation(callsign_enrichment=pat, source="adsb_sentinel", observed_at=300)
    db.insert_callsign_observation(
        callsign_enrichment=musl, source="flight_tracker", observed_at=200
    )

    summary = db.fetch_callsign_summary(limit=10)
    by_callsign = {row["normalized_callsign"]: row for row in summary}

    assert by_callsign["PAT16"]["count"] == 2
    assert by_callsign["PAT16"]["first_seen"] == 100
    assert by_callsign["PAT16"]["last_seen"] == 300
    assert by_callsign["PAT16"]["latest_source"] == "adsb_sentinel"
    assert by_callsign["MUSL"]["count"] == 1


def test_callsign_history_json_fields_parse(tmp_path, monkeypatch):
    test_db_path = tmp_path / "state.db"
    monkeypatch.setattr(db, "DB_PATH", test_db_path)
    enrichment = enrich_callsign("ZEUS31").to_dict()

    db.insert_callsign_observation(
        callsign_enrichment=enrichment,
        source="adsb_sentinel",
    )

    row = db.fetch_recent_callsigns(limit=1)[0]
    assert isinstance(row["tags"], list)
    assert isinstance(row["enrichment"], dict)


# ---------------------------------------------------------------------------
# fetch_recurring_callsigns_summary (Packet 26)
# ---------------------------------------------------------------------------


def test_fetch_recurring_callsigns_summary_filters_below_min_observations(tmp_path, monkeypatch):
    test_db_path = tmp_path / "state.db"
    monkeypatch.setattr(db, "DB_PATH", test_db_path)
    enr = enrich_callsign("PAT16").to_dict()

    # Insert only 3 observations (below default min_observations=5)
    for i in range(3):
        db.insert_callsign_observation(
            callsign_enrichment=enr,
            source="adsb_sentinel",
            observed_at=1_767_571_200 + i * 86_400,
        )

    rows = db.fetch_recurring_callsigns_summary(min_observations=5, min_days=1)
    assert rows == []


def test_fetch_recurring_callsigns_summary_filters_below_min_days(tmp_path, monkeypatch):
    test_db_path = tmp_path / "state.db"
    monkeypatch.setattr(db, "DB_PATH", test_db_path)
    enr = enrich_callsign("RCH100").to_dict()

    # 5 observations all on the same second (1 day)
    for i in range(5):
        db.insert_callsign_observation(
            callsign_enrichment=enr,
            source="adsb_sentinel",
            observed_at=1_767_571_200 + i,
        )

    rows = db.fetch_recurring_callsigns_summary(min_observations=5, min_days=3)
    assert rows == []


def test_fetch_recurring_callsigns_summary_returns_qualifying_callsign(tmp_path, monkeypatch):
    test_db_path = tmp_path / "state.db"
    monkeypatch.setattr(db, "DB_PATH", test_db_path)
    enr = enrich_callsign("PAT16").to_dict()
    base_ts = 1_767_571_200

    # 6 observations across 6 days
    for i in range(6):
        db.insert_callsign_observation(
            callsign_enrichment=enr,
            source="adsb_sentinel",
            observed_at=base_ts + i * 86_400,
        )

    rows = db.fetch_recurring_callsigns_summary(min_observations=5, min_days=3)
    assert len(rows) == 1
    assert rows[0]["normalized_callsign"] == "PAT16"
    assert rows[0]["obs_count"] == 6
    assert rows[0]["day_count"] == 6
