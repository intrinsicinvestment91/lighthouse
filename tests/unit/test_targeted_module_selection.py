"""Unit tests for target-aware module selection in main.py."""

import argparse
from unittest.mock import Mock

import pytest

import main
from modules.signal_router import SignalRoute


def _mock_routes(*signals: str) -> dict[str, SignalRoute]:
    return {
        signal: SignalRoute.from_dict(
            {
                "signal": signal,
                "targets": ["fusion_research"],
                "entities": ["Helion Energy"],
                "keywords": ["fusion"],
                "regions": ["us"],
                "priority": "high",
                "schema_version": 1,
            }
        )
        for signal in signals
    }


def test_targeted_modules_from_routes_maps_signals():
    routes = _mock_routes("sec", "patent", "adsb")
    modules = main.targeted_modules_from_routes(routes)
    assert modules == {"sec", "patents", "velocity", "adsb"}


def test_targeted_modules_from_routes_ignores_unknown_signal():
    routes = _mock_routes("sec")
    routes["unknown"] = routes["sec"]
    modules = main.targeted_modules_from_routes(routes)
    assert modules == {"sec"}


def test_resolve_requested_modules_all_includes_expected_modules():
    modules = main.resolve_requested_modules("all")
    assert modules == main.ALL_PIPELINE_MODULES


def test_resolve_requested_modules_single_module():
    assert main.resolve_requested_modules("sec") == {"sec"}


def test_resolve_requested_modules_targeted_loads_routes(monkeypatch):
    routes = _mock_routes("sec", "patent")
    summary = {
        "route_count": 2,
        "signals": ["patent", "sec"],
        "targets": ["fusion_research"],
        "entities": ["Helion Energy"],
    }
    monkeypatch.setattr(
        "modules.signal_router.build_signal_routes_from_file",
        Mock(return_value=routes),
    )
    monkeypatch.setattr(
        "modules.signal_router.route_summary",
        Mock(return_value=summary),
    )

    modules = main.resolve_requested_modules("targeted")
    assert modules == {"sec", "patents", "velocity", "correlator"}


def test_resolve_requested_modules_targeted_empty_routes_no_correlator(monkeypatch, caplog):
    monkeypatch.setattr(
        "modules.signal_router.build_signal_routes_from_file",
        Mock(return_value={}),
    )

    with caplog.at_level("WARNING"):
        modules = main.resolve_requested_modules("targeted")

    assert modules == set()
    assert "no signal modules selected" in caplog.text.lower()


def test_target_entities_for_signal_returns_route_entities():
    routes = _mock_routes("sec")
    entities = main.target_entities_for_signal(routes, "sec")
    assert entities == ["Helion Energy"]


def test_target_entities_for_signal_none_when_no_route():
    assert main.target_entities_for_signal(_mock_routes("patent"), "sec") is None
    assert main.target_entities_for_signal(None, "sec") is None


def test_target_keywords_for_signal_returns_route_keywords():
    routes = _mock_routes("arxiv")
    keywords = main.target_keywords_for_signal(routes, "arxiv")
    assert keywords == ["fusion"]


def test_target_keywords_for_signal_none_when_no_route():
    assert main.target_keywords_for_signal(_mock_routes("sec"), "arxiv") is None
    assert main.target_keywords_for_signal(None, "arxiv") is None


def test_target_regions_for_signal_returns_route_regions():
    routes = _mock_routes("flight")
    regions = main.target_regions_for_signal(routes, "flight")
    assert regions == ["us"]


def test_target_regions_for_signal_none_when_no_route():
    assert main.target_regions_for_signal(_mock_routes("sec"), "flight") is None
    assert main.target_regions_for_signal(None, "flight") is None


def test_resolve_requested_modules_targeted_invalid_targets_exits(monkeypatch):
    monkeypatch.setattr(
        "modules.signal_router.build_signal_routes_from_file",
        Mock(side_effect=ValueError("invalid target config")),
    )

    with pytest.raises(SystemExit) as exc_info:
        main.resolve_requested_modules("targeted")

    assert exc_info.value.code == 1


