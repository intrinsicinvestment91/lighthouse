"""LHP-LIFE-EVID-01 — typed, bound backup verification and restore-drill reports.

Proves the existing ceremony binds reports to the exact manifest.json bytes they
describe, using the same canonical-JSON SHA-256 recipe as active_profile_state.
Substitution of individually valid artifacts from another backup must refuse.
"""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest

from modules.active_profile_state import no_active_profile_state
from modules.backup_create import create_backup
from modules.backup_manifest import sha256_file
from modules.backup_verify import (
    RESTORE_DRILL_REPORT_KEYS,
    RESTORE_DRILL_REPORT_SCHEMA,
    VERIFICATION_REPORT_SCHEMA,
    VERIFY_BACKUP_REPORT_KEYS,
    BackupVerificationReport,
    ReportValidationError,
    RestoreDrillReport,
    canonical_report_digest,
    restore_drill,
    validate_supplied_restore_drill_report,
    validate_supplied_verification_report,
    verify_backup,
)
from modules.profile_transition import (
    STATUS_BLOCKED,
    STATUS_READY,
    TransitionEvidence,
    TransitionEvidenceError,
    build_transition_preview,
    load_evidence,
)
from tests.unit.test_backup_create import _populate
from tests.unit.test_profile_transition import _profile


def _make_backup(tmp_path: Path, *, name: str, manifest_id: str, payload: str) -> dict[str, Path]:
    data_dir = tmp_path / f"{name}-data"
    _populate(data_dir)
    (data_dir / "entities.json").write_text(payload, encoding="utf-8")
    backup = tmp_path / f"{name}-backup"
    create_backup(
        data_dir=data_dir,
        destination=backup,
        db_path=data_dir / "state.db",
        boundary=data_dir,
        manifest_id=manifest_id,
        created_at="2026-08-30T00:00:00Z",
    )
    verification_path = tmp_path / f"{name}-verification.json"
    verification_path.write_text(json.dumps(verify_backup(backup), indent=2), encoding="utf-8")
    restore_path = tmp_path / f"{name}-restore.json"
    restore_path.write_text(
        json.dumps(restore_drill(backup, tmp_path / f"{name}-restore", source_data_root=data_dir)),
        encoding="utf-8",
    )
    return {
        "data_dir": data_dir,
        "backup": backup,
        "manifest": backup / "manifest.json",
        "verification": verification_path,
        "restore": restore_path,
    }


def _stamp(report: dict) -> dict:
    stamped = copy.deepcopy(report)
    body = {k: v for k, v in stamped.items() if k != "artifact_digest"}
    stamped["artifact_digest"] = canonical_report_digest(body)
    return stamped


# -- digest recipe (must match active_profile_state; not a second convention) ------------


def test_canonical_report_digest_matches_active_profile_state_recipe():
    payload = {"schema_version": 1, "status": "verified", "note": "café", "items": ["b", "a"]}
    manual = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    assert canonical_report_digest(payload) == manual

    state = no_active_profile_state()
    assert canonical_report_digest(state._content()) == state.compute_digest()


def test_artifact_digest_excludes_only_itself(tmp_path):
    _chain = _make_backup(tmp_path, name="one", manifest_id="lhbackup-shared", payload="alpha\n")
    report = json.loads(_chain["verification"].read_text(encoding="utf-8"))
    body = {k: v for k, v in report.items() if k != "artifact_digest"}
    assert report["artifact_digest"] == canonical_report_digest(body)
    assert "artifact_digest" not in body


def test_manifest_sha256_is_file_bytes_not_canonical_report_digest(tmp_path):
    chain = _make_backup(tmp_path, name="one", manifest_id="lhbackup-shared", payload="alpha\n")
    report = json.loads(chain["verification"].read_text(encoding="utf-8"))
    file_digest = sha256_file(chain["manifest"])
    assert report["manifest_sha256"] == file_digest
    assert report["manifest_sha256"] == hashlib.sha256(chain["manifest"].read_bytes()).hexdigest()
    assert report["artifact_digest"] != report["manifest_sha256"]


# -- produced reports are typed ----------------------------------------------------------


