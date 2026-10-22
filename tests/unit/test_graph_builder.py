import json

from modules.correlations import CorrelationRecord
from modules.graph_builder import _extract_callsign_sightings, build_graph_from_correlation


def _sample_record() -> CorrelationRecord:
    return CorrelationRecord(
        event_id="evt-123",
        event_type="sec_adsb",
        entity="Tesla",
        score=8.0,
        signals=["sec", "adsb"],
        evidence={"sec": {"accession_no": "0001"}},
    )


def test_build_graph_basic_case_counts_and_nodes_exist():
    graph = build_graph_from_correlation(_sample_record())

    nodes = graph["nodes"]
    edges = graph["edges"]

    assert len(nodes) == 4
    assert len(edges) == 3

    node_ids = {node["node_id"] for node in nodes}
    assert "entity:Tesla" in node_ids
    assert "event:evt-123" in node_ids
    assert "signal:sec" in node_ids
    assert "signal:adsb" in node_ids


def test_build_graph_edge_correctness():
    graph = build_graph_from_correlation(_sample_record())
    edges = graph["edges"]

    edge_ids = {edge["edge_id"] for edge in edges}
    assert "entity:Tesla->event:evt-123" in edge_ids
    assert "signal:sec->event:evt-123" in edge_ids
    assert "signal:adsb->event:evt-123" in edge_ids

    relation_by_edge = {edge["edge_id"]: edge["relation"] for edge in edges}
    assert relation_by_edge["entity:Tesla->event:evt-123"] == "involved_in"
    assert relation_by_edge["signal:sec->event:evt-123"] == "contributed_to"
    assert relation_by_edge["signal:adsb->event:evt-123"] == "contributed_to"


def test_build_graph_weight_is_score_divided_by_ten():
    graph = build_graph_from_correlation(_sample_record())
    entity_event_edge = next(
        edge for edge in graph["edges"] if edge["edge_id"] == "entity:Tesla->event:evt-123"
    )
    assert entity_event_edge["weight"] == 0.8


def test_build_graph_sec_flight_adsb_multi_signal():
    record = CorrelationRecord(
        event_id="evt-multi",
        event_type="sec_adsb_flight",
        entity="Lockheed Martin",
        score=9.0,
        signals=["sec", "flight", "adsb"],
        evidence={"sec": {}, "flight": [], "adsb": []},
    )
    graph = build_graph_from_correlation(record)

    assert len(graph["nodes"]) == 5
    assert len(graph["edges"]) == 4
    node_ids = {node["node_id"] for node in graph["nodes"]}
    assert "entity:Lockheed Martin" in node_ids
    assert "event:evt-multi" in node_ids
    assert "signal:sec" in node_ids
    assert "signal:flight" in node_ids
    assert "signal:adsb" in node_ids


def test_build_graph_deduplicates_duplicate_signal_names():
    record = CorrelationRecord(
        event_id="evt-dedup",
        event_type="sec_flight",
        entity="Tesla",
        score=7.0,
        signals=["sec", "sec", "flight"],
        evidence={},
    )
    graph = build_graph_from_correlation(record)

    assert len(graph["nodes"]) == 4
    assert len(graph["edges"]) == 3
    signal_nodes = [n for n in graph["nodes"] if n["node_type"] == "signal"]
    assert len(signal_nodes) == 2


def test_build_graph_empty_signals_still_has_entity_and_event():
    record = CorrelationRecord(
        event_id="evt-empty",
        event_type="sec_only",
        entity="Acme",
        score=6.0,
        signals=[],
        evidence={},
    )
    graph = build_graph_from_correlation(record)

    assert len(graph["nodes"]) == 2
    assert len(graph["edges"]) == 1
    assert graph["edges"][0]["relation"] == "involved_in"