@pytest.mark.asyncio
async def test_targeted_mode_runs_only_selected_modules(monkeypatch):
    from unittest.mock import AsyncMock, MagicMock

    called: list[str] = []

    async def track_patents(**_kwargs):
        called.append("patents")
        return []

    async def track_sec(*_args, **_kwargs):
        called.append("sec")
        return []

    async def track_correlator(*_args, **_kwargs):
        called.append("correlator")
        return []

    monkeypatch.setattr(main, "load_target_routes", lambda: _mock_routes("sec"))
    monkeypatch.setattr(main, "validate_config", AsyncMock())
    monkeypatch.setattr(main, "init_db", lambda _: MagicMock())
    monkeypatch.setattr(main, "load_proxy_rotator", lambda: None)
    monkeypatch.setattr(main.nostr_cfg, "private_key_hex", None)
    monkeypatch.setattr(main, "run_patent_sentinel", track_patents)
    monkeypatch.setattr("modules.sec_sentinel.run_sec_sentinel", track_sec)
    monkeypatch.setattr("modules.correlator.run_correlator", track_correlator)

    args = argparse.Namespace(module="targeted", dry_run=True, since_days=1)
    await main.main(args)

    assert called == ["sec", "correlator"]


@pytest.mark.asyncio
async def test_targeted_mode_passes_sec_target_entities(monkeypatch):
    from unittest.mock import AsyncMock, MagicMock

    captured: dict = {}

    async def track_sec(*_args, **kwargs):
        captured.update(kwargs)
        return []

    routes = _mock_routes("sec")
    routes["sec"] = SignalRoute.from_dict(
        {
            "signal": "sec",
            "targets": ["fusion_research"],
            "entities": ["Helion Energy", "Lockheed Martin"],
            "keywords": ["fusion"],
            "regions": ["us"],
            "priority": "high",
            "schema_version": 1,
        }
    )

    monkeypatch.setattr(main, "load_target_routes", lambda: routes)
    monkeypatch.setattr(main, "validate_config", AsyncMock())
    monkeypatch.setattr(main, "init_db", lambda _: MagicMock())
    monkeypatch.setattr(main, "load_proxy_rotator", lambda: None)
    monkeypatch.setattr(main.nostr_cfg, "private_key_hex", None)
    monkeypatch.setattr(main, "run_patent_sentinel", AsyncMock(return_value=[]))
    monkeypatch.setattr("modules.sec_sentinel.run_sec_sentinel", track_sec)
    monkeypatch.setattr("modules.correlator.run_correlator", AsyncMock(return_value=[]))

    args = argparse.Namespace(module="targeted", dry_run=True, since_days=1)
    await main.main(args)

    assert captured["target_entities"] == ["Helion Energy", "Lockheed Martin"]


@pytest.mark.asyncio
async def test_targeted_mode_passes_arxiv_target_entities_and_keywords(monkeypatch):
    from unittest.mock import AsyncMock, MagicMock

    captured: dict = {}

    async def track_arxiv(*_args, **kwargs):
        captured.update(kwargs)
        return []

    routes = _mock_routes("arxiv")
    routes["arxiv"] = SignalRoute.from_dict(
        {
            "signal": "arxiv",
            "targets": ["fusion_research"],
            "entities": ["Synthetic Researcher A", "Synthetic Researcher E"],
            "keywords": ["plasma", "fusion"],
            "regions": ["us"],
            "priority": "high",
            "schema_version": 1,
        }
    )

    monkeypatch.setattr(main, "load_target_routes", lambda: routes)
    monkeypatch.setattr(main, "validate_config", AsyncMock())
    monkeypatch.setattr(main, "init_db", lambda _: MagicMock())
    monkeypatch.setattr(main, "load_proxy_rotator", lambda: None)
    monkeypatch.setattr(main.nostr_cfg, "private_key_hex", None)
    monkeypatch.setattr(main, "run_patent_sentinel", AsyncMock(return_value=[]))
    monkeypatch.setattr("modules.arxiv_sentinel.run_arxiv_sentinel", track_arxiv)
    monkeypatch.setattr("modules.correlator.run_correlator", AsyncMock(return_value=[]))

    args = argparse.Namespace(module="targeted", dry_run=True, since_days=1)
    await main.main(args)

    assert captured["target_entities"] == ["Synthetic Researcher A", "Synthetic Researcher E"]
    assert captured["target_keywords"] == ["plasma", "fusion"]


