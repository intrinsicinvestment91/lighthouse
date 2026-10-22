"""Unit tests for modules/flight_tracker.py"""

import copy

from pytest_httpx import HTTPXMock

import config
from modules.flight_tracker import (
    _bounding_box,
    _filter_facilities_for_targets,
    fetch_states_in_box,
    is_unusual,
    is_us_military_icao_hex,
    run_flight_tracker,
)

_FACILITIES = [
    {"name": "NIF (LLNL)", "lat": 37.6913, "lon": -121.7055, "radius_km": 50},
    {"name": "DARPA HQ", "lat": 38.8796, "lon": -77.1063, "radius_km": 30},
    {
        "name": "Helion Facility",
        "label": "fusion test site",
        "region": "us",
        "lat": 47.0,
        "lon": -122.0,
        "radius_km": 25,
    },
]

_PATTERNS = ["EXEC", "AFSOC", "SPAR"]

_AE_STATE = [
    "ae1234",
    "UAL123  ",
    "United States",
    1745574000,
    1745574000,
    -121.5,
    37.5,
    10000.0,
    False,
    250.0,
    90.0,
    0.0,
    None,
    10500.0,
    "1200",
    False,
    0,
]


# ---------------------------------------------------------------------------
# is_us_military_icao_hex
# ---------------------------------------------------------------------------


def test_is_us_military_icao_hex_accepts_ae_lowercase():
    assert is_us_military_icao_hex("ae1234") is True


def test_is_us_military_icao_hex_accepts_ae_uppercase():
    assert is_us_military_icao_hex("AE1234") is True


def test_is_us_military_icao_hex_strips_whitespace():
    assert is_us_military_icao_hex(" ae1234 ") is True


def test_is_us_military_icao_hex_rejects_non_ae():
    assert is_us_military_icao_hex("a12345") is False


def test_is_us_military_icao_hex_rejects_none():
    assert is_us_military_icao_hex(None) is False


def test_bounding_box_roughly_correct():
    bb = _bounding_box(37.0, -121.0, 100.0)
    assert bb["lat_min"] < 37.0 < bb["lat_max"]
    assert bb["lon_min"] < -121.0 < bb["lon_max"]
    # 100 km at mid-latitudes ≈ 0.9° lat
    assert abs((bb["lat_max"] - bb["lat_min"]) / 2 - 100 / 111.0) < 0.01


def test_is_unusual_no_callsign():
    assert is_unusual({"callsign": ""}, _PATTERNS) is True
    assert is_unusual({"callsign": None}, _PATTERNS) is True
    assert is_unusual({}, _PATTERNS) is True


def test_is_unusual_matches_pattern():
    assert is_unusual({"callsign": "EXEC01"}, _PATTERNS) is True
    assert is_unusual({"callsign": "AFSOC42"}, _PATTERNS) is True


def test_is_unusual_normal_callsign():
    assert is_unusual({"callsign": "UAL123"}, _PATTERNS) is False
    assert is_unusual({"callsign": "SWA456"}, _PATTERNS) is False


def test_is_unusual_ae_hex():
    assert is_unusual({"callsign": "UAL123", "icao24": "ae1234"}, _PATTERNS) is True


def test_is_unusual_case_insensitive():
    assert is_unusual({"callsign": "exec01"}, _PATTERNS) is True


def test_filter_facilities_for_targets_none_keeps_all():
    assert _filter_facilities_for_targets(_FACILITIES, None, None, None) == _FACILITIES


def test_filter_facilities_for_targets_empty_lists_keeps_all():
    assert _filter_facilities_for_targets(_FACILITIES, [], [], []) == _FACILITIES


def test_filter_facilities_for_targets_entity_case_insensitive():
    result = _filter_facilities_for_targets(_FACILITIES, ["helion facility"], None, None)
    assert len(result) == 1
    assert result[0]["name"] == "Helion Facility"


