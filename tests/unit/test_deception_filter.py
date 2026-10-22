"""Unit tests for the deception filter (Branch 1: feature/deception-filter)."""

import time

import pytest

from modules import db
from modules.correlations import CORRELATION_SCHEMA_VERSION, CorrelationRecord
from modules.correlator import (
    _apply_deception_filter,
    _build_event,
    _verification_fields,
)

# ---------------------------------------------------------------------------
# _verification_fields()
# ---------------------------------------------------------------------------


def test_single_detector_tagged_single_source():
    fields = _verification_fields(["sec"], flight=[], adsb=[])
    assert fields["verification_status"] == "single_source"
    assert fields["is_single_source"] is True
    assert fields["source_trust_score"] == pytest.approx(0.3)


def test_two_detectors_no_physical_corroborated():
    fields = _verification_fields(["sec", "narrative"], flight=[], adsb=[])
    assert fields["verification_status"] == "corroborated"
    assert fields["is_single_source"] is False
    assert fields["source_trust_score"] == pytest.approx(0.6)


def test_two_detectors_with_flight_corroborated():
    fields = _verification_fields(["sec", "flight"], flight=[{"icao24": "abc"}], adsb=[])
    assert fields["verification_status"] == "corroborated"
    assert fields["source_trust_score"] == pytest.approx(0.6)


def test_three_detectors_corroborated():
    fields = _verification_fields(["sec", "flight", "adsb"], flight=[{}], adsb=[{}])
    assert fields["verification_status"] == "corroborated"
    assert fields["source_trust_score"] == pytest.approx(0.8)


def test_four_detectors_multi_detector():
    fields = _verification_fields(["sec", "flight", "adsb", "narrative"], flight=[{}], adsb=[{}])
    assert fields["verification_status"] == "multi_detector"
    assert fields["source_trust_score"] == pytest.approx(1.0)
    assert fields["is_single_source"] is False


# ---------------------------------------------------------------------------
# _build_event() — verification fields propagated
# ---------------------------------------------------------------------------


def test_build_event_sec_only_is_single_source():
    entity = {"name": "Acme Corp", "assignee_patterns": ["Acme"]}
    sec = {"accession_no": "0001", "filed_at": "2026-04-01", "company": "Acme Corp"}
    event = _build_event(entity, sec, flight=[], adsb=[], narrative=[])
    assert event["is_single_source"] is True
    assert event["verification_status"] == "single_source"


def test_build_event_multi_detector_not_single_source():
    entity = {"name": "Acme Corp", "assignee_patterns": ["Acme"]}
    sec = {"accession_no": "0001", "filed_at": "2026-04-01", "company": "Acme Corp"}
    flight = [{"icao24": "abc", "callsign": "EXEC1", "facility": "HQ"}]
    adsb = [{"hex": "AE1234", "callsign": "SPAR1", "military": True, "facility": "HQ"}]
    event = _build_event(entity, sec, flight=flight, adsb=adsb, narrative=[])
    assert event["is_single_source"] is False
    assert event["verification_status"] in {"corroborated", "multi_detector"}


# ---------------------------------------------------------------------------
# _apply_deception_filter() — tag-only pass, never drops events
# ---------------------------------------------------------------------------


def test_apply_deception_filter_returns_all_events():
    events = [
        {"event_id": "a", "is_single_source": True, "verification_status": "single_source"},
        {"event_id": "b", "is_single_source": False, "verification_status": "corroborated"},
    ]
    result = _apply_deception_filter(events)
    assert len(result) == 2


def test_apply_deception_filter_empty_list():
    assert _apply_deception_filter([]) == []


# ---------------------------------------------------------------------------
# CorrelationRecord — new fields round-trip
# ---------------------------------------------------------------------------


def test_correlation_record_new_fields_default():
    record = CorrelationRecord(
        event_id="evt_1",
        event_type="sec_only",
        entity="Acme",
        score=6.5,
        signals=["sec"],
        evidence={},
    )
    assert record.verification_status == "unverified"
    assert record.source_trust_score == 0.0
    assert record.is_single_source is False


def test_correlation_record_validation_rejects_bad_status():
    record = CorrelationRecord(
        event_id="evt_2",
        event_type="sec_only",
        entity="Acme",
        score=6.5,
        signals=["sec"],
        evidence={},
        verification_status="totally_made_up",
    )
    with pytest.raises(ValueError, match="verification_status"):
        record.validate()


def test_correlation_record_round_trip_new_fields():
    original = CorrelationRecord(
        event_id="evt_3",
        event_type="sec_flight",
        entity="Lockheed Martin",
        score=8.0,
        signals=["sec", "flight"],
        evidence={"sec": {}},
        verification_status="corroborated",
        source_trust_score=0.6,
        is_single_source=False,
    )
    encoded = original.to_dict()
    decoded = CorrelationRecord.from_dict(encoded)
    assert decoded.verification_status == "corroborated"
    assert decoded.source_trust_score == pytest.approx(0.6)
    assert decoded.is_single_source is False
    assert decoded.schema_version == CORRELATION_SCHEMA_VERSION