def test_build_graph_serialization_is_stable():
    record = _sample_record()
    graph_a = build_graph_from_correlation(record)
    graph_b = build_graph_from_correlation(record)
    assert json.dumps(graph_a, sort_keys=True) == json.dumps(graph_b, sort_keys=True)


# ---------------------------------------------------------------------------
# Packet 25 — callsign graph entity tests
# ---------------------------------------------------------------------------

# PAT is in MILITARY_CALLSIGN_PREFIXES → military_style=True → prefix node generated
_FLIGHT_WITH_PAT16 = [{"callsign": "PAT16", "facility": "DARPA HQ"}]
_ADSB_WITH_RCH = [{"callsign": "RCH100", "facility": "Langley"}]


def _record_flight_callsign() -> CorrelationRecord:
    return CorrelationRecord(
        event_id="evt-cs-1",
        event_type="sec_flight",
        entity="Lockheed Martin",
        score=7.5,
        signals=["sec", "flight"],
        evidence={"sec": {}, "flight": _FLIGHT_WITH_PAT16, "adsb": []},
    )


def _record_nonmilitary_adsb() -> CorrelationRecord:
    # "N" prefix is not in MILITARY_CALLSIGN_PREFIXES
    return CorrelationRecord(
        event_id="evt-cs-2",
        event_type="sec_adsb",
        entity="Tesla",
        score=6.5,
        signals=["sec", "adsb"],
        evidence={"adsb": [{"callsign": "N12345", "facility": "San Jose"}]},
    )


# --- extraction helper ---


def test_extract_callsign_sightings_flight_and_adsb():
    evidence = {
        "flight": [{"callsign": "PAT16", "facility": "DARPA HQ"}],
        "adsb": [{"callsign": "RCH100", "facility": "Langley"}],
    }
    sightings = _extract_callsign_sightings(evidence)
    assert len(sightings) == 2
    assert sightings[0] == {"callsign": "PAT16", "facility": "DARPA HQ", "signal": "flight"}
    assert sightings[1] == {"callsign": "RCH100", "facility": "Langley", "signal": "adsb"}


def test_extract_callsign_sightings_empty_callsign_skipped():
    evidence = {"flight": [{"callsign": "", "facility": "DARPA HQ"}]}
    assert _extract_callsign_sightings(evidence) == []


def test_extract_callsign_sightings_missing_key_skipped():
    evidence = {"flight": [{"facility": "DARPA HQ"}]}
    assert _extract_callsign_sightings(evidence) == []


def test_extract_callsign_sightings_empty_lists():
    assert _extract_callsign_sightings({"flight": [], "adsb": []}) == []


def test_extract_callsign_sightings_no_facility_is_none():
    evidence = {"adsb": [{"callsign": "PAT16"}]}
    sightings = _extract_callsign_sightings(evidence)
    assert sightings[0]["facility"] is None


# --- callsign node creation ---


def test_build_graph_flight_callsign_creates_callsign_node():
    graph = build_graph_from_correlation(_record_flight_callsign())
    node_ids = {n["node_id"] for n in graph["nodes"]}
    assert "callsign:PAT16" in node_ids


def test_build_graph_callsign_node_has_correct_type():
    graph = build_graph_from_correlation(_record_flight_callsign())
    cs_node = next(n for n in graph["nodes"] if n["node_id"] == "callsign:PAT16")
    assert cs_node["node_type"] == "callsign"
    assert cs_node["label"] == "PAT16"


def test_build_graph_callsign_node_properties_military():
    graph = build_graph_from_correlation(_record_flight_callsign())
    cs_node = next(n for n in graph["nodes"] if n["node_id"] == "callsign:PAT16")
    assert cs_node["properties"]["military_style"] is True
    assert cs_node["properties"]["known_prefix"] == "PAT"
    assert cs_node["properties"]["normalized_callsign"] == "PAT16"