def test_filter_facilities_for_targets_keyword_in_label():
    result = _filter_facilities_for_targets(_FACILITIES, None, ["fusion"], None)
    assert len(result) == 1
    assert result[0]["name"] == "Helion Facility"


def test_filter_facilities_for_targets_region_exact_match():
    result = _filter_facilities_for_targets(_FACILITIES, None, None, ["US"])
    assert len(result) == 1
    assert result[0]["region"] == "us"


def test_filter_facilities_for_targets_no_match_returns_empty():
    result = _filter_facilities_for_targets(_FACILITIES, ["Nonexistent"], ["unrelated"], ["eu"])
    assert result == []


def test_filter_facilities_for_targets_does_not_mutate_original():
    original = [dict(f) for f in _FACILITIES]
    _filter_facilities_for_targets(_FACILITIES, ["DARPA"], None, None)
    assert original == _FACILITIES


def test_filter_facilities_for_targets_entity_matches_alias():
    facilities = [
        {
            "name": "NIF",
            "aliases": ["Lawrence Livermore National Laboratory"],
            "lat": 37.0,
            "lon": -121.0,
            "radius_km": 50,
        }
    ]
    result = _filter_facilities_for_targets(
        facilities, ["Lawrence Livermore National Laboratory"], None, None
    )
    assert len(result) == 1
    assert result[0]["name"] == "NIF"


def test_filter_facilities_for_targets_helion_alias():
    facilities = [
        {
            "name": "Everett",
            "aliases": ["Helion Energy"],
            "lat": 47.0,
            "lon": -122.0,
            "radius_km": 40,
        }
    ]
    result = _filter_facilities_for_targets(facilities, ["Helion Energy"], None, None)
    assert len(result) == 1
    assert result[0]["name"] == "Everett"


def test_filter_facilities_for_targets_alias_list_not_mutated():
    facility = {
        "name": "NIF",
        "aliases": ["Lawrence Livermore National Laboratory"],
        "lat": 37.0,
        "lon": -121.0,
        "radius_km": 50,
    }
    before = copy.deepcopy(facility)
    _filter_facilities_for_targets(
        [facility], ["Lawrence Livermore National Laboratory"], None, None
    )
    assert facility == before


def test_filter_facilities_for_targets_string_alias():
    facilities = [
        {
            "name": "Everett",
            "aliases": "Helion Energy",
            "lat": 47.0,
            "lon": -122.0,
            "radius_km": 40,
        }
    ]
    result = _filter_facilities_for_targets(facilities, ["Helion Energy"], None, None)
    assert len(result) == 1
    assert result[0]["name"] == "Everett"


def test_flight_cfg_facilities_have_key_aliases():
    by_name = {f["name"]: f for f in config.flight_cfg.facilities}
    assert "DARPA" in by_name["DARPA HQ"]["aliases"]
    assert "LLNL" in by_name["NIF (LLNL)"]["aliases"]
    assert "Helion Energy" in by_name["Helion Everett"]["aliases"]
    assert "Commonwealth Fusion Systems" in by_name["Commonwealth Fusion (SPARC)"]["aliases"]


async def test_run_flight_tracker_returns_early_when_no_facilities_remain(
    httpx_mock: HTTPXMock, tmp_db, monkeypatch
):
    monkeypatch.setattr(config.flight_cfg, "facilities", _FACILITIES)

    alerts = await run_flight_tracker(
        tmp_db,
        dry_run=True,
        target_entities=["Nonexistent Entity"],
        target_keywords=["unrelated"],
        target_regions=["eu"],
    )

    assert alerts == []
    assert len(httpx_mock.get_requests()) == 0