def test_from_dict_defaults_new_fields_for_old_records():
    # Simulates a DB row from before schema version 2
    record = CorrelationRecord.from_dict(
        {
            "event_id": "old_evt",
            "event_type": "sec_only",
            "entity": "Acme",
            "score": 6.0,
            "signals": ["sec"],
            "evidence": {},
        }
    )
    assert record.verification_status == "unverified"
    assert record.source_trust_score == 0.0
    assert record.is_single_source is False


# ---------------------------------------------------------------------------
# DB: new columns exist and new functions work
# ---------------------------------------------------------------------------


def test_probationary_columns_exist_after_init(tmp_path, monkeypatch):
    test_db_path = tmp_path / "state.db"
    monkeypatch.setattr(db, "DB_PATH", test_db_path)
    db.init_correlation_table()
    columns = db._get_table_columns("correlation_events")
    assert "verification_status" in columns
    assert "source_trust_score" in columns
    assert "is_single_source" in columns
    assert "probation_expires_at" in columns
    assert "promoted_at" in columns


def test_fetch_probationary_correlations_returns_expired(tmp_path, monkeypatch):
    test_db_path = tmp_path / "state.db"
    monkeypatch.setattr(db, "DB_PATH", test_db_path)
    db.init_correlation_table()

    now = int(time.time())
    db.insert_correlation(
        event_id="past_evt",
        event_type="sec_only",
        entities=["Acme"],
        evidence={"sec": {}},
        score=7.5,
        signals=["sec"],
        verification_status="corroborated",
        source_trust_score=0.6,
        is_single_source=False,
        probation_expires_at=now - 60,  # expired 60s ago
    )

    results = db.fetch_probationary_correlations()
    assert any(r["event_id"] == "past_evt" for r in results)


def test_fetch_probationary_correlations_skips_future(tmp_path, monkeypatch):
    test_db_path = tmp_path / "state.db"
    monkeypatch.setattr(db, "DB_PATH", test_db_path)
    db.init_correlation_table()

    now = int(time.time())
    db.insert_correlation(
        event_id="future_evt",
        event_type="sec_only",
        entities=["Acme"],
        evidence={"sec": {}},
        score=7.5,
        signals=["sec"],
        verification_status="corroborated",
        probation_expires_at=now + 9999,  # not expired yet
    )

    results = db.fetch_probationary_correlations()
    assert not any(r["event_id"] == "future_evt" for r in results)


def test_promote_correlation_sets_promoted_at(tmp_path, monkeypatch):
    test_db_path = tmp_path / "state.db"
    monkeypatch.setattr(db, "DB_PATH", test_db_path)
    db.init_correlation_table()

    now = int(time.time())
    db.insert_correlation(
        event_id="promote_me",
        event_type="sec_flight",
        entities=["Acme"],
        evidence={"sec": {}},
        score=8.0,
        signals=["sec", "flight"],
        probation_expires_at=now - 60,
    )

    result = db.promote_correlation("promote_me", promoted_at=now)
    assert result is True

    row = db.fetchone(
        "SELECT promoted_at FROM correlation_events WHERE event_id = ?", ("promote_me",)
    )
    assert row is not None
    assert row["promoted_at"] == now


def test_promote_correlation_returns_false_for_missing_event(tmp_path, monkeypatch):
    test_db_path = tmp_path / "state.db"
    monkeypatch.setattr(db, "DB_PATH", test_db_path)
    db.init_correlation_table()

    result = db.promote_correlation("nonexistent_event", promoted_at=int(time.time()))
    assert result is False


def test_fetch_probationary_skips_already_promoted(tmp_path, monkeypatch):
    test_db_path = tmp_path / "state.db"
    monkeypatch.setattr(db, "DB_PATH", test_db_path)
    db.init_correlation_table()

    now = int(time.time())
    db.insert_correlation(
        event_id="already_promoted",
        event_type="sec_flight",
        entities=["Acme"],
        evidence={"sec": {}},
        score=8.0,
        signals=["sec", "flight"],
        probation_expires_at=now - 60,
    )
    db.promote_correlation("already_promoted", promoted_at=now)

    results = db.fetch_probationary_correlations()
    assert not any(r["event_id"] == "already_promoted" for r in results)


def test_insert_correlation_persists_verification_fields(tmp_path, monkeypatch):
    test_db_path = tmp_path / "state.db"
    monkeypatch.setattr(db, "DB_PATH", test_db_path)
    db.init_correlation_table()

    db.insert_correlation(
        event_id="verify_fields",
        event_type="sec_flight",
        entities=["Acme"],
        evidence={"sec": {}},
        score=7.5,
        signals=["sec", "flight"],
        verification_status="corroborated",
        source_trust_score=0.6,
        is_single_source=False,
        probation_expires_at=int(time.time()) + 1800,
    )

    row = db.fetchone(
        "SELECT verification_status, source_trust_score, is_single_source "
        "FROM correlation_events WHERE event_id = ?",
        ("verify_fields",),
    )
    assert row is not None
    assert row["verification_status"] == "corroborated"
    assert row["source_trust_score"] == pytest.approx(0.6)
    assert row["is_single_source"] == 0