@pytest.mark.asyncio
async def test_targeted_mode_passes_patent_target_entities_and_keywords(monkeypatch):
    from unittest.mock import AsyncMock, MagicMock

    captured: dict = {}

    async def track_patents(**kwargs):
        captured.update(kwargs)
        return []

    routes = _mock_routes("patent")
    routes["patent"] = SignalRoute.from_dict(
        {
            "signal": "patent",
            "targets": ["fusion_research"],
            "entities": ["Helion Energy", "Commonwealth Fusion"],
            "keywords": ["plasma", "fusion"],
            "regions": ["us"],
            "priority": "high",
            "schema_version": 1,
        }
    )

    monkeypatch.setattr(main, "load_target_routes", lambda: routes)
    monkeypatch.setattr(main, "validate_config", AsyncMock())
    monkeypatch.setattr(main, "init_db", lambda _: MagicMock())
    monkeypatch.setattr(main, "load_proxy_rotator", lambda: None)
    monkeypatch.setattr(main.nostr_cfg, "private_key_hex", None)
    monkeypatch.setattr(main, "run_patent_sentinel", track_patents)
    monkeypatch.setattr("modules.velocity_tracker.run_velocity_tracker", AsyncMock(return_value=[]))
    monkeypatch.setattr("modules.correlator.run_correlator", AsyncMock(return_value=[]))

    args = argparse.Namespace(module="targeted", dry_run=True, since_days=1)
    await main.main(args)

    assert captured["target_entities"] == ["Helion Energy", "Commonwealth Fusion"]
    assert captured["target_keywords"] == ["plasma", "fusion"]


@pytest.mark.asyncio
async def test_patents_mode_passes_none_target_params(monkeypatch):
    from unittest.mock import AsyncMock, MagicMock

    captured: dict = {}

    async def track_patents(**kwargs):
        captured.update(kwargs)
        return []

    monkeypatch.setattr(main, "validate_config", AsyncMock())
    monkeypatch.setattr(main, "init_db", lambda _: MagicMock())
    monkeypatch.setattr(main, "load_proxy_rotator", lambda: None)
    monkeypatch.setattr(main.nostr_cfg, "private_key_hex", None)
    monkeypatch.setattr(main, "run_patent_sentinel", track_patents)
    monkeypatch.setattr("modules.velocity_tracker.run_velocity_tracker", AsyncMock(return_value=[]))

    args = argparse.Namespace(module="patents", dry_run=True, since_days=1)
    await main.main(args)

    assert captured["target_entities"] is None
    assert captured["target_keywords"] is None


@pytest.mark.asyncio
async def test_all_mode_passes_none_target_params(monkeypatch):
    from unittest.mock import AsyncMock, MagicMock

    captured: dict = {}

    async def track_patents(**kwargs):
        captured.update(kwargs)
        return []

    monkeypatch.setattr(main, "validate_config", AsyncMock())
    monkeypatch.setattr(main, "init_db", lambda _: MagicMock())
    monkeypatch.setattr(main, "load_proxy_rotator", lambda: None)
    monkeypatch.setattr(main.nostr_cfg, "private_key_hex", None)
    monkeypatch.setattr(main, "run_patent_sentinel", track_patents)
    monkeypatch.setattr("modules.velocity_tracker.run_velocity_tracker", AsyncMock(return_value=[]))
    monkeypatch.setattr("modules.arxiv_sentinel.run_arxiv_sentinel", AsyncMock(return_value=[]))
    monkeypatch.setattr("modules.sec_sentinel.run_sec_sentinel", AsyncMock(return_value=[]))
    monkeypatch.setattr(main, "run_wayback_archiver", AsyncMock(return_value=[]))
    monkeypatch.setattr(
        "modules.narrative_analyzer.run_narrative_analyzer", AsyncMock(return_value=[])
    )
    monkeypatch.setattr("modules.flight_tracker.run_flight_tracker", AsyncMock(return_value=[]))
    monkeypatch.setattr("modules.adsb_sentinel.run_adsb_sentinel", AsyncMock(return_value=[]))
    monkeypatch.setattr("modules.correlator.run_correlator", AsyncMock(return_value=[]))
    monkeypatch.setattr("modules.nostr_relay.run_nostr_bot", AsyncMock())

    args = argparse.Namespace(module="all", dry_run=True, since_days=1)
    await main.main(args)

    assert captured["target_entities"] is None
    assert captured["target_keywords"] is None


@pytest.mark.asyncio
async def test_targeted_mode_passes_flight_target_entities_keywords_regions(monkeypatch):
    from unittest.mock import AsyncMock, MagicMock

    captured: dict = {}

    async def track_flight(*_args, **kwargs):
        captured.update(kwargs)
        return []

    routes = _mock_routes("flight")
    routes["flight"] = SignalRoute.from_dict(
        {
            "signal": "flight",
            "targets": ["fusion_research"],
            "entities": ["Helion Energy", "NIF"],
            "keywords": ["plasma", "fusion"],
            "regions": ["us", "west"],
            "priority": "high",
            "schema_version": 1,
        }
    )

    monkeypatch.setattr(main, "load_target_routes", lambda: routes)
    monkeypatch.setattr(main, "validate_config", AsyncMock())
    monkeypatch.setattr(main, "init_db", lambda _: MagicMock())
    monkeypatch.setattr(main, "load_proxy_rotator", lambda: None)
    monkeypatch.setattr(main.nostr_cfg, "private_key_hex", None)
    monkeypatch.setattr(main, "run_patent_sentinel", AsyncMock(return_value=[]))
    monkeypatch.setattr("modules.flight_tracker.run_flight_tracker", track_flight)
    monkeypatch.setattr("modules.correlator.run_correlator", AsyncMock(return_value=[]))

    args = argparse.Namespace(module="targeted", dry_run=True, since_days=1)
    await main.main(args)

    assert captured["target_entities"] == ["Helion Energy", "NIF"]
    assert captured["target_keywords"] == ["plasma", "fusion"]
    assert captured["target_regions"] == ["us", "west"]


