"""
Module 8 — Flight Tracker Correlator.

Queries the OpenSky Network public API for aircraft in bounding boxes
around key research facilities. Flags unusual callsigns, anonymous
transponders, and known government transport patterns.

All data is sourced from OpenSky's public real-time feed — no classified
or restricted sources are used.
"""

import json
import logging
import math
import sqlite3
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx

from config import flight_cfg
from modules import db
from modules.alerts import ALERT_SCHEMA_VERSION
from modules.callsigns import enrich_callsign
from modules.provenance import ProvenanceRecord
from modules.proxy_rotator import ProxyRotator, make_httpx_client
from modules.retry import http_retry
from modules.scoring import AlertScore

logger = logging.getLogger(__name__)


def _facility_search_text(facility: dict) -> str:
    """Build lowercase searchable text from facility string fields."""
    parts: list[str] = []
    for key in ("name", "label", "entity", "owner", "facility", "description", "region"):
        value = facility.get(key)
        if isinstance(value, str) and value:
            parts.append(value)
    aliases = facility.get("aliases", [])
    if isinstance(aliases, str) and aliases:
        parts.append(aliases)
    elif isinstance(aliases, list):
        for alias in aliases:
            if isinstance(alias, str) and alias:
                parts.append(alias)
    for value in facility.values():
        if isinstance(value, str) and value and value not in parts:
            parts.append(value)
    return " ".join(parts).lower()


def _facility_matches_targets(
    facility: dict,
    target_entities: list[str] | None,
    target_keywords: list[str] | None,
    target_regions: list[str] | None,
) -> bool:
    """Return True when facility matches any active target filter."""
    text_targets: list[str] = []
    if target_entities:
        text_targets.extend(target_entities)
    if target_keywords:
        text_targets.extend(target_keywords)

    haystack = _facility_search_text(facility)

    text_match = False
    if text_targets:
        text_match = any(
            target.lower() in haystack or haystack in target.lower() for target in text_targets
        )

    region_match = False
    if target_regions:
        fac_region = facility.get("region")
        if isinstance(fac_region, str) and fac_region:
            region_match = any(fac_region.lower() == region.lower() for region in target_regions)
        if not region_match:
            region_match = any(region.lower() in haystack for region in target_regions)

    if text_targets and target_regions:
        return text_match or region_match
    if text_targets:
        return text_match
    if target_regions:
        return region_match
    return True


def _filter_facilities_for_targets(
    facilities: list[dict],
    target_entities: list[str] | None = None,
    target_keywords: list[str] | None = None,
    target_regions: list[str] | None = None,
) -> list[dict]:
    """Return facilities matching target entities, keywords, or regions."""
    has_entity_filter = bool(target_entities)
    has_keyword_filter = bool(target_keywords)
    has_region_filter = bool(target_regions)
    if not has_entity_filter and not has_keyword_filter and not has_region_filter:
        return facilities

    return [
        facility
        for facility in facilities
        if _facility_matches_targets(facility, target_entities, target_keywords, target_regions)
    ]


def _attach_alert_score(alert: dict) -> None:
    score = AlertScore(
        source_reliability=0.75,
        keyword_strength=0.6,
        recency=1.0,
        uniqueness=0.5,
        watchlist_match=1.0,
    )
    alert["score"] = round(score.total(), 3)
    alert["score_label"] = score.label()
    alert["alert_type"] = "unusual_aircraft"
    alert["schema_version"] = ALERT_SCHEMA_VERSION


def _attach_alert_provenance(alert: dict) -> None:
    provenance = ProvenanceRecord(
        source_type="opensky_api",
        source_url="https://opensky-network.org",
        retrieved_at=datetime.now(UTC).isoformat(),
        metadata={
            "icao24": alert.get("icao24", ""),
            "facility": alert.get("facility", ""),
            "callsign": alert.get("callsign", ""),
        },
    )
    alert["provenance"] = provenance.to_dict()


# OpenSky state-vector array indices
_OPENSKY_KEYS = [
    "icao24",
    "callsign",
    "origin_country",
    "time_position",
    "last_contact",
    "longitude",
    "latitude",
    "baro_altitude",
    "on_ground",
    "velocity",
    "true_track",
    "vertical_rate",
    "sensors",
    "geo_altitude",
    "squawk",
    "spi",
    "position_source",
]


def _bounding_box(lat: float, lon: float, radius_km: float) -> dict[str, float]:
    """Return {lat_min, lon_min, lat_max, lon_max} for a circle approximation."""
    deg_lat = radius_km / 111.0
    deg_lon = radius_km / (111.0 * math.cos(math.radians(lat)))
    return {
        "lat_min": lat - deg_lat,
        "lon_min": lon - deg_lon,
        "lat_max": lat + deg_lat,
        "lon_max": lon + deg_lon,
    }


def is_us_military_icao_hex(value: str | None) -> bool:
    """Return True for U.S. military ICAO allocation (AE0000–AEFFFF)."""
    if value is None:
        return False
    normalized = value.strip().lower()
    if normalized.startswith("~"):
        normalized = normalized[1:].lstrip()
    return normalized.startswith("ae")


