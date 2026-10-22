"""Unit tests for modules/temporal_analysis.py"""

import time

from modules.temporal_analysis import (
    callsign_activity_windows,
    callsign_recent_activity,
    callsign_recurrence_metrics,
    facility_activity_summary,
    normalize_event_timestamp,
    sort_events_chronologically,
)

# ---------------------------------------------------------------------------
# Fixed timestamps for deterministic testing
# ---------------------------------------------------------------------------
# 2026-01-01 00:00:00 UTC
_T0 = 1_767_225_600
# 2026-01-02 00:00:00 UTC  (+1 day)
_T1 = _T0 + 86_400
# 2026-01-05 00:00:00 UTC  (+4 days)
_T4 = _T0 + 4 * 86_400
# 2026-01-10 00:00:00 UTC  (+9 days)
_T9 = _T0 + 9 * 86_400


def _obs(callsign="PAT16", facility="DARPA HQ", source="adsb_sentinel", observed_at=_T0):
    return {
        "normalized_callsign": callsign,
        "source": source,
        "facility": facility,
        "observed_at": observed_at,
        "tags": [],
        "enrichment": {},
    }


# ---------------------------------------------------------------------------
# callsign_recurrence_metrics
# ---------------------------------------------------------------------------


def test_recurrence_empty_history():
    result = callsign_recurrence_metrics([])
    assert result["callsign"] is None
    assert result["total_observations"] == 0
    assert result["first_seen"] is None
    assert result["last_seen"] is None
    assert result["active_days"] == 0
    assert result["avg_per_day"] == 0.0
    assert result["facilities_seen"] == 0
    assert result["signal_types_seen"] == []
    assert result["most_common_facility"] is None
    assert result["most_common_signal"] is None


def test_recurrence_single_observation():
    result = callsign_recurrence_metrics([_obs(observed_at=_T0)])
    assert result["callsign"] == "PAT16"
    assert result["total_observations"] == 1
    assert result["first_seen"] is not None
    assert result["last_seen"] == result["first_seen"]
    assert result["active_days"] == 1
    assert result["avg_per_day"] == 1.0
    assert result["facilities_seen"] == 1
    assert result["signal_types_seen"] == ["adsb_sentinel"]
    assert result["most_common_facility"] == "DARPA HQ"
    assert result["most_common_signal"] == "adsb_sentinel"


def test_recurrence_multiple_days():
    history = [
        _obs(observed_at=_T0),
        _obs(observed_at=_T1),
        _obs(observed_at=_T4),
        _obs(facility="Pentagon", observed_at=_T9),
        _obs(source="flight_tracker", observed_at=_T9),
    ]
    result = callsign_recurrence_metrics(history)
    assert result["total_observations"] == 5
    assert result["active_days"] == 4  # day 0, 1, 4, 9
    assert result["facilities_seen"] == 2  # "DARPA HQ", "Pentagon"
    assert "adsb_sentinel" in result["signal_types_seen"]
    assert "flight_tracker" in result["signal_types_seen"]
    assert result["most_common_facility"] == "DARPA HQ"  # 4 vs 1
    assert result["avg_per_day"] > 0.0


def test_recurrence_malformed_timestamp():
    history = [
        _obs(observed_at=_T0),
        {**_obs(observed_at=_T1), "observed_at": "not-a-number"},
        _obs(observed_at=_T4),
    ]
    result = callsign_recurrence_metrics(history)
    # Malformed row contributes to total_observations but not to timestamps
    assert result["total_observations"] == 3
    assert result["active_days"] == 2  # only _T0 and _T4 parse
    assert result["first_seen"] is not None


def test_recurrence_no_facility_in_rows():
    history = [
        {**_obs(observed_at=_T0), "facility": None},
        {**_obs(observed_at=_T1), "facility": ""},
    ]
    result = callsign_recurrence_metrics(history)
    assert result["facilities_seen"] == 0
    assert result["most_common_facility"] is None


# ---------------------------------------------------------------------------
# callsign_recent_activity
# ---------------------------------------------------------------------------


