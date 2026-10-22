"""Module A3 — high-signal correlation engine."""

import hashlib
import json
import logging
import sqlite3
import time
from datetime import UTC, datetime
from pathlib import Path

from config import correlator_cfg
from modules import db
from modules.correlation_scoring import score_correlation
from modules.correlations import CorrelationRecord
from modules.graph import GraphEdge, GraphNode
from modules.graph_builder import build_graph_from_correlation
from modules.witness import attach_witness

logger = logging.getLogger(__name__)


def _load_entities() -> list[dict]:
    path = Path(correlator_cfg.entities_path)
    if not path.exists():
        logger.warning("Entities seed missing at %s", path)
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.error("Failed loading entities seed: %s", exc)
        return []


def _parse_sec_time(sec: dict) -> datetime:
    filed_at = sec.get("filed_at") or ""
    for fmt in ("%Y-%m-%d", "%Y%m%d"):
        try:
            return datetime.strptime(filed_at, fmt).replace(tzinfo=UTC)
        except ValueError:
            continue
    return datetime.now(UTC)


def _parse_observed_time(row: dict) -> datetime | None:
    observed_at = row.get("observed_at")
    if observed_at is None:
        return None
    try:
        return datetime.fromtimestamp(int(observed_at), tz=UTC)
    except (TypeError, ValueError, OSError):
        return None


def _matches_entity(entity: dict, sec: dict) -> bool:
    company = (sec.get("company") or "").lower()
    patterns = [p.lower() for p in entity.get("assignee_patterns", [])]
    return any(p in company for p in patterns)


def _facilities_for_entity(entity: dict) -> set[str]:
    return {f.lower() for f in entity.get("facilities", [])}


def _signals_for_entity(
    entity: dict,
    sec_alerts: list[dict],
    flight_alerts: list[dict],
    adsb_alerts: list[dict],
    narrative_alerts: list[dict],
) -> list[dict]:
    facilities = _facilities_for_entity(entity)
    sec = [s for s in sec_alerts if _matches_entity(entity, s)]
    flight = [f for f in flight_alerts if (f.get("facility") or "").lower() in facilities]
    adsb = [a for a in adsb_alerts if (a.get("facility") or "").lower() in facilities]
    narrative = []
    needles = [
        entity.get("name", "").lower(),
        *[p.lower() for p in entity.get("assignee_patterns", [])],
    ]
    for n in narrative_alerts:
        blob = " ".join(
            [str(n.get("agency", "")), str(n.get("summary", "")), str(n.get("url", ""))]
        ).lower()
        if any(k and k in blob for k in needles):
            narrative.append(n)
    return [
        {"kind": "sec", "rows": sec},
        {"kind": "flight", "rows": flight},
        {"kind": "adsb", "rows": adsb},
        {"kind": "narrative", "rows": narrative},
    ]


def _score_event(sec: dict, flight: list[dict], adsb: list[dict], narrative: list[dict]) -> float:
    score = 1.0
    if sec:
        score += 4.0
    if flight:
        score += 1.5
    if adsb:
        score += 1.5
        score += min(1.0, 0.5 * sum(1 for a in adsb if a.get("military") or a.get("interesting")))
    if flight and adsb:
        score += 1.0
    if narrative:
        score += 1.0
    return round(min(score, 10.0), 1)


def _event_id(entity: str, sec: dict, signal_fingerprints: list[str]) -> str:
    filed_at = sec.get("filed_at") or ""
    accession = sec.get("accession_no") or ""
    raw = f"{entity}|{accession}|{filed_at}|{'|'.join(sorted(signal_fingerprints))}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _verification_fields(signals: list[str], flight: list[dict], adsb: list[dict]) -> dict:
    """Compute deception-filter provenance fields from active detector signals."""
    num_detectors = len(signals)
    is_single_source = num_detectors == 1

    # Cross-domain rule: physical signal (flight/adsb) alongside sec = at least corroborated
    has_physical = bool(flight or adsb)

    if num_detectors == 1:
        status = "single_source"
        trust = 0.3
    elif num_detectors == 2:
        status = "corroborated" if has_physical else "corroborated"
        trust = 0.6
    elif num_detectors == 3:
        status = "corroborated"
        trust = 0.8
    else:
        status = "multi_detector"
        trust = 1.0

    return {
        "verification_status": status,
        "source_trust_score": trust,
        "is_single_source": is_single_source,
    }


