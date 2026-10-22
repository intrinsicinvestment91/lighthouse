"""
Module 6b — ADSB-Exchange Sentinel.

Supplements flight_tracker.py (OpenSky) with adsb.lol community feed data,
which surfaces military flags and callsigns that OpenSky strips or hides.

API: api.adsb.lol/v2 — no auth on community tier; set ADSB_EXCHANGE_API_KEY
for the premium tier. Observations are stored in adsb_observations (separate
from flight_observations) to preserve data lineage.

Flags:
  - military=True  (ADSB-Exchange community classification)
  - interesting=True
  - ICAO hex starting with 'ae' (US military allocation AE0000–AFFFFF)
  - callsign matching SAP patterns in flight_cfg.unusual_callsign_patterns
"""

import json
import logging
import sqlite3
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx

from config import adsb_cfg, flight_cfg
from modules import db
from modules.alerts import ALERT_SCHEMA_VERSION
from modules.callsigns import enrich_callsign
from modules.provenance import ProvenanceRecord
from modules.proxy_rotator import ProxyRotator, make_httpx_client
from modules.retry import http_retry
from modules.scoring import AlertScore

logger = logging.getLogger(__name__)

_KM_TO_NM = 1.0 / 1.852


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
    is_military = bool(alert.get("military", False))
    hex_code = str(alert.get("hex", "")).lower()
    score = AlertScore(
        source_reliability=0.7,
        keyword_strength=1.0 if is_military else 0.6,
        recency=1.0,
        uniqueness=1.0 if hex_code.startswith("ae") else 0.5,
        watchlist_match=1.0,
    )
    alert["score"] = round(score.total(), 3)
    alert["score_label"] = score.label()
    alert["alert_type"] = "adsb_observation"
    alert["schema_version"] = ALERT_SCHEMA_VERSION


def _attach_alert_provenance(alert: dict) -> None:
    provenance = ProvenanceRecord(
        source_type="adsb_lol",
        source_url="https://adsb.lol",
        retrieved_at=datetime.now(UTC).isoformat(),
        metadata={
            "hex": alert.get("hex", ""),
            "callsign": alert.get("callsign", ""),
            "facility": alert.get("facility", ""),
        },
    )
    alert["provenance"] = provenance.to_dict()


def is_us_military_icao_hex(value: str | None) -> bool:
    """Return True for U.S. military ICAO allocation (AE0000–AEFFFF)."""
    if value is None:
        return False
    normalized = value.strip().lower()
    if normalized.startswith("~"):
        normalized = normalized[1:].lstrip()
    return normalized.startswith("ae")


def is_unusual_adsb(
    aircraft: dict,
    callsign_patterns: list[str],
    military_hex_prefixes: list[str],
) -> bool:
    """Return True if the aircraft warrants an alert."""
    if aircraft.get("military"):
        return True
    if aircraft.get("interesting"):
        return True
    hex_code = (aircraft.get("hex") or "").lower().strip()
    if any(hex_code.startswith(pfx) for pfx in military_hex_prefixes):
        return True
    callsign = (aircraft.get("flight") or "").strip().upper()
    return bool(callsign) and any(p in callsign for p in callsign_patterns)


@http_retry
async def fetch_adsb_in_radius(
    lat: float,
    lon: float,
    radius_nm: float,
    client: httpx.AsyncClient,
) -> list[dict]:
    """Return aircraft dicts from adsb.lol within radius_nm of (lat, lon)."""
    url = f"{adsb_cfg.adsb_lol_base}/lat/{lat}/lon/{lon}/dist/{radius_nm:.1f}"
    headers = {}
    if adsb_cfg.api_key:
        headers["X-API-Key"] = adsb_cfg.api_key
    resp = await client.get(url, headers=headers)
    resp.raise_for_status()
    return resp.json().get("ac", [])