def test_recent_activity_empty():
    result = callsign_recent_activity([])
    assert result["recent_observations"] == 0
    assert result["recent_facilities"] == []
    assert result["recent_signals"] == []
    assert result["window_days"] == 7


def test_recent_activity_all_old():
    now = int(time.time())
    old = now - 30 * 86_400
    result = callsign_recent_activity([_obs(observed_at=old)], window_days=7)
    assert result["recent_observations"] == 0


def test_recent_activity_mixed():
    now = int(time.time())
    recent_ts = now - 2 * 86_400
    old_ts = now - 10 * 86_400
    history = [
        _obs(observed_at=recent_ts, facility="DARPA HQ", source="adsb_sentinel"),
        _obs(observed_at=old_ts, facility="Pentagon", source="flight_tracker"),
    ]
    result = callsign_recent_activity(history, window_days=7)
    assert result["recent_observations"] == 1
    assert "DARPA HQ" in result["recent_facilities"]
    assert "adsb_sentinel" in result["recent_signals"]


def test_recent_activity_malformed_timestamp():
    history = [
        {**_obs(observed_at=_T0), "observed_at": "bad"},
    ]
    result = callsign_recent_activity(history, window_days=9999)
    # Malformed timestamps are skipped; count = 0
    assert result["recent_observations"] == 0


def test_recent_activity_custom_window():
    now = int(time.time())
    recent_3d = now - 2 * 86_400
    recent_8d = now - 8 * 86_400
    history = [
        _obs(observed_at=recent_3d),
        _obs(observed_at=recent_8d),
    ]
    assert callsign_recent_activity(history, window_days=7)["recent_observations"] == 1
    assert callsign_recent_activity(history, window_days=14)["recent_observations"] == 2


# ---------------------------------------------------------------------------
# facility_activity_summary
# ---------------------------------------------------------------------------


def test_facility_summary_empty():
    assert facility_activity_summary([]) == []


def test_facility_summary_single_facility():
    history = [_obs(observed_at=_T0), _obs(observed_at=_T1)]
    result = facility_activity_summary(history)
    assert len(result) == 1
    assert result[0]["facility"] == "DARPA HQ"
    assert result[0]["total_observations"] == 2
    assert result[0]["unique_callsigns"] == 1
    assert result[0]["most_active_callsign"] == "PAT16"
    assert result[0]["recurring_callsigns"] == ["PAT16"]  # seen twice


def test_facility_summary_multiple_facilities():
    observations = [
        _obs(facility="DARPA HQ", callsign="PAT16", observed_at=_T0),
        _obs(facility="DARPA HQ", callsign="PAT16", observed_at=_T1),
        _obs(facility="DARPA HQ", callsign="RCH123", observed_at=_T4),
        _obs(facility="Pentagon", callsign="MUSL1", observed_at=_T9),
    ]
    result = facility_activity_summary(observations)
    assert result[0]["facility"] == "DARPA HQ"  # more observations
    assert result[0]["total_observations"] == 3
    assert result[1]["facility"] == "Pentagon"
    assert result[1]["total_observations"] == 1


def test_facility_summary_no_facility_grouped_as_none():
    observations = [
        {**_obs(observed_at=_T0), "facility": None},
    ]
    result = facility_activity_summary(observations)
    assert result[0]["facility"] is None


def test_facility_summary_recurring_vs_one_time():
    observations = [
        _obs(callsign="PAT16", facility="DARPA HQ", observed_at=_T0),
        _obs(callsign="PAT16", facility="DARPA HQ", observed_at=_T1),
        _obs(callsign="RCH1", facility="DARPA HQ", observed_at=_T4),
    ]
    result = facility_activity_summary(observations)
    assert "PAT16" in result[0]["recurring_callsigns"]
    assert "RCH1" not in result[0]["recurring_callsigns"]


def test_facility_summary_sorted_by_count():
    observations = [
        _obs(facility="A", observed_at=_T0),
        _obs(facility="B", observed_at=_T1),
        _obs(facility="B", observed_at=_T4),
        _obs(facility="B", observed_at=_T9),
    ]
    result = facility_activity_summary(observations)
    assert result[0]["facility"] == "B"
    assert result[1]["facility"] == "A"