async def test_fetch_states_in_box_parses_response(httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        json={
            "states": [
                [
                    "a1b2c3",
                    "UAL123  ",
                    "United States",
                    1745574000,
                    1745574000,
                    -121.5,
                    37.5,
                    10000.0,
                    False,
                    250.0,
                    90.0,
                    0.0,
                    None,
                    10500.0,
                    "1200",
                    False,
                    0,
                ]
            ],
            "time": 1745574000,
        }
    )
    import httpx as _httpx

    async with _httpx.AsyncClient() as client:
        states = await fetch_states_in_box(36.0, -122.0, 38.0, -120.0, client)

    assert len(states) == 1
    assert states[0]["icao24"] == "a1b2c3"
    assert states[0]["callsign"] == "UAL123  "
    assert states[0]["latitude"] == 37.5


async def test_fetch_states_in_box_empty_response(httpx_mock: HTTPXMock):
    httpx_mock.add_response(json={"states": None, "time": 1745574000})
    import httpx as _httpx

    async with _httpx.AsyncClient() as client:
        states = await fetch_states_in_box(36.0, -122.0, 38.0, -120.0, client)
    assert states == []


async def test_run_flight_tracker_ae_hex_marked_military(
    httpx_mock: HTTPXMock, tmp_db, monkeypatch, tmp_db_path
):
    from modules import db

    monkeypatch.setattr(db, "DB_PATH", tmp_db_path)
    monkeypatch.setattr(
        config.flight_cfg,
        "facilities",
        [{"name": "TestFacility", "lat": 37.0, "lon": -121.0, "radius_km": 50}],
    )
    monkeypatch.setattr(config.flight_cfg, "unusual_callsign_patterns", ["EXEC"])

    httpx_mock.add_response(json={"states": [_AE_STATE], "time": 1745574000})

    alerts = await run_flight_tracker(tmp_db, dry_run=False)

    assert len(alerts) == 1
    assert alerts[0]["icao24"] == "ae1234"
    assert alerts[0]["military"] is True
    assert "callsign_enrichment" in alerts[0]

    callsign_row = tmp_db.execute(
        "SELECT source, icao_hex, facility FROM callsign_observations"
        " WHERE normalized_callsign = ?",
        ("UAL123",),
    ).fetchone()
    assert callsign_row is not None
    assert callsign_row[0] == "flight_tracker"
    assert callsign_row[1] == "ae1234"
    assert callsign_row[2] == "TestFacility"


async def test_run_flight_tracker_callsign_enrichment_otis61(
    httpx_mock: HTTPXMock, tmp_db, monkeypatch
):
    otis_state = list(_AE_STATE)
    otis_state[1] = "OTIS61  "
    monkeypatch.setattr(
        config.flight_cfg,
        "facilities",
        [{"name": "TestFacility", "lat": 37.0, "lon": -121.0, "radius_km": 50}],
    )
    monkeypatch.setattr(config.flight_cfg, "unusual_callsign_patterns", ["EXEC"])

    httpx_mock.add_response(json={"states": [otis_state], "time": 1745574000})

    alerts = await run_flight_tracker(tmp_db, dry_run=True)

    assert len(alerts) == 1
    assert alerts[0]["callsign"] == "OTIS61"
    assert "callsign_enrichment" in alerts[0]
    assert alerts[0]["military_style_callsign"] is True
    assert alerts[0]["callsign_enrichment"]["prefix"] == "OTIS"


async def test_run_flight_tracker_unknown_callsign_military_style_false(
    httpx_mock: HTTPXMock, tmp_db, monkeypatch
):
    monkeypatch.setattr(
        config.flight_cfg,
        "facilities",
        [{"name": "TestFacility", "lat": 37.0, "lon": -121.0, "radius_km": 50}],
    )
    monkeypatch.setattr(config.flight_cfg, "unusual_callsign_patterns", ["EXEC"])

    httpx_mock.add_response(json={"states": [_AE_STATE], "time": 1745574000})

    alerts = await run_flight_tracker(tmp_db, dry_run=True)

    assert len(alerts) == 1
    assert alerts[0]["military"] is True
    assert alerts[0]["military_style_callsign"] is False


