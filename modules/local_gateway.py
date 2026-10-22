"""Private local intelligence gateway — bounded read-only dispatch layer."""

from __future__ import annotations

import logging

from modules import db
from modules.tempo_analysis import (
    callsign_operational_tempo,
    callsign_temporal_clusters,
    facility_operational_tempo,
)

_log = logging.getLogger(__name__)

MAX_DEPTH = 3
MAX_LIMIT = 50

ALLOWED_METHODS: frozenset[str] = frozenset(
    {
        "callsign.summary",
        "facility.summary",
        "correlations.recent",
        "graph.summary",
        "intelligence.daily_brief",
    }
)


def _ok(method: str, result: dict) -> dict:
    return {"status": "ok", "method": method, "result": result}


def _err(method: str, message: str) -> dict:
    return {"status": "error", "method": method, "error": message}


def _strip_correlation(row: dict) -> dict:
    return {
        "id": row.get("id"),
        "entity": row.get("entity"),
        "entities": row.get("entities", []),
        "signals": row.get("signals", []),
        "score": row.get("score"),
        "detected_at": row.get("detected_at"),
        "event_type": row.get("event_type"),
    }


def _callsign_summary(params: dict) -> dict:
    callsign = params.get("callsign")
    if not callsign or not isinstance(callsign, str):
        raise ValueError("params.callsign is required (string)")
    history = db.fetch_callsign_activity(callsign, limit=500)
    return {
        "callsign": callsign,
        "metrics": callsign_operational_tempo(history),
        "clusters": callsign_temporal_clusters(history),
    }


def _facility_summary(params: dict) -> dict:
    facility = params.get("facility")
    if not facility or not isinstance(facility, str):
        raise ValueError("params.facility is required (string)")
    observations = db.fetch_facility_activity(facility, limit=500)
    return facility_operational_tempo(observations)


def _correlations_recent(params: dict) -> dict:
    limit = int(params.get("limit", 10))
    limit = max(1, min(limit, MAX_LIMIT))
    rows = db.fetch_correlations(limit=limit)
    return {"correlations": [_strip_correlation(r) for r in rows], "count": len(rows)}


def _graph_summary(params: dict) -> dict:
    entity = params.get("entity")
    if not entity or not isinstance(entity, str):
        raise ValueError("params.entity is required (string)")
    depth = max(1, min(int(params.get("depth", 2)), MAX_DEPTH))
    entity = entity.strip()

    candidates = [
        f"entity:{entity}",
        f"callsign:{entity.upper()}",
        f"facility:{entity}",
    ]
    node_id = f"entity:{entity}"
    for candidate in candidates:
        if db.fetch_graph_node(candidate):
            node_id = candidate
            break

    result = db.traverse_graph(node_id=node_id, depth=depth, limit=MAX_LIMIT)
    return {"node_id": node_id, "nodes": result["nodes"], "edges": result["edges"]}


def _intelligence_daily_brief(params: dict) -> dict:
    recurring = db.fetch_recurring_callsigns_summary(min_observations=5, min_days=3, limit=5)
    correlations = db.fetch_correlations(min_score=5.0, limit=10)
    return {
        "recurring_callsigns": recurring,
        "recent_correlations": [_strip_correlation(r) for r in correlations],
    }


_DISPATCH: dict[str, object] = {
    "callsign.summary": _callsign_summary,
    "facility.summary": _facility_summary,
    "correlations.recent": _correlations_recent,
    "graph.summary": _graph_summary,
    "intelligence.daily_brief": _intelligence_daily_brief,
}


def dispatch(method: str, params: dict) -> dict:
    if method not in ALLOWED_METHODS:
        return _err(method, f"unknown method: {method!r}")
    try:
        result = _DISPATCH[method](params)  # type: ignore[operator]
        return _ok(method, result)
    except ValueError as exc:
        return _err(method, str(exc))
    except Exception as exc:
        _log.debug("local_gateway dispatch error for %r: %s", method, exc, exc_info=True)
        return _err(method, "internal error")
