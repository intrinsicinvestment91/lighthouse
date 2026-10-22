"""LHP-LIFE-EVID-01 — strict field typing and honesty for evidence reports.

These tests describe desired behaviour: wrong-typed fields and contradictory
verified/completed reports must refuse via TransitionEvidenceError, while honest
failed evidence still loads as blocked.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from modules.backup_verify import (
    BackupVerificationReport,
    ReportValidationError,
    RestoreDrillReport,
    canonical_report_digest,
    validate_supplied_verification_report,
    verify_backup,
)
from modules.profile_transition import (
    STATUS_BLOCKED,
    TransitionEvidenceError,
    build_transition_preview,
    load_evidence,
)
from tests.unit.test_backup_evidence_binding import _make_backup, _stamp
from tests.unit.test_profile_transition import _profile

_VERIFICATION_LIST_FIELDS = frozenset(
    {
        "structural_errors",
        "missing_payload",
        "digest_mismatches",
        "extra_payload",
        "blocking_failures",
        "warnings",
    }
)
_VERIFICATION_DICT_FIELDS = frozenset({"sqlite_integrity"})
_RESTORE_LIST_FIELDS = frozenset(
    {
        "blocking_failures",
        "warnings",
        "unproven_guarantees",
    }
)


def _restamp_as_from_dict(report: dict) -> dict:
    """Stamp artifact_digest over the base (pre-fix) from_dict coerced form.

    List-typed fields go through list() and dict-typed fields through dict() when
    coercion succeeds; otherwise the raw value is stamped. Matches current from_dict.
    """
    stamped = copy.deepcopy(report)
    body = {k: v for k, v in stamped.items() if k != "artifact_digest"}
    list_fields = _VERIFICATION_LIST_FIELDS | _RESTORE_LIST_FIELDS
    coerced: dict = {}
    for key, value in body.items():
        if key in list_fields:
            try:
                coerced[key] = list(value)
            except TypeError:
                coerced[key] = value
        elif key in _VERIFICATION_DICT_FIELDS:
            try:
                coerced[key] = dict(value)
            except (TypeError, ValueError):
                coerced[key] = value
        else:
            coerced[key] = value
    stamped["artifact_digest"] = canonical_report_digest(coerced)
    return stamped


def _write_json(path: Path, payload: dict) -> Path:
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


@pytest.mark.parametrize(
    "field,value",
    [
        ("checked_entries", "many"),
        ("checked_entries", True),
        ("backup_root", 42),
        ("manifest_id", 7),
        ("warnings", "ab"),
        ("digest_mismatches", "xy"),
        ("sqlite_integrity", []),
        ("sqlite_integrity", {"state.db": 1}),
        ("warnings", [1]),
    ],
)
def test_verification_field_wrong_type_refused(tmp_path, field, value):
    """LIFE-EVID-01 item D-1: verification report fields refuse wrong types."""
    chain = _make_backup(tmp_path, name="one", manifest_id="lhbackup-typed", payload="alpha\n")
    report = json.loads(chain["verification"].read_text(encoding="utf-8"))
    report[field] = value
    report = _restamp_as_from_dict(report)
    verification_path = _write_json(tmp_path / "tampered-verification.json", report)
    with pytest.raises(TransitionEvidenceError):
        load_evidence(chain["manifest"], verification_path, chain["restore"])


@pytest.mark.parametrize(
    "field,value",
    [
        ("manifest_valid", "yes"),
        ("files_restored", "3"),
        ("files_restored", True),
        ("note", 1),
        ("restore_destination", 5),
        ("unproven_guarantees", "x"),
        ("source_unchanged", 1),
    ],
)
def test_restore_field_wrong_type_refused(tmp_path, field, value):
    """LIFE-EVID-01 item D-1: restore-drill report fields refuse wrong types."""
    chain = _make_backup(tmp_path, name="one", manifest_id="lhbackup-typed", payload="alpha\n")
    report = json.loads(chain["restore"].read_text(encoding="utf-8"))
    report[field] = value
    report = _restamp_as_from_dict(report)
    restore_path = _write_json(tmp_path / "tampered-restore.json", report)
    with pytest.raises(TransitionEvidenceError):
        load_evidence(chain["manifest"], chain["verification"], restore_path)


def test_producer_reports_still_validate(tmp_path):
    """LIFE-EVID-01 item H-1: real verify_backup/restore_drill output still round-trips."""
    chain = _make_backup(tmp_path, name="one", manifest_id="lhbackup-typed", payload="alpha\n")
    verification = json.loads(chain["verification"].read_text(encoding="utf-8"))
    restore = json.loads(chain["restore"].read_text(encoding="utf-8"))

    v_parsed = BackupVerificationReport.from_dict(verification)
    v_parsed.validate()
    assert v_parsed.to_dict() == verification

    r_parsed = RestoreDrillReport.from_dict(restore)
    r_parsed.validate()
    assert r_parsed.to_dict() == restore

    ev = load_evidence(chain["manifest"], chain["verification"], chain["restore"])
    assert ev.backup_verification_status == "verified"
    assert ev.restore_drill_status == "completed"


@pytest.mark.parametrize(
    "field",
    [
        "digest_mismatches",
        "missing_payload",
        "structural_errors",
        "extra_payload",
        "blocking_failures",
    ],
)
def test_restamped_verified_with_failing_details_not_ready(tmp_path, field):
    """LIFE-EVID-01 item D-2: verified status with failure details must not load as ready."""
    chain = _make_backup(tmp_path, name="one", manifest_id="lhbackup-typed", payload="alpha\n")
    report = json.loads(chain["verification"].read_text(encoding="utf-8"))
    report[field] = ["x"]
    assert report["status"] == "verified"
    report = _stamp(report)
    verification_path = _write_json(tmp_path / "contradictory-verification.json", report)
    with pytest.raises(TransitionEvidenceError):
        load_evidence(chain["manifest"], verification_path, chain["restore"])


@pytest.mark.parametrize(
    "field,value",
    [
        ("manifest_valid", False),
        ("payload_verified", False),
        ("restored_digests_verified", False),
        ("sqlite_integrity_verified", False),
        ("source_unchanged", False),
        ("files_restored", 0),
        ("blocking_failures", ["x"]),
    ],
)
def test_restamped_completed_with_false_flag_not_ready(tmp_path, field, value):
    """LIFE-EVID-01 item D-2: completed status with failing details must not load as ready."""
    chain = _make_backup(tmp_path, name="one", manifest_id="lhbackup-typed", payload="alpha\n")
    report = json.loads(chain["restore"].read_text(encoding="utf-8"))
    report[field] = value
    assert report["status"] == "completed"
    report = _stamp(report)
    restore_path = _write_json(tmp_path / "contradictory-restore.json", report)
    with pytest.raises(TransitionEvidenceError):
        load_evidence(chain["manifest"], chain["verification"], restore_path)


def test_failed_status_with_details_still_loads_as_blocked(tmp_path):
    """LIFE-EVID-01 item H-1: honest failed verification still loads; preview is blocked."""
    chain = _make_backup(tmp_path, name="one", manifest_id="lhbackup-typed", payload="alpha\n")
    (chain["backup"] / "payload" / "entities.json").write_bytes(b"corrupted\n")
    failed = verify_backup(chain["backup"])
    assert failed["status"] == "failed"
    verification_path = _write_json(tmp_path / "failed-verification.json", failed)
    # Original restore report remains bound to the same unchanged manifest.json bytes.
    ev = load_evidence(chain["manifest"], verification_path, chain["restore"])
    assert ev.backup_verification_status == "failed"
    preview = build_transition_preview(target_profile=_profile(), evidence=ev)
    assert preview.transition_status == STATUS_BLOCKED


def test_fixture_backup_has_sqlite_entries(tmp_path):
    """LIFE-EVID-01 item D-2: fixture verification report must include sqlite_integrity ok."""
    chain = _make_backup(tmp_path, name="one", manifest_id="lhbackup-typed", payload="alpha\n")
    report = json.loads(chain["verification"].read_text(encoding="utf-8"))
    ok_keys = [k for k, v in report["sqlite_integrity"].items() if v == "ok"]
    assert ok_keys, (
        "fixture verification must have at least one sqlite_integrity key with value 'ok'"
    )


@pytest.mark.parametrize(
    "bad_value",
    [
        "corrupt",
        "*** in database main ***",
        "unopenable: x",
        "",
    ],
)
def test_restamped_verified_with_non_ok_sqlite_integrity_refused(tmp_path, bad_value):
    """LIFE-EVID-01 item D-2: verified status with non-ok sqlite_integrity must refuse."""
    chain = _make_backup(tmp_path, name="one", manifest_id="lhbackup-typed", payload="alpha\n")
    report = json.loads(chain["verification"].read_text(encoding="utf-8"))
    key = next(iter(report["sqlite_integrity"]))
    report["sqlite_integrity"][key] = bad_value
    assert report["status"] == "verified"
    assert report["blocking_failures"] == []
    report = _restamp_as_from_dict(report)
    verification_path = _write_json(tmp_path / "bad-sqlite-verification.json", report)
    with pytest.raises(TransitionEvidenceError):
        load_evidence(chain["manifest"], verification_path, chain["restore"])
    with pytest.raises(ReportValidationError):
        BackupVerificationReport.from_dict(report).validate()


def test_verified_missing_sqlite_entry_refused(tmp_path):
    """LIFE-EVID-01 item D-2: verified report missing a sqlite_integrity key must refuse."""
    chain = _make_backup(tmp_path, name="one", manifest_id="lhbackup-typed", payload="alpha\n")
    report = json.loads(chain["verification"].read_text(encoding="utf-8"))
    key = next(iter(report["sqlite_integrity"]))
    del report["sqlite_integrity"][key]
    assert report["status"] == "verified"
    report = _restamp_as_from_dict(report)
    verification_path = _write_json(tmp_path / "missing-sqlite-verification.json", report)
    with pytest.raises(TransitionEvidenceError):
        load_evidence(chain["manifest"], verification_path, chain["restore"])
    with pytest.raises(ReportValidationError):
        validate_supplied_verification_report(
            report,
            backup_root=chain["backup"],
            manifest_id=report["manifest_id"],
        )


def test_verified_unknown_sqlite_entry_refused(tmp_path):
    """LIFE-EVID-01 item D-2: verified report with unknown sqlite_integrity key must refuse."""
    chain = _make_backup(tmp_path, name="one", manifest_id="lhbackup-typed", payload="alpha\n")
    report = json.loads(chain["verification"].read_text(encoding="utf-8"))
    report["sqlite_integrity"]["not-in-manifest.db"] = "ok"
    assert report["status"] == "verified"
    report = _restamp_as_from_dict(report)
    verification_path = _write_json(tmp_path / "unknown-sqlite-verification.json", report)
    with pytest.raises(TransitionEvidenceError):
        load_evidence(chain["manifest"], verification_path, chain["restore"])
    with pytest.raises(ReportValidationError):
        validate_supplied_verification_report(
            report,
            backup_root=chain["backup"],
            manifest_id=report["manifest_id"],
        )


def test_failed_report_with_non_ok_sqlite_still_loads_as_blocked(tmp_path):
    """LIFE-EVID-01 item H-1: failed report with non-ok sqlite_integrity still loads as blocked."""
    chain = _make_backup(tmp_path, name="one", manifest_id="lhbackup-typed", payload="alpha\n")
    report = json.loads(chain["verification"].read_text(encoding="utf-8"))
    key = next(iter(report["sqlite_integrity"]))
    report["sqlite_integrity"][key] = "corrupt"
    report["status"] = "failed"
    report["blocking_failures"] = ["x: integrity corrupt"]
    report = _restamp_as_from_dict(report)
    verification_path = _write_json(tmp_path / "failed-sqlite-verification.json", report)
    ev = load_evidence(chain["manifest"], verification_path, chain["restore"])
    assert ev.backup_verification_status == "failed"
    preview = build_transition_preview(target_profile=_profile(), evidence=ev)
    assert preview.transition_status == STATUS_BLOCKED


@pytest.mark.parametrize(
    "kind,field,value",
    [
        ("verification", "status", ["verified"]),
        ("verification", "warnings", 5),
        ("verification", "sqlite_integrity", 5),
        ("restore", "blocking_failures", 5),
    ],
)
def test_malformed_values_raise_typed_refusal(tmp_path, kind, field, value):
    """LIFE-EVID-01 item H-1: malformed values raise ReportValidationError /
    TransitionEvidenceError."""
    chain = _make_backup(tmp_path, name="one", manifest_id="lhbackup-typed", payload="alpha\n")
    if kind == "verification":
        report = json.loads(chain["verification"].read_text(encoding="utf-8"))
        report[field] = value
        path = _write_json(tmp_path / "malformed-verification.json", report)
        with pytest.raises(ReportValidationError):
            BackupVerificationReport.from_dict(report).validate()
        with pytest.raises(TransitionEvidenceError):
            load_evidence(chain["manifest"], path, chain["restore"])
    else:
        report = json.loads(chain["restore"].read_text(encoding="utf-8"))
        report[field] = value
        path = _write_json(tmp_path / "malformed-restore.json", report)
        with pytest.raises(ReportValidationError):
            RestoreDrillReport.from_dict(report).validate()
        with pytest.raises(TransitionEvidenceError):
            load_evidence(chain["manifest"], chain["verification"], path)
