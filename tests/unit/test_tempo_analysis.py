"""Unit tests for modules/tempo_analysis.py"""

from modules.tempo_analysis import (
    callsign_operational_tempo,
    callsign_temporal_clusters,
    facility_operational_tempo,
    identify_recurring_callsigns,
)

# ---------------------------------------------------------------------------
# Fixed timestamps for deterministic testing
# ---------------------------------------------------------------------------
# 2026-01-05 00:00:00 UTC  (Monday)
_T_MON = 1_767_571_200
# 2026-01-06 01:00:00 UTC  (Tuesday, +25h)
_T_TUE = _T_MON + 25 * 3600
# 2026-01-07 01:30:00 UTC  (Wednesday, +24.5h from _T_TUE)
_T_WED = _T_MON + 2 * 86_400 + 1 * 3600 + 1800
# 2026-01-12 00:00:00 UTC  (Monday next week)
_T_MON2 = _T_MON + 7 * 86_400


def _obs(callsign="PAT16", facility="DARPA HQ", source="adsb_sentinel", observed_at=_T_MON):
    return {
        "normalized_callsign": callsign,
        "source": source,
        "facility": facility,
        "observed_at": observed_at,
    }


# ---------------------------------------------------------------------------
# callsign_operational_tempo — empty / edge cases
# ---------------------------------------------------------------------------


def test_tempo_empty_history_returns_nulls():
    result = callsign_operational_tempo([])
    assert result["callsign"] is None
    assert result["total_observations"] == 0
    assert result["active_days"] == 0
    assert result["avg_interval_hours"] is None
    assert result["shortest_interval_hours"] is None
    assert result["longest_interval_hours"] is None
    assert result["weekday_distribution"] == {}
    assert result["hour_distribution"] == {}
    assert result["recurring_facilities"] == []


def test_tempo_single_observation_no_intervals():
    result = callsign_operational_tempo([_obs(observed_at=_T_MON)])
    assert result["total_observations"] == 1
    assert result["active_days"] == 1
    assert result["avg_interval_hours"] is None
    assert result["shortest_interval_hours"] is None
    assert result["longest_interval_hours"] is None
    assert result["callsign"] == "PAT16"


def test_tempo_two_observations_exact_interval():
    # _T_TUE - _T_MON = 25 hours
    result = callsign_operational_tempo([_obs(observed_at=_T_MON), _obs(observed_at=_T_TUE)])
    assert result["avg_interval_hours"] == 25.0
    assert result["shortest_interval_hours"] == 25.0
    assert result["longest_interval_hours"] == 25.0


def test_tempo_intervals_min_max_differ():
    # _T_MON → _T_TUE = 25h, _T_TUE → _T_WED = 24.5h; use a close observation for a short gap
    close_ts = _T_MON + 1800  # 30 minutes after _T_MON
    history = [
        _obs(observed_at=_T_MON),
        _obs(observed_at=close_ts),
        _obs(observed_at=_T_TUE),
    ]
    result = callsign_operational_tempo(history)
    assert result["shortest_interval_hours"] == 0.5
    assert result["longest_interval_hours"] == 24.5
    expected_avg = round((0.5 + 24.5) / 2, 2)
    assert result["avg_interval_hours"] == expected_avg


def test_tempo_weekday_distribution_canonical_order():
    history = [
        _obs(observed_at=_T_MON),  # Monday
        _obs(observed_at=_T_TUE),  # Tuesday
        _obs(observed_at=_T_WED),  # Wednesday
    ]
    result = callsign_operational_tempo(history)
    wd = result["weekday_distribution"]
    assert "monday" in wd
    assert "tuesday" in wd
    assert "wednesday" in wd
    # Keys must appear in canonical Mon–Sun order
    keys = list(wd.keys())
    _canonical = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
    assert keys == sorted(keys, key=_canonical.index)


def test_tempo_hour_distribution_keys():
    # _T_MON = hour 00, _T_TUE = hour 01, _T_WED = hour 01
    history = [
        _obs(observed_at=_T_MON),
        _obs(observed_at=_T_TUE),
        _obs(observed_at=_T_WED),
    ]
    result = callsign_operational_tempo(history)
    hd = result["hour_distribution"]
    assert "00" in hd
    assert "01" in hd
    assert hd["01"] == 2