def test_build_graph_military_callsign_creates_prefix_node():
    graph = build_graph_from_correlation(_record_flight_callsign())
    node_ids = {n["node_id"] for n in graph["nodes"]}
    assert "prefix:PAT" in node_ids
    pfx_node = next(n for n in graph["nodes"] if n["node_id"] == "prefix:PAT")
    assert pfx_node["node_type"] == "callsign_prefix"


def test_build_graph_nonmilitary_callsign_no_prefix_node():
    graph = build_graph_from_correlation(_record_nonmilitary_adsb())
    node_ids = {n["node_id"] for n in graph["nodes"]}
    # No prefix:N node — not a known military prefix
    assert not any(nid.startswith("prefix:") for nid in node_ids)


def test_build_graph_callsign_creates_facility_node():
    graph = build_graph_from_correlation(_record_flight_callsign())
    node_ids = {n["node_id"] for n in graph["nodes"]}
    assert "facility:DARPA HQ" in node_ids
    fac_node = next(n for n in graph["nodes"] if n["node_id"] == "facility:DARPA HQ")
    assert fac_node["node_type"] == "facility"
    assert fac_node["label"] == "DARPA HQ"


def test_build_graph_callsign_no_facility_no_facility_node():
    record = CorrelationRecord(
        event_id="evt-nofac",
        event_type="sec_adsb",
        entity="Acme",
        score=6.0,
        signals=["sec", "adsb"],
        evidence={"adsb": [{"callsign": "RCH1"}]},
    )
    graph = build_graph_from_correlation(record)
    node_ids = {n["node_id"] for n in graph["nodes"]}
    assert not any(nid.startswith("facility:") for nid in node_ids)


# --- edge creation ---


def test_build_graph_callsign_participated_in_edge():
    graph = build_graph_from_correlation(_record_flight_callsign())
    edge_ids = {e["edge_id"] for e in graph["edges"]}
    assert "callsign:PAT16->event:evt-cs-1" in edge_ids
    edge = next(e for e in graph["edges"] if e["edge_id"] == "callsign:PAT16->event:evt-cs-1")
    assert edge["relation"] == "participated_in"


def test_build_graph_callsign_observed_via_edge():
    graph = build_graph_from_correlation(_record_flight_callsign())
    edge_ids = {e["edge_id"] for e in graph["edges"]}
    assert "callsign:PAT16->signal:flight" in edge_ids
    edge = next(e for e in graph["edges"] if e["edge_id"] == "callsign:PAT16->signal:flight")
    assert edge["relation"] == "observed_via"


def test_build_graph_callsign_observed_near_edge():
    graph = build_graph_from_correlation(_record_flight_callsign())
    edge_ids = {e["edge_id"] for e in graph["edges"]}
    assert "callsign:PAT16->facility:DARPA HQ" in edge_ids
    edge = next(e for e in graph["edges"] if e["edge_id"] == "callsign:PAT16->facility:DARPA HQ")
    assert edge["relation"] == "observed_near"


def test_build_graph_callsign_has_prefix_edge():
    graph = build_graph_from_correlation(_record_flight_callsign())
    edge_ids = {e["edge_id"] for e in graph["edges"]}
    assert "callsign:PAT16->prefix:PAT" in edge_ids
    edge = next(e for e in graph["edges"] if e["edge_id"] == "callsign:PAT16->prefix:PAT")
    assert edge["relation"] == "has_prefix"


# --- deduplication ---


def test_build_graph_same_callsign_flight_and_adsb_deduplicates_node():
    record = CorrelationRecord(
        event_id="evt-dedup-cs",
        event_type="sec_adsb_flight",
        entity="Acme",
        score=8.0,
        signals=["adsb", "flight", "sec"],
        evidence={
            "flight": [{"callsign": "RCH100", "facility": "Langley"}],
            "adsb": [{"callsign": "RCH100", "facility": "Langley"}],
        },
    )
    graph = build_graph_from_correlation(record)
    callsign_nodes = [n for n in graph["nodes"] if n["node_type"] == "callsign"]
    assert len(callsign_nodes) == 1
    assert callsign_nodes[0]["node_id"] == "callsign:RCH100"