def _build_temporal_context_for_callsign(callsign: str) -> dict | None:
    """Fetch temporal context for a callsign (read-only, never raises)."""
    try:
        from modules.temporal_analysis import callsign_recurrence_metrics

        history = db.fetch_callsign_activity(callsign, limit=200)
        if not history:
            return None
        metrics = callsign_recurrence_metrics(history)
        return {
            "prior_observations": metrics["total_observations"],
            "first_seen": metrics["first_seen"],
            "last_seen": metrics["last_seen"],
            "active_days": metrics["active_days"],
            "most_common_facility": metrics["most_common_facility"],
        }
    except Exception as exc:
        logger.debug("Temporal context fetch failed for callsign %s: %s", callsign, exc)
        return None


def _enrich_temporal_context(events: list[dict]) -> list[dict]:
    """
    Optionally add temporal_context to correlation evidence for known callsigns.

    Read-only. Informational only — does not affect scoring, thresholds, or publishing gate.
    Never raises; failures are logged at DEBUG level and the event is returned unchanged.
    """
    enriched: list[dict] = []
    for event in events:
        try:
            callsigns: set[str] = set()
            for r in event.get("evidence", {}).get("flight", []):
                cs = r.get("callsign")
                if cs:
                    callsigns.add(cs)
            for r in event.get("evidence", {}).get("adsb", []):
                cs = r.get("callsign")
                if cs:
                    callsigns.add(cs)

            if callsigns:
                temporal_ctx: dict[str, dict] = {}
                for cs in sorted(callsigns):
                    ctx = _build_temporal_context_for_callsign(cs)
                    if ctx:
                        temporal_ctx[cs] = ctx
                if temporal_ctx:
                    evidence = {**event.get("evidence", {}), "temporal_context": temporal_ctx}
                    event = {**event, "evidence": evidence}
        except Exception as exc:
            logger.debug("Temporal enrichment failed for event %s: %s", event.get("event_id"), exc)
        enriched.append(event)
    return enriched


def _build_tempo_context_for_callsign(callsign: str) -> dict | None:
    """Fetch operational tempo context for a callsign (read-only, never raises)."""
    try:
        from modules.tempo_analysis import callsign_operational_tempo

        history = db.fetch_callsign_activity(callsign, limit=200)
        if not history:
            return None
        tempo = callsign_operational_tempo(history)
        return {
            "avg_interval_hours": tempo["avg_interval_hours"],
            "active_days": tempo["active_days"],
            "recurring_facilities": tempo["recurring_facilities"],
        }
    except Exception as exc:
        logger.debug("Tempo context fetch failed for callsign %s: %s", callsign, exc)
        return None


def _enrich_tempo_context(events: list[dict]) -> list[dict]:
    """
    Optionally add tempo_context to correlation evidence for known callsigns.

    Read-only. Informational only — does not affect scoring, thresholds, or publishing gate.
    Never raises; failures are logged at DEBUG level and the event is returned unchanged.
    """
    enriched: list[dict] = []
    for event in events:
        try:
            callsigns: set[str] = set()
            for r in event.get("evidence", {}).get("flight", []):
                cs = r.get("callsign")
                if cs:
                    callsigns.add(cs)
            for r in event.get("evidence", {}).get("adsb", []):
                cs = r.get("callsign")
                if cs:
                    callsigns.add(cs)

            if callsigns:
                tempo_ctx: dict[str, dict] = {}
                for cs in sorted(callsigns):
                    ctx = _build_tempo_context_for_callsign(cs)
                    if ctx:
                        tempo_ctx[cs] = ctx
                if tempo_ctx:
                    evidence = {**event.get("evidence", {}), "tempo_context": tempo_ctx}
                    event = {**event, "evidence": evidence}
        except Exception as exc:
            logger.debug("Tempo enrichment failed for event %s: %s", event.get("event_id"), exc)
        enriched.append(event)
    return enriched


