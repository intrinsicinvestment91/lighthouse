from modules.graph_explainer import explain_graph_result


def test_explain_graph_includes_entity_event_and_signals():
    graph = {
        "nodes": [
            {"node_id": "entity:Tesla", "node_type": "entity", "label": "Tesla", "properties": {}},
            {
                "node_id": "event:abc123",
                "node_type": "event",
                "label": "sec_adsb",
                "properties": {"score": 8.5},
            },
            {"node_id": "signal:sec", "node_type": "signal", "label": "sec", "properties": {}},
            {"node_id": "signal:adsb", "node_type": "signal", "label": "adsb", "properties": {}},
        ],
        "edges": [
            {
                "edge_id": "entity:Tesla->event:abc123",
                "source_id": "entity:Tesla",
                "target_id": "event:abc123",
                "relation": "involved_in",
                "weight": 0.9,
                "properties": {},
            },
            {
                "edge_id": "signal:sec->event:abc123",
                "source_id": "signal:sec",
                "target_id": "event:abc123",
                "relation": "contributed_to",
                "weight": 1.0,
                "properties": {},
            },
            {
                "edge_id": "signal:adsb->event:abc123",
                "source_id": "signal:adsb",
                "target_id": "event:abc123",
                "relation": "contributed_to",
                "weight": 1.0,
                "properties": {},
            },
        ],
    }

    explanation = explain_graph_result(graph)

    assert "Entity Tesla is involved in event event:abc123 with score 8.5." in explanation
    assert "Signals contributing to this event include ADSB activity and SEC filing." in explanation


def test_explain_graph_handles_empty_graph():
    explanation = explain_graph_result({"nodes": [], "edges": []})
    assert explanation == "Graph has no nodes or edges to explain."


def test_explain_graph_handles_single_node():
    graph = {
        "nodes": [
            {"node_id": "entity:Tesla", "node_type": "entity", "label": "Tesla", "properties": {}}
        ],
        "edges": [],
    }

    explanation = explain_graph_result(graph)
    assert (
        explanation == "Graph contains 1 node(s) and 0 edge(s). "
        "No explainable entity-event relationships were found."
    )


def test_explain_graph_handles_event_without_signals():
    graph = {
        "nodes": [
            {"node_id": "entity:Tesla", "node_type": "entity", "label": "Tesla", "properties": {}},
            {"node_id": "event:e1", "node_type": "event", "label": "sec_only", "properties": {}},
        ],
        "edges": [
            {
                "edge_id": "entity:Tesla->event:e1",
                "source_id": "entity:Tesla",
                "target_id": "event:e1",
                "relation": "involved_in",
                "weight": 0.7,
                "properties": {},
            }
        ],
    }

    explanation = explain_graph_result(graph)
    assert explanation == "Entity Tesla is involved in event event:e1."


def test_explain_graph_handles_signal_fallback_label():
    graph = {
        "nodes": [
            {
                "node_id": "event:e1",
                "node_type": "event",
                "label": "custom",
                "properties": {"score": 7},
            },
            {
                "node_id": "signal:customfeed",
                "node_type": "signal",
                "label": "CustomFeed",
                "properties": {},
            },
        ],
        "edges": [
            {
                "edge_id": "signal:customfeed->event:e1",
                "source_id": "signal:customfeed",
                "target_id": "event:e1",
                "relation": "contributed_to",
                "weight": 1.0,
                "properties": {},
            }
        ],
    }

    explanation = explain_graph_result(graph)
    assert explanation == (
        "Event event:e1 is present in the graph with score 7. "
        "Signals contributing to this event include CustomFeed."
    )
