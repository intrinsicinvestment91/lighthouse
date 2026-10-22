"""Unit tests for modules/adsb_sentinel.py"""

import copy
from unittest.mock import AsyncMock, patch

import config
from modules.adsb_sentinel import (
    _filter_facilities_for_targets,
    fetch_adsb_in_radius,
    is_unusual_adsb,
    is_us_military_icao_hex,
    run_adsb_sentinel,
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

_PATTERNS = ["EXEC", "AFSOC"]
_MIL_PREFIXES = ["ae"]


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


# ---------------------------------------------------------------------------
# is_unusual_adsb
# ---------------------------------------------------------------------------


def test_is_unusual_military_flag():
    assert is_unusual_adsb({"military": True}, _PATTERNS, _MIL_PREFIXES) is True


def test_is_unusual_interesting_flag():
    assert is_unusual_adsb({"interesting": True}, _PATTERNS, _MIL_PREFIXES) is True


def test_is_unusual_military_hex_uppercase():
    assert is_unusual_adsb({"hex": "AE1234"}, _PATTERNS, _MIL_PREFIXES) is True


def test_is_unusual_military_hex_lowercase():
    assert is_unusual_adsb({"hex": "ae5678"}, _PATTERNS, _MIL_PREFIXES) is True


def test_is_unusual_callsign_pattern():
    assert is_unusual_adsb({"hex": "a00001", "flight": "EXEC01"}, _PATTERNS, _MIL_PREFIXES) is True
    assert is_unusual_adsb({"hex": "a00002", "flight": "AFSOC42"}, _PATTERNS, _MIL_PREFIXES) is True


def test_is_unusual_normal_aircraft():
    ac = {"hex": "a12345", "flight": "UAL123", "military": False, "interesting": False}
    assert is_unusual_adsb(ac, _PATTERNS, _MIL_PREFIXES) is False


def test_is_unusual_empty_callsign_not_flagged():
    assert is_unusual_adsb({"hex": "a12345", "flight": ""}, _PATTERNS, _MIL_PREFIXES) is False
    assert is_unusual_adsb({"hex": "a12345"}, _PATTERNS, _MIL_PREFIXES) is False


def test_is_unusual_none_callsign_not_flagged():
    assert is_unusual_adsb({"hex": "a12345", "flight": None}, _PATTERNS, _MIL_PREFIXES) is False


# ---------------------------------------------------------------------------
# fetch_adsb_in_radius
# ---------------------------------------------------------------------------


def test_filter_facilities_for_targets_none_keeps_all():
    assert _filter_facilities_for_targets(_FACILITIES, None, None, None) == _FACILITIES


def test_filter_facilities_for_targets_empty_lists_keeps_all():
    assert _filter_facilities_for_targets(_FACILITIES, [], [], []) == _FACILITIES


def test_filter_facilities_for_targets_entity_name_match():
    result = _filter_facilities_for_targets(_FACILITIES, ["darpa hq"], None, None)
    assert len(result) == 1
    assert result[0]["name"] == "DARPA HQ"


def test_filter_facilities_for_targets_keyword_match():
    result = _filter_facilities_for_targets(_FACILITIES, None, ["fusion"], None)
    assert len(result) == 1
    assert result[0]["name"] == "Helion Facility"


def test_filter_facilities_for_targets_region_match():
    result = _filter_facilities_for_targets(_FACILITIES, None, None, ["us"])
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


async def test_run_adsb_sentinel_returns_early_when_no_facilities_remain(
    httpx_mock, tmp_db, monkeypatch
):
    monkeypatch.setattr(config.flight_cfg, "facilities", _FACILITIES)

    alerts = await run_adsb_sentinel(
        tmp_db,
        dry_run=True,
        target_entities=["Nonexistent Entity"],
        target_keywords=["unrelated"],
        target_regions=["eu"],
    )

    assert alerts == []
    assert len(httpx_mock.get_requests()) == 0


async def test_fetch_adsb_in_radius_returns_aircraft(httpx_mock):
    httpx_mock.add_response(
        json={
            "ac": [
                {
                    "hex": "ae1234",
                    "flight": "EXEC01 ",
                    "lat": 37.5,
                    "lon": -121.5,
                    "military": True,
                    "interesting": False,
                }
            ],
            "total": 1,
            "now": 1745574000,
        }
    )
    import httpx as _httpx

    async with _httpx.AsyncClient() as client:
        aircraft = await fetch_adsb_in_radius(37.0, -121.0, 27.0, client)

    assert len(aircraft) == 1
    assert aircraft[0]["hex"] == "ae1234"
    assert aircraft[0]["military"] is True


async def test_fetch_adsb_in_radius_empty(httpx_mock):
    httpx_mock.add_response(json={"ac": [], "total": 0, "now": 1745574000})
    import httpx as _httpx

    async with _httpx.AsyncClient() as client:
        aircraft = await fetch_adsb_in_radius(37.0, -121.0, 27.0, client)

    assert aircraft == []


# ---------------------------------------------------------------------------
# run_adsb_sentinel
# ---------------------------------------------------------------------------

_MILITARY_HIT = {
    "hex": "ae1234",
    "flight": "EXEC01 ",
    "lat": 37.1,
    "lon": -121.1,
    "military": True,
    "interesting": False,
}


async def test_adsb_normalization_marks_ae_hex_as_military(
    httpx_mock, tmp_db, monkeypatch, tmp_db_path
):
    from modules import db

    monkeypatch.setattr(db, "DB_PATH", tmp_db_path)
    monkeypatch.setattr(
        config.flight_cfg,
        "facilities",
        [{"name": "TestFacility", "lat": 37.0, "lon": -121.0, "radius_km": 50}],
    )
    httpx_mock.add_response(
        json={
            "ac": [
                {
                    "hex": "ae1234",
                    "flight": "PAT16",
                    "lat": 37.1,
                    "lon": -121.1,
                    "military": False,
                    "interesting": False,
                }
            ],
            "total": 1,
            "now": 1745574000,
        }
    )

    alerts = await run_adsb_sentinel(tmp_db, dry_run=False)

    assert len(alerts) == 1
    assert alerts[0]["hex"] == "ae1234"
    assert alerts[0]["callsign"] == "PAT16"
    assert alerts[0]["military"] is True
    assert "callsign_enrichment" in alerts[0]
    assert alerts[0]["callsign_enrichment"]["schema_version"] == 1
    assert alerts[0]["military_style_callsign"] is True

    row = tmp_db.execute("SELECT military FROM adsb_observations WHERE hex = 'ae1234'").fetchone()
    assert row is not None
    assert row[0] == 1

    callsign_row = tmp_db.execute(
        "SELECT source, icao_hex, facility FROM callsign_observations"
        " WHERE normalized_callsign = ?",
        ("PAT16",),
    ).fetchone()
    assert callsign_row is not None
    assert callsign_row[0] == "adsb_sentinel"
    assert callsign_row[1] == "ae1234"
    assert callsign_row[2] == "TestFacility"


async def test_adsb_normalization_preserves_feed_military_true(
    httpx_mock, tmp_db, monkeypatch, tmp_db_path
):
    from modules import db

    monkeypatch.setattr(db, "DB_PATH", tmp_db_path)
    monkeypatch.setattr(
        config.flight_cfg,
        "facilities",
        [{"name": "TestFacility", "lat": 37.0, "lon": -121.0, "radius_km": 50}],
    )
    httpx_mock.add_response(json={"ac": [_MILITARY_HIT], "total": 1, "now": 1745574000})

    alerts = await run_adsb_sentinel(tmp_db, dry_run=False)

    assert len(alerts) == 1
    assert alerts[0]["military"] is True
    assert "callsign_enrichment" in alerts[0]

    callsign_row = tmp_db.execute(
        "SELECT source, icao_hex, facility FROM callsign_observations"
        " WHERE normalized_callsign = ?",
        ("EXEC01",),
    ).fetchone()
    assert callsign_row is not None
    assert callsign_row[0] == "adsb_sentinel"
    assert callsign_row[1] == "ae1234"
    assert callsign_row[2] == "TestFacility"


async def test_adsb_known_callsign_military_style_without_ae_military(
    httpx_mock, tmp_db, monkeypatch
):
    monkeypatch.setattr(
        config.flight_cfg,
        "facilities",
        [{"name": "TestFacility", "lat": 37.0, "lon": -121.0, "radius_km": 50}],
    )
    httpx_mock.add_response(
        json={
            "ac": [
                {
                    "hex": "a12345",
                    "flight": "PAT16",
                    "lat": 37.1,
                    "lon": -121.1,
                    "military": False,
                    "interesting": False,
                }
            ],
            "total": 1,
            "now": 1745574000,
        }
    )

    alerts = await run_adsb_sentinel(tmp_db, dry_run=True)

    assert len(alerts) == 1
    assert alerts[0]["military"] is False
    assert alerts[0]["military_style_callsign"] is True
    assert alerts[0]["callsign_enrichment"]["prefix"] == "PAT"


async def test_adsb_unknown_callsign_military_style_false(httpx_mock, tmp_db, monkeypatch):
    monkeypatch.setattr(
        config.flight_cfg,
        "facilities",
        [{"name": "TestFacility", "lat": 37.0, "lon": -121.0, "radius_km": 50}],
    )
    httpx_mock.add_response(
        json={
            "ac": [
                {
                    "hex": "a12345",
                    "flight": "UAL456",
                    "lat": 37.1,
                    "lon": -121.1,
                    "military": False,
                    "interesting": True,
                }
            ],
            "total": 1,
            "now": 1745574000,
        }
    )

    alerts = await run_adsb_sentinel(tmp_db, dry_run=True)

    assert len(alerts) == 1
    assert alerts[0]["military_style_callsign"] is False
    assert "callsign_enrichment" in alerts[0]


async def test_adsb_normalization_keeps_non_ae_non_military(httpx_mock, tmp_db, monkeypatch):
    monkeypatch.setattr(
        config.flight_cfg,
        "facilities",
        [{"name": "TestFacility", "lat": 37.0, "lon": -121.0, "radius_km": 50}],
    )
    httpx_mock.add_response(
        json={
            "ac": [
                {
                    "hex": "a12345",
                    "flight": "UAL456",
                    "lat": 37.1,
                    "lon": -121.1,
                    "military": False,
                    "interesting": False,
                }
            ],
            "total": 1,
            "now": 1745574000,
        }
    )

    alerts = await run_adsb_sentinel(tmp_db, dry_run=True)

    assert alerts == []


async def test_run_adsb_sentinel_flags_military(httpx_mock, tmp_db, monkeypatch, tmp_db_path):
    from modules import db

    monkeypatch.setattr(db, "DB_PATH", tmp_db_path)
    monkeypatch.setattr(
        config.flight_cfg,
        "facilities",
        [{"name": "TestFacility", "lat": 37.0, "lon": -121.0, "radius_km": 50}],
    )
    httpx_mock.add_response(json={"ac": [_MILITARY_HIT], "total": 1, "now": 1745574000})

    alerts = await run_adsb_sentinel(tmp_db, dry_run=False)

    assert len(alerts) == 1
    assert alerts[0]["hex"] == "ae1234"
    assert alerts[0]["military"] is True
    assert alerts[0]["facility"] == "TestFacility"

    row = tmp_db.execute(
        "SELECT callsign, military FROM adsb_observations WHERE hex = 'ae1234'"
    ).fetchone()
    assert row is not None
    assert row[1] == 1

    callsign_row = tmp_db.execute(
        "SELECT source, icao_hex, facility FROM callsign_observations"
        " WHERE normalized_callsign = ?",
        ("EXEC01",),
    ).fetchone()
    assert callsign_row is not None
    assert callsign_row[0] == "adsb_sentinel"
    assert callsign_row[1] == "ae1234"
    assert callsign_row[2] == "TestFacility"


async def test_run_adsb_sentinel_flags_interesting(httpx_mock, tmp_db, monkeypatch, tmp_db_path):
    from modules import db

    monkeypatch.setattr(db, "DB_PATH", tmp_db_path)
    monkeypatch.setattr(
        config.flight_cfg,
        "facilities",
        [{"name": "TestFacility", "lat": 37.0, "lon": -121.0, "radius_km": 50}],
    )
    httpx_mock.add_response(
        json={
            "ac": [
                {
                    "hex": "a99999",
                    "flight": "PRIV01",
                    "lat": 37.1,
                    "lon": -121.1,
                    "military": False,
                    "interesting": True,
                }
            ],
            "total": 1,
            "now": 1745574000,
        }
    )

    alerts = await run_adsb_sentinel(tmp_db, dry_run=False)

    assert len(alerts) == 1
    assert alerts[0]["interesting"] is True

    callsign_row = tmp_db.execute(
        "SELECT source, icao_hex, facility FROM callsign_observations"
        " WHERE normalized_callsign = ?",
        ("PRIV01",),
    ).fetchone()
    assert callsign_row is not None
    assert callsign_row[0] == "adsb_sentinel"
    assert callsign_row[1] == "a99999"
    assert callsign_row[2] == "TestFacility"


async def test_run_adsb_sentinel_normal_aircraft_not_flagged(httpx_mock, tmp_db, monkeypatch):
    monkeypatch.setattr(
        config.flight_cfg,
        "facilities",
        [{"name": "TestFacility", "lat": 37.0, "lon": -121.0, "radius_km": 50}],
    )
    httpx_mock.add_response(
        json={
            "ac": [
                {
                    "hex": "a12345",
                    "flight": "UAL456",
                    "lat": 37.1,
                    "lon": -121.1,
                    "military": False,
                    "interesting": False,
                }
            ],
            "total": 1,
            "now": 1745574000,
        }
    )

    alerts = await run_adsb_sentinel(tmp_db, dry_run=True)
    assert len(alerts) == 0


async def test_run_adsb_sentinel_dry_run_skips_db(httpx_mock, tmp_db, monkeypatch):
    monkeypatch.setattr(
        config.flight_cfg,
        "facilities",
        [{"name": "TestFacility", "lat": 37.0, "lon": -121.0, "radius_km": 50}],
    )
    httpx_mock.add_response(json={"ac": [_MILITARY_HIT], "total": 1, "now": 1745574000})

    await run_adsb_sentinel(tmp_db, dry_run=True)

    count = tmp_db.execute("SELECT COUNT(*) FROM adsb_observations").fetchone()[0]
    assert count == 0


async def test_run_adsb_sentinel_alert_has_score_and_provenance(
    httpx_mock, tmp_db, monkeypatch, tmp_db_path
):
    from modules import db

    monkeypatch.setattr(db, "DB_PATH", tmp_db_path)
    monkeypatch.setattr(
        config.flight_cfg,
        "facilities",
        [{"name": "ScoreFacility", "lat": 37.0, "lon": -121.0, "radius_km": 50}],
    )
    httpx_mock.add_response(json={"ac": [_MILITARY_HIT], "total": 1, "now": 1745574000})

    alerts = await run_adsb_sentinel(tmp_db, dry_run=False)

    assert len(alerts) == 1
    assert "score" in alerts[0]
    assert alerts[0]["score_label"] in ("low", "medium", "high")
    assert alerts[0]["provenance"]["source_type"] == "adsb_lol"
    # AE-prefix hex → uniqueness=1.0 → higher score
    assert alerts[0]["score"] > 0.7

    rows = db.fetch_alerts_by_source("adsb_sentinel")
    assert len(rows) == 1
    assert rows[0]["alert"]["hex"] == "ae1234"


async def test_run_adsb_sentinel_dry_run_skips_alert_insert(
    httpx_mock, tmp_db, monkeypatch, tmp_db_path
):
    from modules import db

    monkeypatch.setattr(db, "DB_PATH", tmp_db_path)
    db.init_alerts_table()
    monkeypatch.setattr(
        config.flight_cfg,
        "facilities",
        [{"name": "DryFacility", "lat": 37.0, "lon": -121.0, "radius_km": 50}],
    )
    httpx_mock.add_response(json={"ac": [_MILITARY_HIT], "total": 1, "now": 1745574000})

    alerts = await run_adsb_sentinel(tmp_db, dry_run=True)

    assert len(alerts) == 1
    assert "score" in alerts[0]
    rows = db.fetch_alerts_by_source("adsb_sentinel")
    assert rows == []


async def test_run_adsb_sentinel_api_failure_continues(tmp_db, monkeypatch):
    monkeypatch.setattr(
        config.flight_cfg,
        "facilities",
        [{"name": "FacA", "lat": 37.0, "lon": -121.0, "radius_km": 50}],
    )
    with patch(
        "modules.adsb_sentinel.fetch_adsb_in_radius",
        new=AsyncMock(side_effect=Exception("connection refused")),
    ):
        result = await run_adsb_sentinel(tmp_db, dry_run=True)

    assert result == []


async def test_run_adsb_sentinel_multiple_facilities(httpx_mock, tmp_db, monkeypatch):
    monkeypatch.setattr(
        config.flight_cfg,
        "facilities",
        [
            {"name": "FacA", "lat": 37.0, "lon": -121.0, "radius_km": 50},
            {"name": "FacB", "lat": 35.9, "lon": -79.9, "radius_km": 50},
        ],
    )
    httpx_mock.add_response(json={"ac": [_MILITARY_HIT], "total": 1, "now": 1745574000})
    httpx_mock.add_response(json={"ac": [], "total": 0, "now": 1745574000})

    alerts = await run_adsb_sentinel(tmp_db, dry_run=True)

    assert len(alerts) == 1
    assert alerts[0]["facility"] == "FacA"


async def test_run_adsb_sentinel_persists_callsign_observation(
    httpx_mock, tmp_db, monkeypatch, tmp_db_path
):
    from modules import db

    monkeypatch.setattr(db, "DB_PATH", tmp_db_path)
    monkeypatch.setattr(
        config.flight_cfg,
        "facilities",
        [{"name": "DARPA HQ", "lat": 37.0, "lon": -121.0, "radius_km": 50}],
    )
    httpx_mock.add_response(
        json={
            "ac": [
                {
                    "hex": "ae1234",
                    "flight": "PAT16",
                    "lat": 37.1,
                    "lon": -121.1,
                    "military": False,
                    "interesting": False,
                }
            ],
            "total": 1,
            "now": 1745574000,
        }
    )

    await run_adsb_sentinel(tmp_db, dry_run=False)

    history = db.fetch_callsign_history("PAT16", limit=10)
    assert len(history) == 1
    assert history[0]["source"] == "adsb_sentinel"
    assert history[0]["facility"] == "DARPA HQ"
    assert history[0]["icao_hex"] == "ae1234"


async def test_run_adsb_sentinel_dry_run_skips_callsign_observation(
    httpx_mock, tmp_db, monkeypatch, tmp_db_path
):
    from modules import db

    monkeypatch.setattr(db, "DB_PATH", tmp_db_path)
    monkeypatch.setattr(
        config.flight_cfg,
        "facilities",
        [{"name": "DARPA HQ", "lat": 37.0, "lon": -121.0, "radius_km": 50}],
    )
    httpx_mock.add_response(
        json={
            "ac": [
                {
                    "hex": "ae1234",
                    "flight": "PAT16",
                    "lat": 37.1,
                    "lon": -121.1,
                    "military": False,
                    "interesting": False,
                }
            ],
            "total": 1,
            "now": 1745574000,
        }
    )

    await run_adsb_sentinel(tmp_db, dry_run=True)

    assert db.fetch_recent_callsigns() == []


async def test_run_adsb_sentinel_callsign_insert_failure_isolated(
    httpx_mock, tmp_db, monkeypatch, tmp_db_path
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
        [{"name": "DARPA HQ", "lat": 37.0, "lon": -121.0, "radius_km": 50}],
    )
    httpx_mock.add_response(json={"ac": [_MILITARY_HIT], "total": 1, "now": 1745574000})

    alerts = await run_adsb_sentinel(tmp_db, dry_run=False)

    assert len(alerts) == 1
    row = tmp_db.execute("SELECT callsign FROM adsb_observations WHERE hex = 'ae1234'").fetchone()
    assert row is not None
    assert db.fetch_recent_callsigns() == []