# ---------------------------------------------------------------------------
# callsign_activity_windows
# ---------------------------------------------------------------------------


def test_activity_windows_empty():
    assert callsign_activity_windows([]) == []


def test_activity_windows_single_day():
    history = [_obs(observed_at=_T0), _obs(callsign="PAT17", observed_at=_T0 + 3600)]
    windows = callsign_activity_windows(history)
    assert len(windows) == 1
    assert windows[0]["count"] == 2
    assert "PAT16" in windows[0]["callsigns"]
    assert "PAT17" in windows[0]["callsigns"]


def test_activity_windows_multiple_days_sorted():
    history = [
        _obs(observed_at=_T9),
        _obs(observed_at=_T0),
        _obs(observed_at=_T4),
    ]
    windows = callsign_activity_windows(history)
    assert len(windows) == 3
    # Chronological order
    assert windows[0]["date"] < windows[1]["date"] < windows[2]["date"]


def test_activity_windows_skips_malformed_timestamps():
    history = [
        {**_obs(observed_at=_T0), "observed_at": "bad"},
        _obs(observed_at=_T1),
    ]
    windows = callsign_activity_windows(history)
    assert len(windows) == 1
    assert windows[0]["count"] == 1


def test_activity_windows_fields():
    history = [
        _obs(callsign="PAT16", facility="DARPA HQ", source="adsb_sentinel", observed_at=_T0),
    ]
    windows = callsign_activity_windows(history)
    w = windows[0]
    assert "date" in w
    assert w["count"] == 1
    assert "PAT16" in w["callsigns"]
    assert "DARPA HQ" in w["facilities"]
    assert "adsb_sentinel" in w["signals"]


# ---------------------------------------------------------------------------
# Graph preparation helpers
# ---------------------------------------------------------------------------


def test_normalize_event_timestamp_unix_int():
    event = {"detected_at": _T0}
    ts = normalize_event_timestamp(event)
    assert ts is not None
    assert "2026" in ts


def test_normalize_event_timestamp_iso_string():
    event = {"detected_at": "2026-01-15T12:00:00+00:00"}
    ts = normalize_event_timestamp(event)
    assert ts is not None
    assert "2026-01-15" in ts


def test_normalize_event_timestamp_missing():
    assert normalize_event_timestamp({}) is None


def test_normalize_event_timestamp_bad_value():
    assert normalize_event_timestamp({"detected_at": "not-a-date"}) is None


def test_normalize_event_timestamp_custom_field():
    event = {"observed_at": _T0}
    ts = normalize_event_timestamp(event, ts_field="observed_at")
    assert ts is not None


def test_sort_events_chronologically():
    events = [
        {"detected_at": "2026-01-10T00:00:00+00:00", "id": "c"},
        {"detected_at": "2026-01-01T00:00:00+00:00", "id": "a"},
        {"detected_at": "2026-01-05T00:00:00+00:00", "id": "b"},
    ]
    sorted_events = sort_events_chronologically(events)
    assert [e["id"] for e in sorted_events] == ["a", "b", "c"]


def test_sort_events_chronologically_reverse():
    events = [
        {"detected_at": "2026-01-01T00:00:00+00:00", "id": "a"},
        {"detected_at": "2026-01-10T00:00:00+00:00", "id": "c"},
    ]
    sorted_events = sort_events_chronologically(events, reverse=True)
    assert sorted_events[0]["id"] == "c"


def test_sort_events_chronologically_missing_timestamps_last():
    events = [
        {"detected_at": "2026-01-01T00:00:00+00:00", "id": "valid"},
        {"id": "no_ts"},
    ]
    sorted_events = sort_events_chronologically(events)
    assert sorted_events[0]["id"] == "valid"
    assert sorted_events[-1]["id"] == "no_ts"


def test_sort_events_chronologically_empty():
    assert sort_events_chronologically([]) == []