def _apply_deception_filter(events: list[dict]) -> list[dict]:
    """Tag-and-log pass — does not drop events, only warns on single-source."""
    for event in events:
        if event.get("is_single_source"):
            logger.warning(
                "[DeceptionFilter] single-source event %s flagged '%s' — requires cross-domain "
                "corroboration before Nostr publishing",
                event["event_id"],
                event.get("verification_status"),
            )
    return events


def _attach_witness_survival(event: dict) -> dict:
    """Attach witness data, falling back to unsigned hash if signing path fails."""
    try:
        return attach_witness(event)
    except Exception as exc:
        logger.warning(
            "[SurvivalMode] witness attach failed for event %s: %s",
            event.get("event_id"),
            exc,
        )
        seed = event.get("event_id") or json.dumps(event, sort_keys=True)
        fallback_hash = hashlib.sha256(seed.encode("utf-8")).hexdigest()
        return {
            **event,
            "witness_hash": fallback_hash,
            "witness_signature": None,
        }


def _build_event(
    entity: dict, sec: dict, flight: list[dict], adsb: list[dict], narrative: list[dict]
) -> dict:
    signals: list[str] = ["sec"]
    if flight:
        signals.append("flight")
    if adsb:
        signals.append("adsb")
    if narrative:
        signals.append("narrative")

    evidence = {
        "sec": sec,
        "flight": flight,
        "adsb": adsb,
        "narrative": narrative,
    }
    fingerprints = [f"f:{r.get('icao24', '')}:{r.get('callsign', '')}" for r in flight]
    fingerprints += [f"a:{r.get('hex', '')}:{r.get('callsign', '')}" for r in adsb]
    fingerprints += [f"n:{r.get('url', '')}" for r in narrative]
    vfields = _verification_fields(signals, flight, adsb)
    legacy_score = _score_event(sec, flight, adsb, narrative)
    score = score_correlation(
        signals=signals,
        evidence=evidence,
        base_score=legacy_score,
    )
    return {
        "event_id": _event_id(entity["name"], sec, fingerprints),
        "event_type": (
            f"sec_{'_'.join(sorted(set(signals[1:])))}" if len(signals) > 1 else "sec_only"
        ),
        "entities": [entity["name"]],
        "score": score,
        "signals": sorted(set(signals)),
        "evidence": evidence,
        "sec_filed_at": sec.get("filed_at"),
        "observed_at": int(time.time()),
        **vfields,
    }


def correlate(
    sec_alerts: list[dict],
    narrative_alerts: list[dict],
    flight_alerts: list[dict],
    adsb_alerts: list[dict],
    score_threshold: float = 6.0,
) -> list[dict]:
    events: list[dict] = []
    entities = _load_entities()
    for entity in entities:
        grouped = _signals_for_entity(
            entity, sec_alerts, flight_alerts, adsb_alerts, narrative_alerts
        )
        sec_rows = next(g["rows"] for g in grouped if g["kind"] == "sec")
        if not sec_rows:
            continue
        flight_rows = next(g["rows"] for g in grouped if g["kind"] == "flight")
        adsb_rows = next(g["rows"] for g in grouped if g["kind"] == "adsb")
        narrative_rows = next(g["rows"] for g in grouped if g["kind"] == "narrative")
        for sec in sec_rows:
            sec_time = _parse_sec_time(sec)
            window_hours = correlator_cfg.window_flight_sec_hours
            near_flight: list[dict] = []
            for flight in flight_rows:
                observed = _parse_observed_time(flight)
                in_window = observed is None or (
                    abs((observed - sec_time).total_seconds()) <= window_hours * 3600
                )
                if in_window:
                    near_flight.append(flight)
            near_adsb: list[dict] = []
            for adsb in adsb_rows:
                observed = _parse_observed_time(adsb)
                in_window = observed is None or (
                    abs((observed - sec_time).total_seconds()) <= window_hours * 3600
                )
                if in_window:
                    near_adsb.append(adsb)
            event = _build_event(entity, sec, near_flight, near_adsb, narrative_rows)
            if event["score"] >= score_threshold:
                events.append(event)
    return events


