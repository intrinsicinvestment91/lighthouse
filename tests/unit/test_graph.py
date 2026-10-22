import pytest

from modules.graph import GRAPH_SCHEMA_VERSION, GraphEdge, GraphNode


def test_valid_node_construction():
    node = GraphNode(node_id="entity:tesla", node_type="entity", label="Tesla")
    node.validate()
    assert node.node_id == "entity:tesla"
    assert node.node_type == "entity"
    assert node.label == "Tesla"


def test_valid_edge_construction():
    edge = GraphEdge(
        edge_id="edge:1",
        source_id="entity:tesla",
        target_id="signal:sec",
        relation="linked_to",
        weight=0.8,
    )
    edge.validate()
    assert edge.edge_id == "edge:1"
    assert edge.source_id == "entity:tesla"
    assert edge.target_id == "signal:sec"
    assert edge.relation == "linked_to"
    assert edge.weight == 0.8


def test_node_serialization_round_trip():
    original = GraphNode(
        node_id="facility:nellis",
        node_type="facility",
        label="Nellis",
        properties={"region": "us-west"},
    )
    encoded = original.to_dict()
    decoded = GraphNode.from_dict(encoded)

    assert decoded.node_id == original.node_id
    assert decoded.node_type == original.node_type
    assert decoded.label == original.label
    assert decoded.properties == original.properties
    assert decoded.schema_version == GRAPH_SCHEMA_VERSION


def test_edge_serialization_round_trip():
    original = GraphEdge(
        edge_id="edge:2",
        source_id="entity:lockheed",
        target_id="filing:8k:123",
        relation="filed",
        weight=0.95,
        properties={"source": "sec"},
    )
    encoded = original.to_dict()
    decoded = GraphEdge.from_dict(encoded)

    assert decoded.edge_id == original.edge_id
    assert decoded.source_id == original.source_id
    assert decoded.target_id == original.target_id
    assert decoded.relation == original.relation
    assert decoded.weight == original.weight
    assert decoded.properties == original.properties
    assert decoded.schema_version == GRAPH_SCHEMA_VERSION


def test_missing_node_fields_fail_validation():
    with pytest.raises(ValueError, match="node_id required"):
        GraphNode(node_id="", node_type="entity", label="Tesla").validate()

    with pytest.raises(ValueError, match="node_type required"):
        GraphNode(node_id="entity:tesla", node_type="", label="Tesla").validate()

    with pytest.raises(ValueError, match="label required"):
        GraphNode(node_id="entity:tesla", node_type="entity", label="").validate()


def test_missing_edge_fields_fail_validation():
    with pytest.raises(ValueError, match="edge_id required"):
        GraphEdge(
            edge_id="",
            source_id="entity:tesla",
            target_id="signal:sec",
            relation="linked_to",
        ).validate()

    with pytest.raises(ValueError, match="source_id required"):
        GraphEdge(
            edge_id="edge:3",
            source_id="",
            target_id="signal:sec",
            relation="linked_to",
        ).validate()

    with pytest.raises(ValueError, match="target_id required"):
        GraphEdge(
            edge_id="edge:3",
            source_id="entity:tesla",
            target_id="",
            relation="linked_to",
        ).validate()

    with pytest.raises(ValueError, match="relation required"):
        GraphEdge(
            edge_id="edge:3",
            source_id="entity:tesla",
            target_id="signal:sec",
            relation="",
        ).validate()


def test_invalid_edge_weight_fails_validation():
    with pytest.raises(ValueError, match="weight must be between 0.0 and 1.0"):
        GraphEdge(
            edge_id="edge:4",
            source_id="entity:tesla",
            target_id="signal:sec",
            relation="linked_to",
            weight=1.1,
        ).validate()


def test_properties_default_to_empty_dict():
    node = GraphNode(node_id="entity:spacex", node_type="entity", label="SpaceX")
    edge = GraphEdge(
        edge_id="edge:5",
        source_id="entity:spacex",
        target_id="signal:flight",
        relation="linked_to",
    )

    assert node.properties == {}
    assert edge.properties == {}

    node_from_data = GraphNode.from_dict(
        {"node_id": "facility:edwards", "node_type": "facility", "label": "Edwards"}
    )
    edge_from_data = GraphEdge.from_dict(
        {
            "edge_id": "edge:6",
            "source_id": "facility:edwards",
            "target_id": "signal:adsb",
            "relation": "linked_to",
        }
    )
    assert node_from_data.properties == {}
    assert edge_from_data.properties == {}
