import json
from unittest.mock import Mock

import main


def _run_cli_with_args(monkeypatch, args):
    monkeypatch.setattr("sys.argv", ["main.py", *args])
    main.cli_main()


def _backfill_result(**overrides):
    result = {
        "dry_run": False,
        "requested_limit": 100,
        "correlations_seen": 5,
        "correlations_backfilled": 4,
        "correlations_failed": 1,
        "nodes": 16,
        "edges": 12,
        "errors": [{"correlation": "evt-bad", "error": "invalid row"}],
    }
    result.update(overrides)
    return result


def test_graph_backfill_cli_human_output(monkeypatch, capsys):
    backfill = Mock(return_value=_backfill_result())
    monkeypatch.setattr(
        "modules.graph_backfill.backfill_correlation_graphs",
        backfill,
    )

    _run_cli_with_args(monkeypatch, ["graph-backfill", "--limit", "50"])

    out = capsys.readouterr().out
    assert "Graph backfill complete" in out
    assert "Correlations seen: 5" in out
    assert "Backfilled: 4" in out
    assert "Failed: 1" in out
    assert "Nodes: 16" in out
    assert "Edges: 12" in out
    assert "Dry run: false" in out


def test_graph_backfill_cli_json_output(monkeypatch, capsys):
    backfill = Mock(return_value=_backfill_result(dry_run=True))
    monkeypatch.setattr(
        "modules.graph_backfill.backfill_correlation_graphs",
        backfill,
    )

    _run_cli_with_args(monkeypatch, ["graph-backfill", "--json", "--dry-run"])

    out = capsys.readouterr().out
    parsed = json.loads(out)
    assert parsed["correlations_seen"] == 5
    assert parsed["correlations_backfilled"] == 4
    assert parsed["dry_run"] is True


def test_graph_backfill_cli_passes_limit_and_dry_run(monkeypatch):
    backfill = Mock(return_value=_backfill_result())
    monkeypatch.setattr(
        "modules.graph_backfill.backfill_correlation_graphs",
        backfill,
    )

    _run_cli_with_args(
        monkeypatch,
        ["graph-backfill", "--limit", "25", "--dry-run"],
    )

    backfill.assert_called_once_with(limit=25, dry_run=True)