@pytest.mark.asyncio
async def test_targeted_mode_passes_adsb_target_entities_keywords_regions(monkeypatch):
    from unittest.mock import AsyncMock, MagicMock

    captured: dict = {}

    async def track_adsb(*_args, **kwargs):
        captured.update(kwargs)
        return []

    routes = _mock_routes("adsb")
    routes["adsb"] = SignalRoute.from_dict(
        {
            "signal": "adsb",
            "targets": ["aerospace_defense"],
            "entities": ["Lockheed Martin", "General Atomics"],
            "keywords": ["hypersonic", "propulsion"],
            "regions": ["us"],
            "priority": "high",
            "schema_version": 1,
        }
    )

    monkeypatch.setattr(main, "load_target_routes", lambda: routes)
    monkeypatch.setattr(main, "validate_config", AsyncMock())
    monkeypatch.setattr(main, "init_db", lambda _: MagicMock())
    monkeypatch.setattr(main, "load_proxy_rotator", lambda: None)
    monkeypatch.setattr(main.nostr_cfg, "private_key_hex", None)
    monkeypatch.setattr(main, "run_patent_sentinel", AsyncMock(return_value=[]))
    monkeypatch.setattr("modules.adsb_sentinel.run_adsb_sentinel", track_adsb)
    monkeypatch.setattr("modules.correlator.run_correlator", AsyncMock(return_value=[]))

    args = argparse.Namespace(module="targeted", dry_run=True, since_days=1)
    await main.main(args)

    assert captured["target_entities"] == ["Lockheed Martin", "General Atomics"]
    assert captured["target_keywords"] == ["hypersonic", "propulsion"]
    assert captured["target_regions"] == ["us"]


@pytest.mark.asyncio
async def test_flight_mode_passes_none_target_params(monkeypatch):
    from unittest.mock import AsyncMock, MagicMock

    captured: dict = {}

    async def track_flight(*_args, **kwargs):
        captured.update(kwargs)
        return []

    monkeypatch.setattr(main, "validate_config", AsyncMock())
    monkeypatch.setattr(main, "init_db", lambda _: MagicMock())
    monkeypatch.setattr(main, "load_proxy_rotator", lambda: None)
    monkeypatch.setattr(main.nostr_cfg, "private_key_hex", None)
    monkeypatch.setattr("modules.flight_tracker.run_flight_tracker", track_flight)

    args = argparse.Namespace(module="flight", dry_run=True, since_days=1)
    await main.main(args)

    assert captured["target_entities"] is None
    assert captured["target_keywords"] is None
    assert captured["target_regions"] is None


@pytest.mark.asyncio
async def test_adsb_mode_passes_none_target_params(monkeypatch):
    from unittest.mock import AsyncMock, MagicMock

    captured: dict = {}

    async def track_adsb(*_args, **kwargs):
        captured.update(kwargs)
        return []

    monkeypatch.setattr(main, "validate_config", AsyncMock())
    monkeypatch.setattr(main, "init_db", lambda _: MagicMock())
    monkeypatch.setattr(main, "load_proxy_rotator", lambda: None)
    monkeypatch.setattr(main.nostr_cfg, "private_key_hex", None)
    monkeypatch.setattr("modules.adsb_sentinel.run_adsb_sentinel", track_adsb)

    args = argparse.Namespace(module="adsb", dry_run=True, since_days=1)
    await main.main(args)

    assert captured["target_entities"] is None
    assert captured["target_keywords"] is None
    assert captured["target_regions"] is None


