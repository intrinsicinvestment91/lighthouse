"""Unit tests for modules/correlation_scoring.py"""

from modules.correlation_scoring import (
    clamp_score,
    evidence_quality_bonus,
    multi_signal_bonus,
    normalize_signals,
    score_correlation,
)

HIGH_EVIDENCE = {
    "sec": {"items": "1.01,8.01"},
    "adsb": [
        {
            "hex": "AE1234",
            "military": True,
            "military_style_callsign": True,
            "callsign": "PAT16",
            "callsign_enrichment": {"military_style": True},
        }
    ],
    "flight": [{"icao24": "ae5678", "callsign": "PAT99"}],
}


def test_normalize_signals_lowercases_deduplicates_sorts():
    assert normalize_signals(["Flight", "SEC", "flight", ""]) == ["flight", "sec"]


def test_clamp_score_clamps_low_high_and_rounds():
    assert clamp_score(-1.0) == 0.0
    assert clamp_score(11.234) == 10.0
    assert clamp_score(7.456) == 7.5


def test_base_signal_weights_produce_nonzero_score():
    score = score_correlation(signals=["sec", "flight"])
    assert score > 0


def test_archive_only_score_remains_low():
    score = score_correlation(signals=["archive"])
    assert score <= 1.0


def test_sec_flight_higher_than_flight_only():
    sec_flight = score_correlation(signals=["sec", "flight"])
    flight_only = score_correlation(signals=["flight"])
    assert sec_flight > flight_only


def test_sec_adsb_flight_higher_than_sec_flight():
    triple = score_correlation(signals=["sec", "adsb", "flight"])
    pair = score_correlation(signals=["sec", "flight"])
    assert triple > pair


def test_military_evidence_increases_score():
    base = score_correlation(signals=["sec", "adsb"], evidence={})
    boosted = score_correlation(
        signals=["sec", "adsb"],
        evidence={"adsb": [{"military": True}]},
    )
    assert boosted > base


def test_military_style_callsign_evidence_increases_score():
    base = score_correlation(signals=["flight"], evidence={})
    boosted = score_correlation(
        signals=["flight"],
        evidence={"flight": [{"military_style_callsign": True}]},
    )
    assert boosted > base


def test_ae_range_hex_evidence_increases_score():
    base = score_correlation(signals=["adsb"], evidence={})
    boosted = score_correlation(
        signals=["adsb"],
        evidence={"adsb": [{"hex": "AE1234"}]},
    )
    assert boosted > base


def test_sec_material_item_evidence_increases_score():
    base = score_correlation(signals=["sec"], evidence={"sec": {"items": "9.01"}})
    boosted = score_correlation(
        signals=["sec"],
        evidence={"sec": {"items": "1.01,8.01"}},
    )
    assert boosted > base


def test_known_callsign_text_increases_score():
    base = score_correlation(signals=["flight"], evidence={"flight": [{"callsign": "N123AB"}]})
    boosted = score_correlation(
        signals=["flight"],
        evidence={"flight": [{"callsign": "PAT16"}]},
    )
    assert boosted > base


def test_quality_bonus_capped():
    bonus = evidence_quality_bonus(HIGH_EVIDENCE)
    assert bonus == 2.0


def test_multi_signal_bonus_capped():
    bonus = multi_signal_bonus(["sec", "adsb", "flight", "narrative", "patent"])
    assert bonus == 2.5


def test_score_max_clamps_at_10():
    score = score_correlation(
        signals=["sec", "adsb", "flight", "narrative"],
        evidence=HIGH_EVIDENCE,
        base_score=9.5,
    )
    assert score == 10.0


def test_empty_signals_without_base_returns_zero():
    assert score_correlation(signals=[]) == 0.0