def test_verify_backup_emits_typed_bound_report(tmp_path):
    chain = _make_backup(tmp_path, name="one", manifest_id="lhbackup-shared", payload="alpha\n")
    report = json.loads(chain["verification"].read_text(encoding="utf-8"))
    parsed = BackupVerificationReport.from_dict(report)
    parsed.validate()
    assert parsed.report_schema == VERIFICATION_REPORT_SCHEMA
    assert parsed.schema_version == 1
    assert parsed.status == "verified"
    assert parsed.manifest_id == "lhbackup-shared"
    assert parsed.manifest_sha256 == sha256_file(chain["manifest"])


def test_restore_drill_emits_typed_bound_report(tmp_path):
    chain = _make_backup(tmp_path, name="one", manifest_id="lhbackup-shared", payload="alpha\n")
    report = json.loads(chain["restore"].read_text(encoding="utf-8"))
    parsed = RestoreDrillReport.from_dict(report)
    parsed.validate()
    assert parsed.report_schema == RESTORE_DRILL_REPORT_SCHEMA
    assert parsed.schema_version == 1
    assert parsed.status == "completed"
    assert parsed.manifest_sha256 == sha256_file(chain["manifest"])
    assert parsed.profile_activation_authorized is False
    assert parsed.production_restore_performed is False


def test_failed_verify_report_is_still_fully_typed(tmp_path):
    chain = _make_backup(tmp_path, name="one", manifest_id="lhbackup-shared", payload="alpha\n")
    (chain["backup"] / "payload" / "entities.json").unlink()
    report = verify_backup(chain["backup"])
    parsed = BackupVerificationReport.from_dict(report)
    parsed.validate()
    assert report["status"] == "failed"
    assert report["report_schema"] == VERIFICATION_REPORT_SCHEMA
    assert "checked_entries" in report
    assert report["manifest_sha256"] == sha256_file(chain["manifest"])


# -- closed parse + digest tamper --------------------------------------------------------


def test_verification_report_refuses_unknown_and_missing_keys():
    with pytest.raises(ReportValidationError, match="unexpected|missing"):
        BackupVerificationReport.from_dict({"status": "verified"})
    with pytest.raises(ReportValidationError, match="unexpected"):
        BackupVerificationReport.from_dict(
            {
                "report_schema": VERIFICATION_REPORT_SCHEMA,
                "schema_version": 1,
                "status": "verified",
                "backup_root": "/tmp/x",
                "manifest_id": "id",
                "manifest_sha256": "a" * 64,
                "checked_entries": 0,
                "structural_errors": [],
                "missing_payload": [],
                "digest_mismatches": [],
                "extra_payload": [],
                "sqlite_integrity": {},
                "blocking_failures": [],
                "warnings": [],
                "artifact_digest": "b" * 64,
                "importance": "high",
            }
        )


def test_changing_one_report_field_without_recomputing_digest_refuses(tmp_path):
    chain = _make_backup(tmp_path, name="one", manifest_id="lhbackup-shared", payload="alpha\n")
    report = json.loads(chain["verification"].read_text(encoding="utf-8"))
    report["warnings"] = ["edited"]
    with pytest.raises(ReportValidationError, match="artifact_digest"):
        BackupVerificationReport.from_dict(report).validate()


def test_restamped_digest_with_wrong_manifest_sha256_refuses_consumption(tmp_path):
    chain = _make_backup(tmp_path, name="one", manifest_id="lhbackup-shared", payload="alpha\n")
    report = json.loads(chain["verification"].read_text(encoding="utf-8"))
    report["manifest_sha256"] = "0" * 64
    report = _stamp(report)
    BackupVerificationReport.from_dict(report).validate()
    with pytest.raises(ReportValidationError, match="manifest_sha256"):
        validate_supplied_verification_report(
            report,
            backup_root=chain["backup"],
            manifest_id="lhbackup-shared",
        )


def test_same_manifest_id_different_bytes_refuses_swapped_verification(tmp_path):
    a = _make_backup(tmp_path, name="a", manifest_id="lhbackup-shared", payload="alpha-bytes\n")
    b = _make_backup(tmp_path, name="b", manifest_id="lhbackup-shared", payload="beta-bytes\n")
    assert (
        json.loads(a["manifest"].read_text())["manifest_id"]
        == json.loads(b["manifest"].read_text())["manifest_id"]
    )
    assert sha256_file(a["manifest"]) != sha256_file(b["manifest"])
    a_verify = json.loads(a["verification"].read_text(encoding="utf-8"))
    a_verify["backup_root"] = str(b["backup"])
    a_verify = _stamp(a_verify)
    with pytest.raises(ReportValidationError, match="manifest_sha256"):
        validate_supplied_verification_report(
            a_verify,
            backup_root=b["backup"],
            manifest_id="lhbackup-shared",
        )