@pytest.mark.asyncio
async def test_targeted_mode_passes_narrative_target_entities_and_keywords(monkeypatch):
    from unittest.mock import AsyncMock, MagicMock

    captured: dict = {}

    async def track_narrative(*_args, **kwargs):
        captured.update(kwargs)
        return []

    routes = _mock_routes("narrative")
    routes["narrative"] = SignalRoute.from_dict(
        {
            "signal": "narrative",
            "targets": ["advanced_research_institutions"],
            "entities": ["JPL", "Lawrence Livermore National Laboratory"],
            "keywords": ["prototype", "laboratory"],
            "regions": ["us"],
            "priority": "medium",
            "schema_version": 1,
        }
    )

    monkeypatch.setattr(main, "load_target_routes", lambda: routes)
    monkeypatch.setattr(main, "validate_config", AsyncMock())
    monkeypatch.setattr(main, "init_db", lambda _: MagicMock())
    monkeypatch.setattr(main, "load_proxy_rotator", lambda: None)
    monkeypatch.setattr(main.nostr_cfg, "private_key_hex", None)
    monkeypatch.setattr(main, "run_patent_sentinel", AsyncMock(return_value=[]))
    monkeypatch.setattr("modules.narrative_analyzer.run_narrative_analyzer", track_narrative)
    monkeypatch.setattr("modules.correlator.run_correlator", AsyncMock(return_value=[]))

    args = argparse.Namespace(module="targeted", dry_run=True, since_days=1)
    await main.main(args)

    assert captured["target_entities"] == ["JPL", "Lawrence Livermore National Laboratory"]
    assert captured["target_keywords"] == ["prototype", "laboratory"]


@pytest.mark.asyncio
async def test_targeted_mode_passes_archive_target_entities_and_keywords(monkeypatch):
    from unittest.mock import AsyncMock, MagicMock

    captured: dict = {}

    async def track_archive(**kwargs):
        captured.update(kwargs)
        return []

    routes = _mock_routes("archive")
    routes["archive"] = SignalRoute.from_dict(
        {
            "signal": "archive",
            "targets": ["aerospace_defense"],
            "entities": ["Lockheed Martin", "General Atomics"],
            "keywords": ["hypersonic", "propulsion"],
            "regions": ["us"],
            "priority": "high",
            "schema_version": 1,
        }
    )

    monkeypatch.setattr(main, "load_target_routes", lambda: routes)
    monkeypatch.setattr(main, "validate_config", AsyncMock())
    monkeypatch.setattr(main, "init_db", lambda _: MagicMock())
    monkeypatch.setattr(main, "load_proxy_rotator", lambda: None)
    monkeypatch.setattr(main.nostr_cfg, "private_key_hex", None)
    monkeypatch.setattr(main, "run_patent_sentinel", AsyncMock(return_value=[]))
    monkeypatch.setattr(main, "run_wayback_archiver", track_archive)
    monkeypatch.setattr("modules.correlator.run_correlator", AsyncMock(return_value=[]))

    args = argparse.Namespace(module="targeted", dry_run=True, since_days=1)
    await main.main(args)

    assert captured["target_entities"] == ["Lockheed Martin", "General Atomics"]
    assert captured["target_keywords"] == ["hypersonic", "propulsion"]


@pytest.mark.asyncio
async def test_narrative_mode_passes_none_target_params(monkeypatch):
    from unittest.mock import AsyncMock, MagicMock

    captured: dict = {}

    async def track_narrative(*_args, **kwargs):
        captured.update(kwargs)
        return []

    monkeypatch.setattr(main, "validate_config", AsyncMock())
    monkeypatch.setattr(main, "init_db", lambda _: MagicMock())
    monkeypatch.setattr(main, "load_proxy_rotator", lambda: None)
    monkeypatch.setattr(main.nostr_cfg, "private_key_hex", None)
    monkeypatch.setattr("modules.narrative_analyzer.run_narrative_analyzer", track_narrative)

    args = argparse.Namespace(module="narrative", dry_run=True, since_days=1)
    await main.main(args)

    assert captured["target_entities"] is None
    assert captured["target_keywords"] is None


@pytest.mark.asyncio
async def test_archive_mode_passes_none_target_params(monkeypatch):
    from unittest.mock import AsyncMock, MagicMock

    captured: dict = {}

    async def track_archive(**kwargs):
        captured.update(kwargs)
        return []

    monkeypatch.setattr(main, "validate_config", AsyncMock())
    monkeypatch.setattr(main, "init_db", lambda _: MagicMock())
    monkeypatch.setattr(main, "load_proxy_rotator", lambda: None)
    monkeypatch.setattr(main.nostr_cfg, "private_key_hex", None)
    monkeypatch.setattr(main, "run_wayback_archiver", track_archive)

    args = argparse.Namespace(module="archive", dry_run=True, since_days=1)
    await main.main(args)

    assert captured["target_entities"] is None
    assert captured["target_keywords"] is None