def test_tempo_recurring_facilities_only_when_seen_twice():
    history = [
        _obs(facility="DARPA HQ", observed_at=_T_MON),
        _obs(facility="DARPA HQ", observed_at=_T_TUE),
        _obs(facility="Langley", observed_at=_T_WED),
    ]
    result = callsign_operational_tempo(history)
    assert "DARPA HQ" in result["recurring_facilities"]
    assert "Langley" not in result["recurring_facilities"]


def test_tempo_malformed_timestamp_skipped():
    history = [
        {"normalized_callsign": "PAT16", "observed_at": "not-a-timestamp", "facility": "HQ"},
        _obs(observed_at=_T_MON),
    ]
    result = callsign_operational_tempo(history)
    assert result["total_observations"] == 2
    assert result["active_days"] == 1  # only one valid timestamp parsed
    assert result["avg_interval_hours"] is None  # only 1 valid ts


def test_tempo_deterministic_repeat_calls():
    history = [_obs(observed_at=_T_MON), _obs(observed_at=_T_TUE), _obs(observed_at=_T_WED)]
    a = callsign_operational_tempo(history)
    b = callsign_operational_tempo(history)
    assert a == b


def test_tempo_no_facility_not_in_recurring():
    history = [
        {"normalized_callsign": "PAT16", "observed_at": _T_MON, "facility": None},
        {"normalized_callsign": "PAT16", "observed_at": _T_TUE, "facility": None},
    ]
    result = callsign_operational_tempo(history)
    assert result["recurring_facilities"] == []


def test_tempo_active_days_counted_correctly():
    # 4 observations on 3 different days
    history = [
        _obs(observed_at=_T_MON),
        _obs(observed_at=_T_MON + 3600),  # same day as _T_MON
        _obs(observed_at=_T_TUE),
        _obs(observed_at=_T_WED),
    ]
    result = callsign_operational_tempo(history)
    assert result["total_observations"] == 4
    assert result["active_days"] == 3


# ---------------------------------------------------------------------------
# facility_operational_tempo
# ---------------------------------------------------------------------------


def test_facility_tempo_empty():
    result = facility_operational_tempo([])
    assert result["total_observations"] == 0
    assert result["unique_callsigns"] == 0
    assert result["recurring_callsigns"] == []
    assert result["most_active_callsign"] is None
    assert result["recurrence_concentration"] == 0.0


def test_facility_tempo_single_callsign():
    obs = [_obs(observed_at=_T_MON), _obs(observed_at=_T_TUE)]
    result = facility_operational_tempo(obs)
    assert result["total_observations"] == 2
    assert result["unique_callsigns"] == 1
    assert result["most_active_callsign"] == "PAT16"
    assert result["recurring_callsigns"] == ["PAT16"]
    assert result["recurrence_concentration"] == 1.0


def test_facility_tempo_multiple_callsigns_concentration():
    obs = [
        _obs(callsign="PAT16", observed_at=_T_MON),
        _obs(callsign="PAT16", observed_at=_T_TUE),
        _obs(callsign="RCH100", observed_at=_T_WED),
    ]
    result = facility_operational_tempo(obs)
    assert result["unique_callsigns"] == 2
    assert result["recurring_callsigns"] == ["PAT16"]
    assert result["most_active_callsign"] == "PAT16"
    assert result["recurrence_concentration"] == 0.5


def test_facility_tempo_busiest_weekdays_order():
    obs = [
        _obs(observed_at=_T_MON),
        _obs(observed_at=_T_MON + 3600),  # 2× Monday
        _obs(observed_at=_T_TUE),
    ]
    result = facility_operational_tempo(obs)
    weekdays = [d for d, _ in result["busiest_weekdays"]]
    # Monday before Tuesday (canonical order)
    assert weekdays.index("monday") < weekdays.index("tuesday")


def test_facility_tempo_no_observations_no_callsigns():
    result = facility_operational_tempo([])
    assert result["busiest_weekdays"] == []
    assert result["busiest_hours"] == []


# ---------------------------------------------------------------------------
# identify_recurring_callsigns
# ---------------------------------------------------------------------------


def _multi_obs(callsign, count, base_ts=_T_MON, day_step=86_400):
    """Generate `count` observations for a callsign across `count` days."""
    return [
        {
            "normalized_callsign": callsign,
            "facility": "DARPA HQ",
            "source": "adsb_sentinel",
            "observed_at": base_ts + i * day_step,
        }
        for i in range(count)
    ]


