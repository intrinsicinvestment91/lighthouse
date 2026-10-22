"""Unit tests for the `targets` CLI command in main.py."""

import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import main
from modules.signal_router import SignalRoute
from modules.targets import TARGET_SCHEMA_VERSION, TargetConfig, TargetProfile


def _run_cli_with_args(monkeypatch, args):
    monkeypatch.setattr("sys.argv", ["main.py", *args])
    main.cli_main()


def _mock_validation(*, valid=True):
    return {
        "valid": valid,
        "errors": [] if valid else ["invalid target config"],
        "warnings": [],
        "target_count": 2,
        "enabled_count": 1,
    }


def _mock_config():
    return TargetConfig(
        targets=[
            TargetProfile.from_dict(
                {
                    "name": "fusion_research",
                    "entities": ["Helion Energy"],
                    "keywords": ["fusion"],
                    "signals": ["sec"],
                    "regions": ["us"],
                    "priority": "high",
                    "schema_version": TARGET_SCHEMA_VERSION,
                }
            )
        ]
    )


def _mock_routes():
    return {
        "sec": SignalRoute.from_dict(
            {
                "signal": "sec",
                "targets": ["fusion_research"],
                "entities": ["Helion Energy"],
                "keywords": ["fusion"],
                "regions": ["us"],
                "priority": "high",
            }
        )
    }


def test_targets_default_prints_summary(monkeypatch, capsys):
    validate_targets = Mock(return_value=_mock_validation())
    load_targets = Mock(return_value=_mock_config())
    build_signal_routes = Mock(return_value=_mock_routes())
    route_summary = Mock(
        return_value={
            "route_count": 1,
            "signals": ["sec"],
            "targets": ["fusion_research"],
            "entities": ["Helion Energy"],
        }
    )

    monkeypatch.setattr("modules.targets.validate_targets", validate_targets)
    monkeypatch.setattr("modules.targets.load_targets", load_targets)
    monkeypatch.setattr("modules.signal_router.build_signal_routes", build_signal_routes)
    monkeypatch.setattr("modules.signal_router.route_summary", route_summary)

    _run_cli_with_args(monkeypatch, ["targets"])

    out = capsys.readouterr().out
    assert "Target validation: PASS" in out
    assert "Routes:" in out


def test_targets_json_output(monkeypatch, capsys):
    validate_targets = Mock(return_value=_mock_validation())
    load_targets = Mock(return_value=_mock_config())
    build_signal_routes = Mock(return_value=_mock_routes())
    route_summary = Mock(
        return_value={
            "route_count": 1,
            "signals": ["sec"],
            "targets": ["fusion_research"],
            "entities": ["Helion Energy"],
        }
    )

    monkeypatch.setattr("modules.targets.validate_targets", validate_targets)
    monkeypatch.setattr("modules.targets.load_targets", load_targets)
    monkeypatch.setattr("modules.signal_router.build_signal_routes", build_signal_routes)
    monkeypatch.setattr("modules.signal_router.route_summary", route_summary)

    _run_cli_with_args(monkeypatch, ["targets", "--json"])

    parsed = json.loads(capsys.readouterr().out)
    assert "validation" in parsed
    assert "summary" in parsed
    assert "targets" in parsed
    assert "routes" not in parsed


def test_targets_show_routes_json_includes_routes(monkeypatch, capsys):
    validate_targets = Mock(return_value=_mock_validation())
    load_targets = Mock(return_value=_mock_config())
    build_signal_routes = Mock(return_value=_mock_routes())
    route_summary = Mock(
        return_value={
            "route_count": 1,
            "signals": ["sec"],
            "targets": ["fusion_research"],
            "entities": ["Helion Energy"],
        }
    )

    monkeypatch.setattr("modules.targets.validate_targets", validate_targets)
    monkeypatch.setattr("modules.targets.load_targets", load_targets)
    monkeypatch.setattr("modules.signal_router.build_signal_routes", build_signal_routes)
    monkeypatch.setattr("modules.signal_router.route_summary", route_summary)

    _run_cli_with_args(monkeypatch, ["targets", "--json", "--show-routes"])

    parsed = json.loads(capsys.readouterr().out)
    assert "routes" in parsed
    assert "sec" in parsed["routes"]


def test_targets_path_is_forwarded(monkeypatch, capsys):
    validate_targets = Mock(return_value=_mock_validation())
    load_targets = Mock(return_value=_mock_config())
    build_signal_routes = Mock(return_value=_mock_routes())
    route_summary = Mock(
        return_value={
            "route_count": 1,
            "signals": ["sec"],
            "targets": ["fusion_research"],
            "entities": ["Helion Energy"],
        }
    )

    monkeypatch.setattr("modules.targets.validate_targets", validate_targets)
    monkeypatch.setattr("modules.targets.load_targets", load_targets)
    monkeypatch.setattr("modules.signal_router.build_signal_routes", build_signal_routes)
    monkeypatch.setattr("modules.signal_router.route_summary", route_summary)

    custom_path = "/tmp/custom.json"
    _run_cli_with_args(monkeypatch, ["targets", "--path", custom_path])

    validate_targets.assert_called_once_with(custom_path)
    load_targets.assert_called_once_with(custom_path)
    capsys.readouterr()


def test_targets_invalid_exits_nonzero(monkeypatch, capsys):
    validate_targets = Mock(return_value=_mock_validation(valid=False))

    monkeypatch.setattr("modules.targets.validate_targets", validate_targets)

    with pytest.raises(SystemExit) as exc_info:
        _run_cli_with_args(monkeypatch, ["targets"])

    assert exc_info.value.code == 1
    out = capsys.readouterr().out
    assert "FAIL" in out or "invalid" in out.lower()


def test_targets_command_uses_seed_file_smoke(capsys):
    args = SimpleNamespace(json=False, path="data/targets.json", show_routes=False)
    main.handle_targets_command(args)

    out = capsys.readouterr().out
    assert "Target validation: PASS" in out
    assert "sec" in out
