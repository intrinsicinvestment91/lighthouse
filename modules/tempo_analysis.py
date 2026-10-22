"""Operational tempo and mission recurrence analysis for callsign behavior."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import UTC, datetime
from typing import Any

_WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


def _parse_ts(ts: Any) -> datetime | None:
    if ts is None:
        return None
    try:
        return datetime.fromtimestamp(int(ts), tz=UTC)
    except (TypeError, ValueError, OSError):
        return None


def _weekday_name(dt: datetime) -> str:
    return _WEEKDAYS[dt.weekday()]


def _hour_key(dt: datetime) -> str:
    return f"{dt.hour:02d}"


def callsign_operational_tempo(history: list[dict]) -> dict:
    """
    Compute operational tempo metrics from callsign observation history.

    Returns interval statistics, weekday/hour distributions, and recurring facilities.
    Deterministic — input rows must be callsign_observations dicts.
    """
    callsign = history[0].get("normalized_callsign") if history else None

    if not history:
        return {
            "callsign": callsign,
            "total_observations": 0,
            "active_days": 0,
            "avg_interval_hours": None,
            "shortest_interval_hours": None,
            "longest_interval_hours": None,
            "weekday_distribution": {},
            "hour_distribution": {},
            "recurring_facilities": [],
        }

    timestamps: list[datetime] = []
    weekday_counts: Counter[str] = Counter()
    hour_counts: Counter[str] = Counter()
    facility_counts: Counter[str] = Counter()

    for row in history:
        dt = _parse_ts(row.get("observed_at"))
        if dt is not None:
            timestamps.append(dt)
            weekday_counts[_weekday_name(dt)] += 1
            hour_counts[_hour_key(dt)] += 1
        facility = row.get("facility")
        if facility:
            facility_counts[facility] += 1

    timestamps.sort()
    active_days = len({dt.strftime("%Y-%m-%d") for dt in timestamps})

    avg_interval: float | None = None
    shortest: float | None = None
    longest: float | None = None
    if len(timestamps) >= 2:
        intervals_hours = [
            (timestamps[i + 1] - timestamps[i]).total_seconds() / 3600
            for i in range(len(timestamps) - 1)
        ]
        avg_interval = round(sum(intervals_hours) / len(intervals_hours), 2)
        shortest = round(min(intervals_hours), 2)
        longest = round(max(intervals_hours), 2)

    recurring_facilities = sorted(f for f, c in facility_counts.items() if c > 1)
    weekday_dist = {d: weekday_counts[d] for d in _WEEKDAYS if weekday_counts[d] > 0}
    hour_dist = dict(sorted(hour_counts.items()))

    return {
        "callsign": callsign,
        "total_observations": len(history),
        "active_days": active_days,
        "avg_interval_hours": avg_interval,
        "shortest_interval_hours": shortest,
        "longest_interval_hours": longest,
        "weekday_distribution": weekday_dist,
        "hour_distribution": hour_dist,
        "recurring_facilities": recurring_facilities,
    }


def facility_operational_tempo(observations: list[dict]) -> dict:
    """
    Aggregate callsign observations for a facility into an operational tempo summary.

    Returns cadence metrics: most active callsigns, busiest weekdays/hours, concentration ratio.
    """
    if not observations:
        return {
            "facility": None,
            "total_observations": 0,
            "unique_callsigns": 0,
            "recurring_callsigns": [],
            "most_active_callsign": None,
            "busiest_weekdays": [],
            "busiest_hours": [],
            "recurrence_concentration": 0.0,
        }

    facility = observations[0].get("facility")
    callsign_counts: Counter[str] = Counter()
    weekday_counts: Counter[str] = Counter()
    hour_counts: Counter[str] = Counter()

    for row in observations:
        cs = row.get("normalized_callsign")
        if cs:
            callsign_counts[cs] += 1
        dt = _parse_ts(row.get("observed_at"))
        if dt is not None:
            weekday_counts[_weekday_name(dt)] += 1
            hour_counts[_hour_key(dt)] += 1

    unique = len(callsign_counts)
    recurring = sorted(cs for cs, c in callsign_counts.items() if c > 1)
    most_active = callsign_counts.most_common(1)[0][0] if callsign_counts else None
    concentration = round(len(recurring) / unique, 2) if unique > 0 else 0.0

    busiest_weekdays = [(d, weekday_counts[d]) for d in _WEEKDAYS if weekday_counts[d] > 0]
    busiest_hours = sorted(hour_counts.items())

    return {
        "facility": facility,
        "total_observations": len(observations),
        "unique_callsigns": unique,
        "recurring_callsigns": recurring,
        "most_active_callsign": most_active,
        "busiest_weekdays": busiest_weekdays,
        "busiest_hours": busiest_hours,
        "recurrence_concentration": concentration,
    }


def identify_recurring_callsigns(
    observations: list[dict],
    *,
    min_observations: int = 5,
    min_days: int = 3,
    min_span_days: int = 0,
) -> list[dict]:
    """
    Return operational tempo for callsigns exceeding recurrence thresholds.

    Filters by minimum observations, active days, and optional minimum span in days.
    Results are sorted by total_observations descending, then callsign for stability.
    """
    by_callsign: dict[str, list[dict]] = defaultdict(list)
    for row in observations:
        cs = row.get("normalized_callsign")
        if cs:
            by_callsign[cs].append(row)

    results: list[dict] = []
    for cs in sorted(by_callsign.keys()):
        history = by_callsign[cs]
        if len(history) < min_observations:
            continue

        valid_ts = [_parse_ts(r.get("observed_at")) for r in history]
        valid_ts = [t for t in valid_ts if t is not None]
        if not valid_ts:
            continue

        active_days = len({t.strftime("%Y-%m-%d") for t in valid_ts})
        if active_days < min_days:
            continue

        if min_span_days > 0:
            span_days = (max(valid_ts) - min(valid_ts)).total_seconds() / 86400
            if span_days < min_span_days:
                continue

        results.append(callsign_operational_tempo(history))

    results.sort(key=lambda t: (-t["total_observations"], t["callsign"] or ""))
    return results


def callsign_temporal_clusters(history: list[dict]) -> dict:
    """
    Build deterministic temporal clusters from callsign observation history.

    Returns:
    - same_day: dates with multiple observations
    - same_hour: hour-of-day values (HH) appearing more than once across all observations
    - weekday_pattern: count per weekday (canonical order)
    - weekly_windows: ISO week keys (YYYY-WNN) with observation counts
    """
    if not history:
        return {
            "same_day": [],
            "same_hour": [],
            "weekday_pattern": {},
            "weekly_windows": [],
        }

    day_counts: Counter[str] = Counter()
    hour_counts: Counter[str] = Counter()
    weekday_counts: Counter[str] = Counter()
    week_counts: Counter[str] = Counter()

    for row in history:
        dt = _parse_ts(row.get("observed_at"))
        if dt is None:
            continue
        day_counts[dt.strftime("%Y-%m-%d")] += 1
        hour_counts[_hour_key(dt)] += 1
        weekday_counts[_weekday_name(dt)] += 1
        week_counts[dt.strftime("%G-W%V")] += 1

    same_day = [{"date": d, "count": c} for d, c in sorted(day_counts.items()) if c > 1]
    same_hour = [{"hour": h, "count": c} for h, c in sorted(hour_counts.items()) if c > 1]
    weekday_pattern = {d: weekday_counts[d] for d in _WEEKDAYS if weekday_counts[d] > 0}
    weekly_windows = [{"week": w, "count": c} for w, c in sorted(week_counts.items())]

    return {
        "same_day": same_day,
        "same_hour": same_hour,
        "weekday_pattern": weekday_pattern,
        "weekly_windows": weekly_windows,
    }