def test_same_manifest_id_different_bytes_refuses_swapped_restore(tmp_path):
    a = _make_backup(tmp_path, name="a", manifest_id="lhbackup-shared", payload="alpha-bytes\n")
    b = _make_backup(tmp_path, name="b", manifest_id="lhbackup-shared", payload="beta-bytes\n")
    a_restore = json.loads(a["restore"].read_text(encoding="utf-8"))
    a_restore["backup_root"] = str(b["backup"])
    a_restore = _stamp(a_restore)
    with pytest.raises(ReportValidationError, match="manifest_sha256"):
        validate_supplied_restore_drill_report(
            a_restore,
            backup_root=b["backup"],
            manifest_id="lhbackup-shared",
            source_data_root=b["data_dir"],
        )


def test_load_evidence_refuses_status_only_reports(tmp_path):
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps({"manifest_id": "lhbackup-xyz", "created_at": "2026-07-10T00:00:00+00:00"}),
        encoding="utf-8",
    )
    verification = tmp_path / "verification.json"
    verification.write_text(json.dumps({"status": "verified"}), encoding="utf-8")
    restore = tmp_path / "restore.json"
    restore.write_text(json.dumps({"status": "completed"}), encoding="utf-8")
    with pytest.raises(TransitionEvidenceError):
        load_evidence(manifest, verification, restore)


def test_load_evidence_refuses_verification_from_other_manifest(tmp_path):
    a = _make_backup(tmp_path, name="a", manifest_id="lhbackup-shared", payload="alpha-bytes\n")
    b = _make_backup(tmp_path, name="b", manifest_id="lhbackup-shared", payload="beta-bytes\n")
    with pytest.raises(TransitionEvidenceError, match="manifest"):
        load_evidence(b["manifest"], a["verification"], b["restore"])


def test_passed_create_time_status_does_not_make_preview_ready():
    ev = TransitionEvidence(
        backup_manifest_id="lhbackup-abc123",
        backup_manifest_sha256="a" * 64,
        backup_verification_status="passed",
        restore_drill_status="completed",
        evidence_timestamp="2026-07-10T00:00:00+00:00",
    )
    preview = build_transition_preview(target_profile=_profile(), evidence=ev)
    assert preview.transition_status != STATUS_READY
    assert preview.transition_status == STATUS_BLOCKED


def test_verification_schema_mirror_pins_contract():
    mirror = json.loads(
        Path("docs/schemas/lighthouse_backup_verification.v1.schema.json").read_text(
            encoding="utf-8"
        )
    )
    assert set(mirror["required"]) == VERIFY_BACKUP_REPORT_KEYS
    assert set(mirror["properties"]) == VERIFY_BACKUP_REPORT_KEYS
    assert mirror["additionalProperties"] is False
    assert mirror["$id"] == VERIFICATION_REPORT_SCHEMA


def test_restore_schema_mirror_pins_contract():
    mirror = json.loads(
        Path("docs/schemas/lighthouse_restore_drill.v1.schema.json").read_text(encoding="utf-8")
    )
    assert set(mirror["required"]) == RESTORE_DRILL_REPORT_KEYS
    assert set(mirror["properties"]) == RESTORE_DRILL_REPORT_KEYS
    assert mirror["additionalProperties"] is False
    assert mirror["$id"] == RESTORE_DRILL_REPORT_SCHEMA


def test_verified_typed_evidence_still_makes_preview_ready(tmp_path):
    chain = _make_backup(tmp_path, name="one", manifest_id="lhbackup-ready", payload="ready\n")
    ev = load_evidence(chain["manifest"], chain["verification"], chain["restore"])
    preview = build_transition_preview(target_profile=_profile(), evidence=ev)
    assert preview.transition_status == STATUS_READY
    assert ev.backup_verification_status == "verified"