async def test_run_flight_tracker_no_callsign_enrichment_safe(
    httpx_mock: HTTPXMock, tmp_db, monkeypatch
):
    no_cs_state = [
        "abc123",
        "",
        "US",
        None,
        None,
        -121.1,
        37.1,
        5000.0,
        False,
        200.0,
        0.0,
        0.0,
        None,
        5200.0,
        None,
        False,
        0,
    ]
    monkeypatch.setattr(
        config.flight_cfg,
        "facilities",
        [{"name": "TestFacility", "lat": 37.0, "lon": -121.0, "radius_km": 50}],
    )
    monkeypatch.setattr(config.flight_cfg, "unusual_callsign_patterns", ["EXEC"])

    httpx_mock.add_response(json={"states": [no_cs_state], "time": 1745574000})

    alerts = await run_flight_tracker(tmp_db, dry_run=True)

    assert len(alerts) == 1
    assert alerts[0]["callsign"] == "<no-callsign>"
    assert alerts[0]["callsign_enrichment"]["confidence"] == 0.0
    assert alerts[0]["military_style_callsign"] is False


async def test_run_flight_tracker_ae_hex_flagged_unusual(
    httpx_mock: HTTPXMock, tmp_db, monkeypatch
):
    monkeypatch.setattr(
        config.flight_cfg,
        "facilities",
        [{"name": "TestFacility", "lat": 37.0, "lon": -121.0, "radius_km": 50}],
    )
    monkeypatch.setattr(config.flight_cfg, "unusual_callsign_patterns", ["EXEC"])

    httpx_mock.add_response(json={"states": [_AE_STATE], "time": 1745574000})

    alerts = await run_flight_tracker(tmp_db, dry_run=True)

    assert len(alerts) == 1
    assert alerts[0]["icao24"] == "ae1234"


async def test_run_flight_tracker_records_unusual(
    httpx_mock: HTTPXMock, tmp_db, monkeypatch, tmp_db_path
):
    from modules import db

    monkeypatch.setattr(db, "DB_PATH", tmp_db_path)
    monkeypatch.setattr(
        config.flight_cfg,
        "facilities",
        [{"name": "TestFacility", "lat": 37.0, "lon": -121.0, "radius_km": 50}],
    )
    monkeypatch.setattr(config.flight_cfg, "unusual_callsign_patterns", ["EXEC"])

    httpx_mock.add_response(
        json={
            "states": [
                [
                    "abc123",
                    "EXEC01  ",
                    "US",
                    None,
                    None,
                    -121.1,
                    37.1,
                    5000.0,
                    False,
                    200.0,
                    0.0,
                    0.0,
                    None,
                    5200.0,
                    None,
                    False,
                    0,
                ]
            ],
            "time": 1745574000,
        }
    )

    alerts = await run_flight_tracker(tmp_db, dry_run=False)
    assert len(alerts) == 1
    assert alerts[0]["callsign"] == "EXEC01"
    assert alerts[0]["facility"] == "TestFacility"

    # Verify DB record
    row = tmp_db.execute(
        "SELECT callsign FROM flight_observations WHERE icao24 = 'abc123'"
    ).fetchone()
    assert row is not None
    assert row[0] == "EXEC01"

    callsign_row = tmp_db.execute(
        "SELECT source, icao_hex, facility FROM callsign_observations"
        " WHERE normalized_callsign = ?",
        ("EXEC01",),
    ).fetchone()
    assert callsign_row is not None
    assert callsign_row[0] == "flight_tracker"
    assert callsign_row[1] == "abc123"
    assert callsign_row[2] == "TestFacility"


