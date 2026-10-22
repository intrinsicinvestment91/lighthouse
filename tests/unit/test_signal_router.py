import json

import pytest

from modules.signal_router import (
    SignalRoute,
    _merge_priority,
    build_signal_routes,
    build_signal_routes_from_file,
    route_summary,
)
from modules.targets import TARGET_SCHEMA_VERSION, TargetConfig, TargetProfile


def _write_targets(tmp_path, payload):
    path = tmp_path / "targets.json"
    path.write_text(json.dumps(payload))
    return path


def _target_config(*targets: TargetProfile) -> TargetConfig:
    return TargetConfig(targets=list(targets))


def _valid_target(**overrides) -> TargetProfile:
    base = {
        "name": "fusion_research",
        "entities": ["Helion Energy"],
        "keywords": ["fusion"],
        "signals": ["sec", "patent"],
        "regions": ["us"],
        "priority": "high",
        "enabled": True,
    }
    base.update(overrides)
    return TargetProfile.from_dict(base)


def _valid_route(**overrides) -> SignalRoute:
    base = {
        "signal": "sec",
        "targets": ["fusion_research"],
        "entities": ["Helion Energy"],
        "keywords": ["fusion"],
        "regions": ["us"],
        "priority": "high",
    }
    base.update(overrides)
    return SignalRoute.from_dict(base)


# SignalRoute tests


def test_valid_signal_route_validates():
    route = _valid_route()
    route.validate()
    assert route.signal == "sec"


def test_invalid_signal_fails():
    route = SignalRoute.from_dict({"signal": "velocity", "targets": ["t1"]})
    with pytest.raises(ValueError, match="invalid signal: velocity"):
        route.validate()


def test_route_serialization_round_trip():
    original = _valid_route()
    encoded = original.to_dict()
    decoded = SignalRoute.from_dict(encoded)
    assert decoded.to_dict() == encoded


def test_route_deduplicates_entities_keywords_regions():
    route = SignalRoute.from_dict(
        {
            "signal": "sec",
            "targets": ["a", "a"],
            "entities": ["Tesla", "Tesla"],
            "keywords": ["Fusion", "fusion"],
            "regions": ["US", "us"],
        }
    )
    assert route.targets == ["a"]
    assert route.entities == ["Tesla"]
    assert route.keywords == ["Fusion"]
    assert route.regions == ["us"]


def test_merge_priority_keeps_highest():
    assert _merge_priority("low", "medium") == "medium"
    assert _merge_priority("medium", "high") == "high"
    assert _merge_priority("high", "low") == "high"


# build_signal_routes tests


def test_build_signal_routes_from_enabled_targets():
    config = _target_config(
        _valid_target(name="fusion_research", signals=["sec", "arxiv"]),
        _valid_target(
            name="aerospace_defense",
            entities=["Lockheed Martin"],
            signals=["sec"],
            priority="high",
        ),
    )
    routes = build_signal_routes(config)

    assert set(routes.keys()) == {"sec", "arxiv"}
    assert routes["sec"].targets == ["fusion_research", "aerospace_defense"]
    assert "Helion Energy" in routes["sec"].entities
    assert "Lockheed Martin" in routes["sec"].entities


def test_disabled_targets_are_ignored():
    config = _target_config(
        _valid_target(name="active", enabled=True),
        _valid_target(name="inactive", enabled=False, signals=["arxiv"]),
    )
    routes = build_signal_routes(config)

    assert "inactive" not in {t for r in routes.values() for t in r.targets}
    assert "arxiv" not in routes


def test_target_with_no_signals_contributes_no_route():
    config = _target_config(_valid_target(name="no_signals", signals=[]))
    routes = build_signal_routes(config)
    assert routes == {}


def test_route_merges_target_entities_keywords_regions():
    config = _target_config(
        _valid_target(
            name="t1",
            entities=["Entity A"],
            keywords=["keyword-a"],
            regions=["us"],
            signals=["sec"],
        ),
        _valid_target(
            name="t2",
            entities=["Entity B"],
            keywords=["keyword-b"],
            regions=["eu"],
            signals=["sec"],
        ),
    )
    route = build_signal_routes(config)["sec"]

    assert route.targets == ["t1", "t2"]
    assert route.entities == ["Entity A", "Entity B"]
    assert route.keywords == ["keyword-a", "keyword-b"]
    assert route.regions == ["us", "eu"]


def test_route_priority_becomes_highest_target_priority():
    config = _target_config(
        _valid_target(name="low_target", signals=["sec"], priority="low"),
        _valid_target(name="high_target", signals=["sec"], priority="high"),
    )
    route = build_signal_routes(config)["sec"]
    assert route.priority == "high"


def test_invalid_target_config_raises():
    config = TargetConfig(
        targets=[
            TargetProfile.from_dict(
                {
                    "name": "bad",
                    "entities": [],
                    "keywords": [],
                    "regions": [],
                    "signals": ["sec"],
                }
            )
        ]
    )
    with pytest.raises(ValueError, match="target must include at least one entity"):
        build_signal_routes(config)


def test_build_signal_routes_from_file(tmp_path):
    path = _write_targets(
        tmp_path,
        {
            "schema_version": TARGET_SCHEMA_VERSION,
            "targets": [
                {
                    "name": "fusion_research",
                    "entities": ["Helion Energy"],
                    "keywords": ["fusion"],
                    "signals": ["sec"],
                    "regions": ["us"],
                    "priority": "high",
                    "enabled": True,
                    "schema_version": TARGET_SCHEMA_VERSION,
                }
            ],
        },
    )
    routes = build_signal_routes_from_file(path)
    assert "sec" in routes
    assert routes["sec"].targets == ["fusion_research"]


def test_route_summary_returns_deterministic_counts():
    config = _target_config(
        _valid_target(name="t1", signals=["sec", "arxiv"]),
        _valid_target(name="t2", entities=["Lockheed Martin"], signals=["sec"]),
    )
    routes = build_signal_routes(config)
    summary = route_summary(routes)

    assert summary["route_count"] == 2
    assert summary["signals"] == ["arxiv", "sec"]
    assert summary["targets"] == ["t1", "t2"]
    assert "Helion Energy" in summary["entities"]
    assert "Lockheed Martin" in summary["entities"]


# Seed file tests


def test_seed_targets_json_builds_at_least_one_route():
    routes = build_signal_routes_from_file("data/targets.json")
    assert len(routes) >= 1


def test_seed_route_summary_includes_expected_signals():
    routes = build_signal_routes_from_file("data/targets.json")
    summary = route_summary(routes)

    assert summary["route_count"] >= 1
    assert "sec" in summary["signals"]
    assert "arxiv" in summary["signals"]


def test_seed_government_defense_labs_routes_darpa():
    routes = build_signal_routes_from_file("data/targets.json")

    for signal in ("flight", "adsb", "archive", "narrative"):
        route = routes[signal]
        assert "government_defense_labs" in route.targets
        assert "DARPA" in route.entities

    assert "DARPA" not in routes["sec"].entities