async def run_adsb_sentinel(
    conn: sqlite3.Connection,
    dry_run: bool = False,
    rotator: ProxyRotator | None = None,
    target_entities: list[str] | None = None,
    target_keywords: list[str] | None = None,
    target_regions: list[str] | None = None,
) -> list[dict]:
    """Scan all configured facilities via adsb.lol; return alert dicts."""
    all_facilities = list(flight_cfg.facilities)
    if target_entities or target_keywords or target_regions:
        facilities = _filter_facilities_for_targets(
            all_facilities, target_entities, target_keywords, target_regions
        )
        logger.info(
            "[ADSB] Target filter active: %d of %d facilities selected",
            len(facilities),
            len(all_facilities),
        )
        if not facilities:
            logger.info("[ADSB] Target filter excluded all facilities — skipping")
            return []
    else:
        facilities = all_facilities

    alerts: list[dict] = []
    now = int(time.time())

    async with make_httpx_client(rotator, timeout=adsb_cfg.http_timeout) as client:
        for facility in facilities:
            name = facility["name"]
            radius_nm = facility["radius_km"] * _KM_TO_NM
            try:
                aircraft_list = await fetch_adsb_in_radius(
                    facility["lat"], facility["lon"], radius_nm, client
                )
            except Exception as exc:
                logger.warning("[ADSB] Failed to query %s: %s", name, exc)
                continue

            unusual = [
                a
                for a in aircraft_list
                if is_unusual_adsb(
                    a,
                    flight_cfg.unusual_callsign_patterns,
                    adsb_cfg.military_hex_prefixes,
                )
            ]
            logger.info(
                "[ADSB] %s: %d aircraft, %d flagged",
                name,
                len(aircraft_list),
                len(unusual),
            )

            for ac in unusual:
                hex_code = (ac.get("hex") or "UNKNOWN").strip()
                raw_callsign = (ac.get("flight") or "").strip()
                callsign_info = enrich_callsign(raw_callsign or None)
                callsign = raw_callsign or "<no-callsign>"
                lat = ac.get("lat")
                lon = ac.get("lon")
                feed_military = bool(ac.get("military"))
                military = feed_military or is_us_military_icao_hex(hex_code)
                interesting = bool(ac.get("interesting"))

                if not dry_run:
                    conn.execute(
                        """
                        INSERT OR IGNORE INTO adsb_observations
                            (hex, callsign, facility, observed_at, lat, lon,
                             military, interesting)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (hex_code, callsign, name, now, lat, lon, int(military), int(interesting)),
                    )
                    conn.commit()
                    try:
                        db.insert_callsign_observation(
                            callsign_enrichment=callsign_info.to_dict(),
                            source="adsb_sentinel",
                            facility=name,
                            icao_hex=hex_code,
                            lat=lat,
                            lon=lon,
                            observed_at=now,
                        )
                    except Exception:
                        logger.exception("[Callsign] Failed to persist callsign observation")

                alerts.append(
                    {
                        "facility": name,
                        "hex": hex_code,
                        "callsign": callsign,
                        "callsign_enrichment": callsign_info.to_dict(),
                        "military_style_callsign": callsign_info.military_style,
                        "lat": lat,
                        "lon": lon,
                        "military": military,
                        "interesting": interesting,
                    }
                )

    if alerts:
        logger.warning("[ADSB] %d unusual observations total", len(alerts))

    for alert in alerts:
        _attach_alert_score(alert)
        _attach_alert_provenance(alert)
    if not dry_run:
        db.init_alerts_table()
        for alert in alerts:
            db.insert_alert("adsb_sentinel", alert)

    out_dir = Path(adsb_cfg.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = int(time.time())
    (out_dir / f"adsb_{ts}.json").write_text(json.dumps(alerts, indent=2))

    return alerts


if __name__ == "__main__":
    import asyncio
    import logging as _logging

    from modules.data_paths import data_path
    from modules.state_db import init_db

    _logging.basicConfig(level=logging.INFO)
    _conn = init_db(data_path("state.db"))
    asyncio.run(run_adsb_sentinel(_conn, dry_run=True))