async def test_run_flight_tracker_dry_run_skips_db(httpx_mock: HTTPXMock, tmp_db, monkeypatch):
    monkeypatch.setattr(
        config.flight_cfg,
        "facilities",
        [{"name": "TestFacility", "lat": 37.0, "lon": -121.0, "radius_km": 50}],
    )
    monkeypatch.setattr(config.flight_cfg, "unusual_callsign_patterns", ["EXEC"])

    httpx_mock.add_response(
        json={
            "states": [
                [
                    "abc999",
                    "EXEC99  ",
                    "US",
                    None,
                    None,
                    -121.1,
                    37.1,
                    5000.0,
                    False,
                    200.0,
                    0.0,
                    0.0,
                    None,
                    5200.0,
                    None,
                    False,
                    0,
                ]
            ],
            "time": 1745574000,
        }
    )

    await run_flight_tracker(tmp_db, dry_run=True)
    count = tmp_db.execute("SELECT COUNT(*) FROM flight_observations").fetchone()[0]
    assert count == 0


async def test_run_flight_tracker_alert_has_score_and_provenance(
    httpx_mock: HTTPXMock, tmp_db, monkeypatch, tmp_db_path
):
    from modules import db

    monkeypatch.setattr(db, "DB_PATH", tmp_db_path)
    monkeypatch.setattr(
        config.flight_cfg,
        "facilities",
        [{"name": "ScoreFacility", "lat": 37.0, "lon": -121.0, "radius_km": 50}],
    )
    monkeypatch.setattr(config.flight_cfg, "unusual_callsign_patterns", ["EXEC"])
    httpx_mock.add_response(
        json={
            "states": [
                [
                    "sc0001",
                    "EXEC01  ",
                    "US",
                    None,
                    None,
                    -121.1,
                    37.1,
                    5000.0,
                    False,
                    200.0,
                    0.0,
                    0.0,
                    None,
                    5200.0,
                    None,
                    False,
                    0,
                ]
            ],
            "time": 1745574000,
        }
    )

    alerts = await run_flight_tracker(tmp_db, dry_run=False)

    assert len(alerts) == 1
    assert "score" in alerts[0]
    assert "score_label" in alerts[0]
    assert alerts[0]["score_label"] in ("low", "medium", "high")
    assert alerts[0]["provenance"]["source_type"] == "opensky_api"

    rows = db.fetch_alerts_by_source("flight_tracker")
    assert len(rows) == 1
    assert rows[0]["alert"]["facility"] == "ScoreFacility"


async def test_run_flight_tracker_dry_run_skips_alert_insert(
    httpx_mock: HTTPXMock, tmp_db, monkeypatch, tmp_db_path
):
    from modules import db

    monkeypatch.setattr(db, "DB_PATH", tmp_db_path)
    db.init_alerts_table()
    monkeypatch.setattr(
        config.flight_cfg,
        "facilities",
        [{"name": "DryFacility", "lat": 37.0, "lon": -121.0, "radius_km": 50}],
    )
    monkeypatch.setattr(config.flight_cfg, "unusual_callsign_patterns", ["EXEC"])
    httpx_mock.add_response(
        json={
            "states": [
                [
                    "dr0001",
                    "EXEC02  ",
                    "US",
                    None,
                    None,
                    -121.1,
                    37.1,
                    5000.0,
                    False,
                    200.0,
                    0.0,
                    0.0,
                    None,
                    5200.0,
                    None,
                    False,
                    0,
                ]
            ],
            "time": 1745574000,
        }
    )

    alerts = await run_flight_tracker(tmp_db, dry_run=True)

    assert len(alerts) == 1
    assert "score" in alerts[0]
    rows = db.fetch_alerts_by_source("flight_tracker")
    assert rows == []


