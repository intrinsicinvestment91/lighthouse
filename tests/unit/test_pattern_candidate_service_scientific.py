"""Scientific detector service path under LHP-V1-PROFILE-01C.

Service-level detection requires an active profile that owns the detector. With only
repository_capability_discovery activatable, scientific detection is refused at the
admission gate. Pure detector unit tests remain in test_scientific_source_disappearance.py.
Historical candidate reads remain available when a candidate row already exists.
"""

from __future__ import annotations

import datetime
import hashlib

import pytest

from modules import db
from modules import pattern_candidate_service as service
from modules import public_source_check as psc
from modules import scientific_source_disappearance as ssd
from modules.pattern_candidate_service import (
    DetectorNotAdmitted,
    PatternServiceError,
    run_detection,
    show_candidate,
)
from tests.unit.active_profile_test_helpers import plant_active_rcd

DAY = 24 * 3600
DIGEST_A = hashlib.sha256(b"dataset-A").hexdigest()
DIGEST_B = hashlib.sha256(b"dataset-B").hexdigest()
POLICY_DIGEST = hashlib.sha256(b"policy").hexdigest()
ADAPTER_ID = "public_source_intake"
ADAPTER_VERSION = "public_source_intake/1"
SCIENTIFIC = "scientific_source_disappearance_review"


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "state.db")
    plant_active_rcd()
    return tmp_path / "state.db"


def _iso(day):
    return datetime.datetime.fromtimestamp(1_800_000_000 + day * DAY, tz=datetime.UTC).isoformat()


def store_run(*, day, targets, nonce):
    at = _iso(day)
    canonicals = [t[0] for t in targets]
    run_id = psc.compute_run_id(
        started_at=at,
        run_nonce=nonce,
        adapter_id=ADAPTER_ID,
        adapter_version=ADAPTER_VERSION,
        policy_digest=POLICY_DIGEST,
        target_count=len(canonicals),
        target_set_digest=psc.target_set_digest(canonicals),
    )
    checks = [
        psc.build_check(
            run_id=run_id,
            canonical_locator=canonical,
            final_locator=canonical,
            redirected=False,
            redirect_chain=(),
            outcome=outcome,
            http_status=status,
            content_digest=digest,
            body_size_bytes=(512 if digest else None),
            content_type=("text/csv" if digest else None),
            checked_at=at,
            adapter_id=ADAPTER_ID,
            adapter_version=ADAPTER_VERSION,
            policy_digest=POLICY_DIGEST,
        )
        for (canonical, outcome, status, digest) in targets
    ]
    run = psc.build_run(
        started_at=at,
        completed_at=at,
        run_nonce=nonce,
        adapter_id=ADAPTER_ID,
        adapter_version=ADAPTER_VERSION,
        policy_digest=POLICY_DIGEST,
        targets_ordered=tuple(canonicals),
        checks=tuple(checks),
    )
    db.store_check_run(run=run.to_dict(), checks=[check.to_dict() for check in checks])


L = "https://a.example.org/dataset.csv"


def _passing_context(*, trigger_day):
    return [(f"https://o{i}.example.org/x", "available", 200, DIGEST_B) for i in range(3)]


def seed_valid_disappearance(*, trigger_day=10):
    store_run(day=1, targets=[(L, "available", 200, DIGEST_A)], nonce="n1")
    store_run(
        day=trigger_day,
        targets=[(L, "not_found", 404, None), *_passing_context(trigger_day=trigger_day)],
        nonce="n2",
    )


def test_scientific_detection_refuses_under_active_rcd(ledger):
    seed_valid_disappearance()
    with pytest.raises(DetectorNotAdmitted, match="not admitted|ownership"):
        run_detection(detector=SCIENTIFIC, now=_iso(999))


def test_scientific_dry_run_also_refuses_under_active_rcd(ledger):
    seed_valid_disappearance()
    with pytest.raises(DetectorNotAdmitted):
        run_detection(detector=SCIENTIFIC, dry_run=True, now=_iso(999))


def test_scientific_detection_refuses_without_active_profile(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "state.db")
    with pytest.raises(PatternServiceError, match="active profile"):
        run_detection(detector=SCIENTIFIC, dry_run=True)


def test_historical_scientific_candidate_show_remains_available(ledger):
    """A previously stored scientific candidate remains readable under active RCD."""
    seed_valid_disappearance()
    data, _truncated = service._collect_public_source_input(_iso(999))
    result = ssd.detect_disappearing_sources(data)
    assert len(result.candidates) == 1
    candidate = service._assemble_scientific_candidate(result.candidates[0], _iso(999))
    db.init_pattern_candidate_tables()
    assert db.insert_pattern_candidate(candidate) is True

    shown = show_candidate(candidate.candidate_id)
    assert shown["candidate"]["detector_id"] == SCIENTIFIC
    assert "evidence" in shown["candidate"]
    assert shown["candidate"]["evidence"]["triggering_not_found"]["http_status"] == 404
    assert any("permanent" in s.lower() for s in shown["unknown_statements"])
    assert any("not a finding" in s.lower() for s in shown["limitation_statements"])


def test_an_unknown_detector_is_refused(ledger):
    with pytest.raises(PatternServiceError):
        run_detection(detector="not_a_detector")