def test_identify_recurring_filters_below_min_observations():
    obs = _multi_obs("PAT16", 4)  # 4 < min_observations=5
    result = identify_recurring_callsigns(obs, min_observations=5, min_days=1)
    assert result == []


def test_identify_recurring_filters_below_min_days():
    # 5 observations all on the same day
    ts = _T_MON
    obs = [
        {
            "normalized_callsign": "PAT16",
            "facility": "HQ",
            "source": "adsb",
            "observed_at": ts + i * 100,
        }
        for i in range(5)
    ]
    result = identify_recurring_callsigns(obs, min_observations=5, min_days=3)
    assert result == []


def test_identify_recurring_passes_threshold():
    obs = _multi_obs("PAT16", 6)  # 6 obs, 6 days
    result = identify_recurring_callsigns(obs, min_observations=5, min_days=3)
    assert len(result) == 1
    assert result[0]["callsign"] == "PAT16"


def test_identify_recurring_sorted_by_obs_count_desc():
    obs = _multi_obs("PAT16", 8) + _multi_obs("RCH100", 6, base_ts=_T_MON + 1)
    result = identify_recurring_callsigns(obs, min_observations=5, min_days=3)
    assert len(result) == 2
    assert result[0]["total_observations"] >= result[1]["total_observations"]


def test_identify_recurring_min_span_days_filter():
    # 5 obs across 5 days but within 2-day span (compressed)
    obs = [
        {
            "normalized_callsign": "PAT16",
            "facility": "HQ",
            "source": "adsb",
            "observed_at": _T_MON + i * 1800,
        }
        for i in range(5)
    ]  # all within 2 hours
    result = identify_recurring_callsigns(obs, min_observations=5, min_days=1, min_span_days=2)
    assert result == []


def test_identify_recurring_empty_input():
    assert identify_recurring_callsigns([]) == []


# ---------------------------------------------------------------------------
# callsign_temporal_clusters
# ---------------------------------------------------------------------------


def test_clusters_empty_history():
    result = callsign_temporal_clusters([])
    assert result["same_day"] == []
    assert result["same_hour"] == []
    assert result["weekday_pattern"] == {}
    assert result["weekly_windows"] == []


def test_clusters_same_day_detected():
    history = [
        _obs(observed_at=_T_MON),
        _obs(observed_at=_T_MON + 3600),  # same day as _T_MON
        _obs(observed_at=_T_TUE),
    ]
    result = callsign_temporal_clusters(history)
    assert len(result["same_day"]) == 1
    assert result["same_day"][0]["count"] == 2


def test_clusters_same_hour_detected():
    # Both _T_TUE and _T_WED are at hour 01 UTC
    history = [_obs(observed_at=_T_TUE), _obs(observed_at=_T_WED)]
    result = callsign_temporal_clusters(history)
    same_hour_entries = {e["hour"]: e["count"] for e in result["same_hour"]}
    assert "01" in same_hour_entries
    assert same_hour_entries["01"] == 2


def test_clusters_weekday_pattern_canonical_order():
    history = [
        _obs(observed_at=_T_WED),
        _obs(observed_at=_T_MON),
        _obs(observed_at=_T_TUE),
    ]
    result = callsign_temporal_clusters(history)
    keys = list(result["weekday_pattern"].keys())
    canonical = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
    assert keys == [k for k in canonical if k in keys]


def test_clusters_weekly_windows_iso_format():
    history = [_obs(observed_at=_T_MON), _obs(observed_at=_T_MON2)]
    result = callsign_temporal_clusters(history)
    for w in result["weekly_windows"]:
        assert "-W" in w["week"]


def test_clusters_malformed_timestamps_skipped():
    history = [
        {"normalized_callsign": "PAT16", "observed_at": "bad", "facility": "HQ"},
        _obs(observed_at=_T_MON),
    ]
    result = callsign_temporal_clusters(history)
    # 1 valid observation → no same_day (need >1), weekly_windows has 1 entry
    assert len(result["weekly_windows"]) == 1


def test_clusters_deterministic():
    history = [_obs(observed_at=_T_MON), _obs(observed_at=_T_TUE), _obs(observed_at=_T_WED)]
    a = callsign_temporal_clusters(history)
    b = callsign_temporal_clusters(history)
    assert a == b
