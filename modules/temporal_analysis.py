"""Deterministic temporal analysis helpers for callsign and facility activity."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import UTC, datetime
from typing import Any


def _parse_timestamp(ts: Any) -> datetime | None:
    """Parse observed_at (Unix int) to UTC datetime."""
    if ts is None:
        return None
    try:
        return datetime.fromtimestamp(int(ts), tz=UTC)
    except (TypeError, ValueError, OSError):
        return None


def _day_key(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d")


def callsign_recurrence_metrics(history: list[dict]) -> dict:
    """
    Compute deterministic recurrence metrics from callsign observation history.

    Input: list of callsign_observations rows.
    Returns total_observations, first/last seen, active_days, avg_per_day,
    facilities_seen, signal_types_seen, most_common_facility/signal.
    """
    if not history:
        return {
            "callsign": None,
            "total_observations": 0,
            "first_seen": None,
            "last_seen": None,
            "active_days": 0,
            "avg_per_day": 0.0,
            "facilities_seen": 0,
            "signal_types_seen": [],
            "most_common_facility": None,
            "most_common_signal": None,
        }

    callsign = history[0].get("normalized_callsign")
    total = len(history)
    timestamps: list[datetime] = []
    facilities: list[str] = []
    signals: list[str] = []

    for row in history:
        dt = _parse_timestamp(row.get("observed_at"))
        if dt is not None:
            timestamps.append(dt)
        facility = row.get("facility")
        if facility:
            facilities.append(facility)
        source = row.get("source")
        if source:
            signals.append(source)

    if timestamps:
        first_dt = min(timestamps)
        last_dt = max(timestamps)
        first_seen = first_dt.isoformat()
        last_seen = last_dt.isoformat()
        active_days = len({_day_key(dt) for dt in timestamps})
        span_seconds = (last_dt - first_dt).total_seconds()
        if span_seconds > 0:
            avg_per_day = round(total / (span_seconds / 86400), 2)
        else:
            avg_per_day = float(total)
    else:
        first_seen = None
        last_seen = None
        active_days = 0
        avg_per_day = 0.0

    facility_counter: Counter[str] = Counter(facilities)
    signal_counter: Counter[str] = Counter(signals)

    return {
        "callsign": callsign,
        "total_observations": total,
        "first_seen": first_seen,
        "last_seen": last_seen,
        "active_days": active_days,
        "avg_per_day": avg_per_day,
        "facilities_seen": len(set(facilities)),
        "signal_types_seen": sorted(set(signals)),
        "most_common_facility": facility_counter.most_common(1)[0][0] if facility_counter else None,
        "most_common_signal": signal_counter.most_common(1)[0][0] if signal_counter else None,
    }


def callsign_recent_activity(history: list[dict], window_days: int = 7) -> dict:
    """
    Return observations falling within a recent time window.

    Input: list of callsign_observations rows.
    Returns recent_observations count, recent_facilities, recent_signals.
    """
    if not history:
        return {
            "recent_observations": 0,
            "recent_facilities": [],
            "recent_signals": [],
            "window_days": window_days,
        }

    cutoff_ts = datetime.now(UTC).timestamp() - (window_days * 86400)
    recent: list[dict] = []
    for row in history:
        observed_at = row.get("observed_at")
        if observed_at is not None:
            try:
                if int(observed_at) >= cutoff_ts:
                    recent.append(row)
            except (TypeError, ValueError):
                pass

    facilities = sorted({r.get("facility") for r in recent if r.get("facility")})
    signals = sorted({r.get("source") for r in recent if r.get("source")})

    return {
        "recent_observations": len(recent),
        "recent_facilities": facilities,
        "recent_signals": signals,
        "window_days": window_days,
    }


def facility_activity_summary(observations: list[dict]) -> list[dict]:
    """
    Aggregate observations by facility.

    Input: list of callsign_observations rows (any callsigns/facilities).
    Returns list of facility summary dicts sorted by total_observations descending.
    """
    if not observations:
        return []

    by_facility: dict[str, list[dict]] = defaultdict(list)
    for row in observations:
        facility = row.get("facility") or "_unknown_"
        by_facility[facility].append(row)

    summaries: list[dict] = []
    for facility, rows in by_facility.items():
        callsigns = [r.get("normalized_callsign") for r in rows if r.get("normalized_callsign")]
        callsign_counter: Counter[str] = Counter(callsigns)

        timestamps = [_parse_timestamp(r.get("observed_at")) for r in rows]
        valid_ts = [dt for dt in timestamps if dt is not None]

        first_seen = min(valid_ts).isoformat() if valid_ts else None
        last_seen = max(valid_ts).isoformat() if valid_ts else None
        recurring = sorted(cs for cs, count in callsign_counter.items() if count > 1)
        most_active = callsign_counter.most_common(1)[0][0] if callsign_counter else None

        summaries.append(
            {
                "facility": facility if facility != "_unknown_" else None,
                "total_observations": len(rows),
                "unique_callsigns": len(set(callsigns)),
                "first_seen": first_seen,
                "last_seen": last_seen,
                "recurring_callsigns": recurring,
                "most_active_callsign": most_active,
            }
        )

    summaries.sort(key=lambda s: s["total_observations"], reverse=True)
    return summaries


def callsign_activity_windows(history: list[dict]) -> list[dict]:
    """
    Group observations by calendar day, sorted chronologically.

    Input: list of callsign_observations rows.
    Returns list of {date, count, callsigns, facilities, signals} dicts.
    """
    if not history:
        return []

    by_day: dict[str, list[dict]] = defaultdict(list)
    for row in history:
        dt = _parse_timestamp(row.get("observed_at"))
        if dt is None:
            continue
        by_day[_day_key(dt)].append(row)

    windows: list[dict] = []
    for day in sorted(by_day.keys()):
        rows = by_day[day]
        callsigns = sorted(
            {r.get("normalized_callsign") for r in rows if r.get("normalized_callsign")}
        )
        facilities = sorted({r.get("facility") for r in rows if r.get("facility")})
        signals = sorted({r.get("source") for r in rows if r.get("source")})
        windows.append(
            {
                "date": day,
                "count": len(rows),
                "callsigns": callsigns,
                "facilities": facilities,
                "signals": signals,
            }
        )

    return windows


# ---------------------------------------------------------------------------
# Graph preparation helpers (Part 5)
# ---------------------------------------------------------------------------


def normalize_event_timestamp(event: dict, ts_field: str = "detected_at") -> str | None:
    """
    Return a normalized ISO-format timestamp from a correlation or alert event.

    Handles ISO strings and Unix integer/float timestamps. Returns None if absent or unparseable.
    Stable across representations — safe to use as a sort key or graph edge property.
    """
    raw = event.get(ts_field)
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        try:
            return datetime.fromtimestamp(float(raw), tz=UTC).isoformat()
        except (OSError, ValueError):
            return None
    if isinstance(raw, str):
        for fmt in (None, "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                if fmt is None:
                    dt = datetime.fromisoformat(raw)
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=UTC)
                    return dt.isoformat()
                else:
                    return datetime.strptime(raw, fmt).replace(tzinfo=UTC).isoformat()
            except (ValueError, TypeError):
                continue
    return None


def sort_events_chronologically(
    events: list[dict],
    ts_field: str = "detected_at",
    *,
    reverse: bool = False,
) -> list[dict]:
    """
    Return events sorted by timestamp, oldest first (reverse=True for newest first).

    Events with unparseable or absent timestamps sort last.
    Deterministic — events with identical timestamps retain their relative order (stable sort).
    """
    sentinel = "9999-99-99" if not reverse else ""

    def _key(event: dict) -> str:
        ts = normalize_event_timestamp(event, ts_field=ts_field)
        return ts if ts is not None else sentinel

    return sorted(events, key=_key, reverse=reverse)
