import pytest

from modules.alerts import ALERT_SCHEMA_VERSION, AlertRecord


def test_minimal_valid_construction():
    record = AlertRecord(source="sec_sentinel", alert_type="material_filing")
    record.validate()

    assert record.source == "sec_sentinel"
    assert record.alert_type == "material_filing"


def test_to_dict_serializes():
    record = AlertRecord(
        source="velocity_tracker",
        alert_type="velocity_spike",
        title="Spike detected",
        entity="Tesla",
        score=0.85,
        score_label="high",
        provenance={"source_url": "https://example.com"},
        payload={"ipc": "H01M"},
    )

    result = record.to_dict()

    assert result["source"] == "velocity_tracker"
    assert result["alert_type"] == "velocity_spike"
    assert result["title"] == "Spike detected"
    assert result["entity"] == "Tesla"
    assert result["score"] == 0.85
    assert result["score_label"] == "high"
    assert result["provenance"] == {"source_url": "https://example.com"}
    assert result["payload"] == {"ipc": "H01M"}


def test_from_dict_deserializes():
    data = {
        "source": "adsb_sentinel",
        "alert_type": "unusual_aircraft",
        "title": "Military aircraft near facility",
        "entity": "Nellis",
        "score": 0.7,
        "score_label": "medium",
        "provenance": {"source_type": "api"},
        "payload": {"hex": "AE1234"},
        "schema_version": ALERT_SCHEMA_VERSION,
    }

    record = AlertRecord.from_dict(data)

    assert record.source == "adsb_sentinel"
    assert record.alert_type == "unusual_aircraft"
    assert record.title == "Military aircraft near facility"
    assert record.entity == "Nellis"
    assert record.score == 0.7
    assert record.score_label == "medium"
    assert record.provenance == {"source_type": "api"}
    assert record.payload == {"hex": "AE1234"}
    assert record.schema_version == ALERT_SCHEMA_VERSION


def test_missing_source_raises_value_error():
    with pytest.raises(ValueError, match="source must be a non-empty string"):
        AlertRecord(source="", alert_type="material_filing").validate()


def test_missing_alert_type_raises_value_error():
    with pytest.raises(ValueError, match="alert_type must be a non-empty string"):
        AlertRecord(source="sec_sentinel", alert_type="").validate()


def test_invalid_score_raises_value_error():
    with pytest.raises(ValueError, match="score must be None or between 0.0 and 1.0"):
        AlertRecord(source="sec_sentinel", alert_type="material_filing", score=1.5).validate()


def test_invalid_score_label_raises_value_error():
    with pytest.raises(ValueError, match="score_label must be one of: low, medium, high"):
        AlertRecord(
            source="sec_sentinel",
            alert_type="material_filing",
            score_label="critical",
        ).validate()


def test_defaults_for_payload_and_provenance():
    record = AlertRecord(source="arxiv_sentinel", alert_type="researcher_silence")
    assert record.payload == {}
    assert record.provenance == {}

    from_data = AlertRecord.from_dict(
        {"source": "arxiv_sentinel", "alert_type": "researcher_silence"}
    )
    assert from_data.payload == {}
    assert from_data.provenance == {}


def test_default_schema_version_matches_constant():
    record = AlertRecord(source="flight_tracker", alert_type="unusual_flight")
    assert record.schema_version == ALERT_SCHEMA_VERSION


def test_invalid_schema_version_raises_value_error():
    with pytest.raises(ValueError, match="schema_version must be >= 1"):
        AlertRecord(source="x", alert_type="y", schema_version=0).validate()