async def test_run_flight_tracker_persists_callsign_observation(
    httpx_mock: HTTPXMock, tmp_db, monkeypatch, tmp_db_path
):
    from modules import db

    monkeypatch.setattr(db, "DB_PATH", tmp_db_path)
    monkeypatch.setattr(
        config.flight_cfg,
        "facilities",
        [{"name": "TestFacility", "lat": 37.0, "lon": -121.0, "radius_km": 50}],
    )
    monkeypatch.setattr(config.flight_cfg, "unusual_callsign_patterns", ["EXEC"])
    httpx_mock.add_response(
        json={
            "states": [
                [
                    "abc123",
                    "EXEC01  ",
                    "US",
                    None,
                    None,
                    -121.1,
                    37.1,
                    5000.0,
                    False,
                    200.0,
                    0.0,
                    0.0,
                    None,
                    5200.0,
                    None,
                    False,
                    0,
                ]
            ],
            "time": 1745574000,
        }
    )

    await run_flight_tracker(tmp_db, dry_run=False)

    history = db.fetch_callsign_history("EXEC01", limit=10)
    assert len(history) == 1
    assert history[0]["source"] == "flight_tracker"
    assert history[0]["facility"] == "TestFacility"
    assert history[0]["icao_hex"] == "abc123"


async def test_run_flight_tracker_dry_run_skips_callsign_observation(
    httpx_mock: HTTPXMock, tmp_db, monkeypatch, tmp_db_path
):
    from modules import db

    monkeypatch.setattr(db, "DB_PATH", tmp_db_path)
    monkeypatch.setattr(
        config.flight_cfg,
        "facilities",
        [{"name": "TestFacility", "lat": 37.0, "lon": -121.0, "radius_km": 50}],
    )
    monkeypatch.setattr(config.flight_cfg, "unusual_callsign_patterns", ["EXEC"])
    httpx_mock.add_response(
        json={
            "states": [
                [
                    "abc999",
                    "EXEC99  ",
                    "US",
                    None,
                    None,
                    -121.1,
                    37.1,
                    5000.0,
                    False,
                    200.0,
                    0.0,
                    0.0,
                    None,
                    5200.0,
                    None,
                    False,
                    0,
                ]
            ],
            "time": 1745574000,
        }
    )

    await run_flight_tracker(tmp_db, dry_run=True)

    assert db.fetch_recent_callsigns() == []


async def test_run_flight_tracker_callsign_insert_failure_isolated(
    httpx_mock: HTTPXMock, tmp_db, monkeypatch, tmp_db_path
):
    from unittest.mock import Mock

    from modules import db

    monkeypatch.setattr(db, "DB_PATH", tmp_db_path)
    monkeypatch.setattr(
        db,
        "insert_callsign_observation",
        Mock(side_effect=RuntimeError("callsign db down")),
    )
    monkeypatch.setattr(
        config.flight_cfg,
        "facilities",
        [{"name": "TestFacility", "lat": 37.0, "lon": -121.0, "radius_km": 50}],
    )
    monkeypatch.setattr(config.flight_cfg, "unusual_callsign_patterns", ["EXEC"])
    httpx_mock.add_response(
        json={
            "states": [
                [
                    "abc123",
                    "EXEC01  ",
                    "US",
                    None,
                    None,
                    -121.1,
                    37.1,
                    5000.0,
                    False,
                    200.0,
                    0.0,
                    0.0,
                    None,
                    5200.0,
                    None,
                    False,
                    0,
                ]
            ],
            "time": 1745574000,
        }
    )

    alerts = await run_flight_tracker(tmp_db, dry_run=False)

    assert len(alerts) == 1
    row = tmp_db.execute(
        "SELECT callsign FROM flight_observations WHERE icao24 = 'abc123'"
    ).fetchone()
    assert row is not None
    assert db.fetch_recent_callsigns() == []


async def test_run_flight_tracker_api_failure_continues(tmp_db, monkeypatch):
    from unittest.mock import AsyncMock, patch

    monkeypatch.setattr(
        config.flight_cfg,
        "facilities",
        [{"name": "FacA", "lat": 37.0, "lon": -121.0, "radius_km": 50}],
    )
    with patch(
        "modules.flight_tracker.fetch_states_in_box",
        new=AsyncMock(side_effect=Exception("connection refused")),
    ):
        result = await run_flight_tracker(tmp_db, dry_run=True)
    assert isinstance(result, list)