def test_build_graph_same_callsign_both_signals_gets_two_observed_via_edges():
    record = CorrelationRecord(
        event_id="evt-dedup-cs",
        event_type="sec_adsb_flight",
        entity="Acme",
        score=8.0,
        signals=["adsb", "flight", "sec"],
        evidence={
            "flight": [{"callsign": "RCH100", "facility": "Langley"}],
            "adsb": [{"callsign": "RCH100", "facility": "Langley"}],
        },
    )
    graph = build_graph_from_correlation(record)
    via_edges = [e for e in graph["edges"] if e["relation"] == "observed_via"]
    # One edge for flight, one for adsb
    assert len(via_edges) == 2
    via_targets = {e["target_id"] for e in via_edges}
    assert "signal:flight" in via_targets
    assert "signal:adsb" in via_targets


def test_build_graph_multiple_callsigns_each_get_node():
    record = CorrelationRecord(
        event_id="evt-multi-cs",
        event_type="sec_flight",
        entity="DoD",
        score=7.0,
        signals=["flight", "sec"],
        evidence={
            "flight": [
                {"callsign": "PAT16", "facility": "DARPA HQ"},
                {"callsign": "MUSL1", "facility": "DARPA HQ"},
            ]
        },
    )
    graph = build_graph_from_correlation(record)
    node_ids = {n["node_id"] for n in graph["nodes"]}
    assert "callsign:PAT16" in node_ids
    assert "callsign:MUSL1" in node_ids


# --- temporal metadata ---


def test_build_graph_callsign_temporal_context_enriches_node():
    record = CorrelationRecord(
        event_id="evt-temporal",
        event_type="sec_flight",
        entity="Lockheed Martin",
        score=8.0,
        signals=["sec", "flight"],
        evidence={
            "flight": [{"callsign": "PAT16", "facility": "DARPA HQ"}],
            "temporal_context": {
                "PAT16": {
                    "prior_observations": 14,
                    "first_seen": "2026-01-01T00:00:00+00:00",
                    "last_seen": "2026-05-01T00:00:00+00:00",
                    "active_days": 10,
                    "most_common_facility": "DARPA HQ",
                }
            },
        },
    )
    graph = build_graph_from_correlation(record)
    cs_node = next(n for n in graph["nodes"] if n["node_id"] == "callsign:PAT16")
    props = cs_node["properties"]
    assert props["observation_count"] == 14
    assert props["first_seen"] == "2026-01-01T00:00:00+00:00"
    assert props["last_seen"] == "2026-05-01T00:00:00+00:00"


def test_build_graph_no_temporal_context_still_creates_callsign_node():
    graph = build_graph_from_correlation(_record_flight_callsign())
    cs_node = next(n for n in graph["nodes"] if n["node_id"] == "callsign:PAT16")
    # No temporal fields in properties
    assert "observation_count" not in cs_node["properties"]
    assert "first_seen" not in cs_node["properties"]


# --- total count verification ---


def test_build_graph_military_callsign_full_count():
    # entity, event, signal:sec, signal:flight, callsign:PAT16, facility:DARPA HQ, prefix:PAT
    # = 7 nodes
    # involved_in, contributed_to×2, participated_in, observed_via, observed_near, has_prefix
    # = 7 edges
    graph = build_graph_from_correlation(_record_flight_callsign())
    assert len(graph["nodes"]) == 7
    assert len(graph["edges"]) == 7


def test_build_graph_callsign_serialization_stable():
    record = _record_flight_callsign()
    graph_a = build_graph_from_correlation(record)
    graph_b = build_graph_from_correlation(record)
    assert json.dumps(graph_a, sort_keys=True) == json.dumps(graph_b, sort_keys=True)
