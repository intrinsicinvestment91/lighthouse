import json
from unittest.mock import Mock

import main


def _run_cli_with_args(monkeypatch, args):
    monkeypatch.setattr("sys.argv", ["main.py", *args])
    main.cli_main()


def _graph_result():
    return {
        "nodes": [
            {"node_id": "entity:Tesla", "node_type": "entity", "label": "Tesla", "properties": {}},
            {
                "node_id": "event:evt1",
                "node_type": "event",
                "label": "sec_adsb",
                "properties": {"score": 8.0},
            },
        ],
        "edges": [
            {
                "edge_id": "entity:Tesla->event:evt1",
                "source_id": "entity:Tesla",
                "target_id": "event:evt1",
                "relation": "involved_in",
                "weight": 0.8,
                "properties": {},
            }
        ],
    }


def test_graph_basic_execution_calls_traverse(monkeypatch, capsys):
    init_graph_tables = Mock()
    traverse = Mock(return_value=_graph_result())
    monkeypatch.setattr(main.db, "init_graph_tables", init_graph_tables)
    monkeypatch.setattr(main.db, "traverse_graph", traverse)
    # Entity resolution probes the DB via fetch_graph_node; mock it so the test
    # is deterministic and does not depend on a pre-existing data/state.db
    # (absent on a clean CI checkout — otherwise: no such table: graph_nodes).
    monkeypatch.setattr(main.db, "fetch_graph_node", Mock(return_value=None))

    _run_cli_with_args(monkeypatch, ["graph", "--entity", "Tesla"])

    init_graph_tables.assert_called_once_with()
    traverse.assert_called_once_with(node_id="entity:Tesla", depth=1, limit=100)
    out = capsys.readouterr().out
    assert "Nodes:" in out
    assert "Edges:" in out


def test_graph_json_output_is_valid_json(monkeypatch, capsys):
    init_graph_tables = Mock()
    traverse = Mock(return_value=_graph_result())
    monkeypatch.setattr(main.db, "init_graph_tables", init_graph_tables)
    monkeypatch.setattr(main.db, "traverse_graph", traverse)
    # Entity resolution probes the DB via fetch_graph_node; mock it so the test
    # is deterministic and does not depend on a pre-existing data/state.db
    # (absent on a clean CI checkout — otherwise: no such table: graph_nodes).
    monkeypatch.setattr(main.db, "fetch_graph_node", Mock(return_value=None))

    _run_cli_with_args(monkeypatch, ["graph", "--entity", "Tesla", "--json"])

    init_graph_tables.assert_called_once_with()
    out = capsys.readouterr().out
    parsed = json.loads(out)
    assert "nodes" in parsed
    assert "edges" in parsed
    assert parsed["nodes"][0]["node_id"] == "entity:Tesla"


def test_graph_entity_mapping_uses_entity_prefix(monkeypatch):
    init_graph_tables = Mock()
    traverse = Mock(return_value=_graph_result())
    monkeypatch.setattr(main.db, "init_graph_tables", init_graph_tables)
    monkeypatch.setattr(main.db, "traverse_graph", traverse)
    # Entity resolution probes the DB via fetch_graph_node; mock it so the test
    # is deterministic and does not depend on a pre-existing data/state.db
    # (absent on a clean CI checkout — otherwise: no such table: graph_nodes).
    monkeypatch.setattr(main.db, "fetch_graph_node", Mock(return_value=None))

    _run_cli_with_args(monkeypatch, ["graph", "--entity", "Tesla"])

    init_graph_tables.assert_called_once_with()
    args, kwargs = traverse.call_args
    assert args == ()
    assert kwargs["node_id"] == "entity:Tesla"


def test_graph_depth_propagation(monkeypatch):
    init_graph_tables = Mock()
    traverse = Mock(return_value=_graph_result())
    monkeypatch.setattr(main.db, "init_graph_tables", init_graph_tables)
    monkeypatch.setattr(main.db, "traverse_graph", traverse)
    # Entity resolution probes the DB via fetch_graph_node; mock it so the test
    # is deterministic and does not depend on a pre-existing data/state.db
    # (absent on a clean CI checkout — otherwise: no such table: graph_nodes).
    monkeypatch.setattr(main.db, "fetch_graph_node", Mock(return_value=None))

    _run_cli_with_args(monkeypatch, ["graph", "--entity", "Tesla", "--depth", "2", "--limit", "25"])

    init_graph_tables.assert_called_once_with()
    traverse.assert_called_once_with(node_id="entity:Tesla", depth=2, limit=25)