def _event_to_correlation_record(event: dict) -> CorrelationRecord:
    entities = event.get("entities") or []
    entity = entities[0] if entities else ""
    observed_at = event.get("observed_at")
    return CorrelationRecord(
        event_id=event["event_id"],
        event_type=event["event_type"],
        entity=entity,
        score=event["score"],
        signals=event.get("signals") or [],
        evidence=event.get("evidence") or {},
        observed_at=str(observed_at) if observed_at is not None else None,
        verification_status=event.get("verification_status", "unverified"),
        source_trust_score=float(event.get("source_trust_score", 0.0)),
        is_single_source=bool(event.get("is_single_source", False)),
        witness_hash=event.get("witness_hash"),
        witness_signature=event.get("witness_signature"),
    )


def _persist_graph_for_event(event: dict) -> None:
    """Build and upsert graph nodes/edges; never raises."""
    event_id = event.get("event_id", "")
    try:
        record = _event_to_correlation_record(event)
        graph = build_graph_from_correlation(record)
        for node_data in graph["nodes"]:
            db.upsert_graph_node(GraphNode.from_dict(node_data))
        for edge_data in graph["edges"]:
            db.upsert_graph_edge(GraphEdge.from_dict(edge_data))
        logger.info(
            "[Graph] Persisted graph for correlation %s: %d nodes, %d edges",
            event_id,
            len(graph["nodes"]),
            len(graph["edges"]),
        )
    except Exception:
        logger.exception("[Graph] Failed to persist graph for correlation %s", event_id)


async def run_correlator(
    conn: sqlite3.Connection,
    *,
    sec_alerts: list[dict],
    narrative_alerts: list[dict],
    flight_alerts: list[dict],
    adsb_alerts: list[dict],
    dry_run: bool = False,
) -> list[dict]:
    events = correlate(sec_alerts, narrative_alerts, flight_alerts, adsb_alerts)
    events = _apply_deception_filter(events)
    events = _enrich_temporal_context(events)
    events = _enrich_tempo_context(events)
    inserted = 0
    if not dry_run:
        db.init_correlation_table()
        db.init_graph_tables()
        for event in events:
            event = _attach_witness_survival(event)
            if db.insert_correlation(
                event_id=event["event_id"],
                event_type=event["event_type"],
                entity=event["entities"][0],
                entities=event["entities"],
                signals=event["signals"],
                evidence=event["evidence"],
                score=event["score"],
                sec_filed_at=event["sec_filed_at"],
                observed_at=event["observed_at"],
                verification_status=event.get("verification_status", "unverified"),
                source_trust_score=event.get("source_trust_score", 0.0),
                is_single_source=event.get("is_single_source", False),
                probation_expires_at=int(time.time()) + 1800,
                witness_hash=event.get("witness_hash"),
                witness_signature=event.get("witness_signature"),
            ):
                inserted += 1
                _persist_graph_for_event(event)
    logger.info("[Correlator] events=%d inserted=%d dry_run=%s", len(events), inserted, dry_run)
    if events:
        out_dir = Path(correlator_cfg.output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"correlations_{int(time.time())}.json"
        out_path.write_text(json.dumps(events, indent=2), encoding="utf-8")
    return events