def is_unusual(state: dict, patterns: list[str]) -> bool:
    """
    Return True if the aircraft looks unusual:
    - No callsign (anonymous transponder)
    - Callsign matches a known government/military pattern
    - ICAO24 in U.S. military allocation (AE range)
    """
    if is_us_military_icao_hex(state.get("icao24")):
        return True
    callsign = (state.get("callsign") or "").strip()
    if not callsign:
        return True
    callsign_upper = callsign.upper()
    return any(p in callsign_upper for p in patterns)


@http_retry
async def fetch_states_in_box(
    lat_min: float,
    lon_min: float,
    lat_max: float,
    lon_max: float,
    client: httpx.AsyncClient,
) -> list[dict]:
    """Return state-vector dicts from OpenSky for a bounding box."""
    url = f"{flight_cfg.opensky_base}/states/all"
    params = {"lamin": lat_min, "lomin": lon_min, "lamax": lat_max, "lomax": lon_max}
    auth = None
    if flight_cfg.opensky_user and flight_cfg.opensky_pass:
        auth = (flight_cfg.opensky_user, flight_cfg.opensky_pass)

    resp = await client.get(url, params=params, auth=auth)
    resp.raise_for_status()
    body = resp.json()
    states_raw = body.get("states") or []
    return [dict(zip(_OPENSKY_KEYS, s, strict=False)) for s in states_raw]


async def run_flight_tracker(
    conn: sqlite3.Connection,
    dry_run: bool = False,
    rotator: ProxyRotator | None = None,
    target_entities: list[str] | None = None,
    target_keywords: list[str] | None = None,
    target_regions: list[str] | None = None,
) -> list[dict]:
    """Scan all configured facilities; return unusual-activity alert dicts."""
    all_facilities = list(flight_cfg.facilities)
    if target_entities or target_keywords or target_regions:
        facilities = _filter_facilities_for_targets(
            all_facilities, target_entities, target_keywords, target_regions
        )
        logger.info(
            "[Flight] Target filter active: %d of %d facilities selected",
            len(facilities),
            len(all_facilities),
        )
        if not facilities:
            logger.info("[Flight] Target filter excluded all facilities — skipping")
            return []
    else:
        facilities = all_facilities

    alerts: list[dict] = []
    now = int(time.time())

    async with make_httpx_client(rotator, timeout=flight_cfg.http_timeout) as client:
        for facility in facilities:
            name = facility["name"]
            bb = _bounding_box(facility["lat"], facility["lon"], facility["radius_km"])
            try:
                states = await fetch_states_in_box(
                    bb["lat_min"], bb["lon_min"], bb["lat_max"], bb["lon_max"], client
                )
            except Exception as exc:
                logger.warning("[Flight] Failed to query %s: %s", name, exc)
                continue

            unusual = [s for s in states if is_unusual(s, flight_cfg.unusual_callsign_patterns)]
            logger.info(
                "[Flight] %s: %d aircraft, %d unusual",
                name,
                len(states),
                len(unusual),
            )

            for state in unusual:
                icao24 = state.get("icao24") or "UNKNOWN"
                raw_callsign = (state.get("callsign") or "").strip()
                callsign_info = enrich_callsign(raw_callsign or None)
                callsign = raw_callsign or "<no-callsign>"
                lat = state.get("latitude")
                lon = state.get("longitude")
                military = is_us_military_icao_hex(icao24)

                if not dry_run:
                    conn.execute(
                        """
                        INSERT OR IGNORE INTO flight_observations
                            (icao24, callsign, facility, observed_at, lat, lon)
                        VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (icao24, callsign, name, now, lat, lon),
                    )
                    conn.commit()
                    try:
                        db.insert_callsign_observation(
                            callsign_enrichment=callsign_info.to_dict(),
                            source="flight_tracker",
                            facility=name,
                            icao_hex=icao24,
                            lat=lat,
                            lon=lon,
                            observed_at=now,
                        )
                    except Exception:
                        logger.exception("[Callsign] Failed to persist callsign observation")

                alerts.append(
                    {
                        "facility": name,
                        "icao24": icao24,
                        "callsign": callsign,
                        "callsign_enrichment": callsign_info.to_dict(),
                        "military_style_callsign": callsign_info.military_style,
                        "lat": lat,
                        "lon": lon,
                        "military": military,
                        "bounding_box": {
                            "north": bb["lat_max"],
                            "south": bb["lat_min"],
                            "east": bb["lon_max"],
                            "west": bb["lon_min"],
                        },
                    }
                )

    if alerts:
        logger.warning("[Flight] %d unusual observations total", len(alerts))

    for alert in alerts:
        _attach_alert_score(alert)
        _attach_alert_provenance(alert)
    if not dry_run:
        db.init_alerts_table()
        for alert in alerts:
            db.insert_alert("flight_tracker", alert)

    out_dir = Path(flight_cfg.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = int(time.time())
    (out_dir / f"flights_{ts}.json").write_text(json.dumps(alerts, indent=2))

    return alerts


if __name__ == "__main__":
    import asyncio
    import logging as _logging

    from modules.data_paths import data_path
    from modules.state_db import init_db

    _logging.basicConfig(level=logging.INFO)
    _conn = init_db(data_path("state.db"))
    asyncio.run(run_flight_tracker(_conn, dry_run=True))
