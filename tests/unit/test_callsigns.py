"""Unit tests for modules/callsigns.py"""

import pytest

from modules.callsigns import (
    CALLSIGN_SCHEMA_VERSION,
    CallsignEnrichment,
    callsign_prefix,
    enrich_callsign,
    is_known_military_callsign,
    normalize_callsign,
)


def test_normalize_strips_and_uppercases():
    assert normalize_callsign(" pat16 ") == "PAT16"
    assert normalize_callsign("MUSL  ") == "MUSL"


def test_normalize_returns_none_for_empty():
    assert normalize_callsign("") is None
    assert normalize_callsign("   ") is None
    assert normalize_callsign(None) is None


def test_prefix_extraction_pat16():
    assert callsign_prefix("PAT16") == "PAT"


def test_prefix_extraction_zeus31():
    assert callsign_prefix("ZEUS31") == "ZEUS"


def test_prefix_returns_none_for_numeric_only():
    assert callsign_prefix("12345") is None


def test_known_military_prefix_pat16():
    assert is_known_military_callsign("PAT16") is True


def test_known_military_prefix_musl():
    assert is_known_military_callsign("MUSL") is True


def test_unknown_prefix_not_military_style():
    assert is_known_military_callsign("UAL123") is False


def test_enrichment_empty_callsign_confidence_zero():
    info = enrich_callsign(None)
    assert info.confidence == 0.0
    assert info.normalized_callsign is None
    assert info.tags == []


def test_enrichment_known_prefix_confidence_08():
    info = enrich_callsign("PAT16")
    assert info.confidence == 0.8
    assert info.military_style is True


def test_enrichment_unknown_alpha_prefix_confidence_03():
    info = enrich_callsign("C6575")
    assert info.confidence == 0.3
    assert info.prefix == "C"
    assert info.military_style is False


def test_enrichment_numeric_confidence_01():
    info = enrich_callsign("12345")
    assert info.confidence == 0.1
    assert info.prefix is None


def test_enrichment_tags_include_military_style_and_category():
    info = enrich_callsign("ZEUS31")
    assert "military_style" in info.tags
    assert "known_prefix:ZEUS" in info.tags
    assert "category:military_or_special_mission" in info.tags


def test_serialization_round_trip():
    info = enrich_callsign("OTIS61")
    restored = CallsignEnrichment.from_dict(info.to_dict())
    assert restored.normalized_callsign == "OTIS61"
    assert restored.prefix == "OTIS"
    assert restored.schema_version == CALLSIGN_SCHEMA_VERSION


def test_validation_rejects_bad_confidence():
    record = CallsignEnrichment(
        callsign="X",
        normalized_callsign="X",
        prefix=None,
        category=None,
        military_style=False,
        confidence=1.5,
    )
    with pytest.raises(ValueError, match="confidence"):
        record.validate()
