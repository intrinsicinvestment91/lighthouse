import pytest

from modules.correlations import CORRELATION_SCHEMA_VERSION, CorrelationRecord


def test_valid_correlation_record_validate_passes():
    record = CorrelationRecord(
        event_id="evt_123",
        event_type="sec_adsb",
        entity="Tesla",
        score=7.5,
        signals=["sec", "adsb"],
        evidence={"sec": {"accession_no": "0001"}},
        observed_at="2026-04-27T10:00:00Z",
    )

    record.validate()

    assert record.event_id == "evt_123"
    assert record.entity == "Tesla"
    assert record.score == 7.5


def test_invalid_score_raises_value_error():
    with pytest.raises(ValueError, match="score must be 0-10"):
        CorrelationRecord(
            event_id="evt_123",
            event_type="sec_adsb",
            entity="Tesla",
            score=10.1,
            signals=["sec"],
            evidence={},
        ).validate()


def test_missing_entity_raises_value_error():
    with pytest.raises(ValueError, match="entity required"):
        CorrelationRecord(
            event_id="evt_123",
            event_type="sec_only",
            entity="",
            score=6.0,
            signals=["sec"],
            evidence={},
        ).validate()


def test_round_trip_serialization():
    original = CorrelationRecord(
        event_id="evt_999",
        event_type="sec_flight_adsb",
        entity="Lockheed Martin",
        score=9.0,
        signals=["sec", "flight", "adsb"],
        evidence={"flight": [{"icao24": "abc123"}]},
        observed_at="2026-04-27T10:00:00Z",
    )

    encoded = original.to_dict()
    decoded = CorrelationRecord.from_dict(encoded)

    assert decoded.event_id == original.event_id
    assert decoded.event_type == original.event_type
    assert decoded.entity == original.entity
    assert decoded.score == original.score
    assert decoded.signals == original.signals
    assert decoded.evidence == original.evidence
    assert decoded.observed_at == original.observed_at
    assert decoded.schema_version == CORRELATION_SCHEMA_VERSION


def test_from_dict_defaults_signals_and_evidence():
    record = CorrelationRecord.from_dict(
        {
            "event_id": "evt_defaulted",
            "event_type": "sec_only",
            "entity": "SpaceX",
            "score": 6.0,
        }
    )

    assert record.signals == []
    assert record.evidence == {}
    assert record.schema_version == CORRELATION_SCHEMA_VERSION
