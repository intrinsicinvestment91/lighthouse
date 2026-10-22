"""Tests for the deterministic profile-transition preview contract (LHP-PROFILE-03).

Temp fixtures only. No real data/state.db, no network, no profile activation. The preview is
an evidence/review artifact; it must fail closed and NEVER reach ready_for_review without
present, passing backup evidence and two valid profiles.
"""

from __future__ import annotations

import hashlib
import json

import pytest

from modules.backup_manifest import sha256_file
from modules.backup_verify import (
    REPORT_SCHEMA_VERSION,
    RESTORE_DRILL_REPORT_SCHEMA,
    VERIFICATION_REPORT_SCHEMA,
    BackupVerificationReport,
    RestoreDrillReport,
)
from modules.profile_transition import (
    DETERMINISTIC_SERIALIZATION_VERSION,
    DIGEST_ALGORITHM,
    GENERATOR_VERSION,
    PROFILE_TRANSITION_PREVIEW_SCHEMA_VERSION,
    RESTORE_DRILL_PASS_STATUS,
    STATUS_BLOCKED,
    STATUS_INCOMPLETE,
    STATUS_INVALID,
    STATUS_READY,
    TARGET_POLICY_SNAPSHOT_KEYS,
    VERIFICATION_PASS_STATUS,
    ProfileTransitionError,
    ProfileTransitionPreview,
    TransitionEvidence,
    TransitionEvidenceError,
    build_transition_preview,
    compare_profiles,
    generate_transition_preview_from_registry,
    load_evidence,
)
from modules.profiles import IntelligenceProfile


def _write_typed_bound_reports(tmp_path, manifest_path):
    """Write typed verify/restore reports bound to the current manifest file bytes."""
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    digest = sha256_file(manifest_path)
    verify = BackupVerificationReport(
        report_schema=VERIFICATION_REPORT_SCHEMA,
        schema_version=REPORT_SCHEMA_VERSION,
        status="verified",
        backup_root=str(tmp_path / "bound-backup"),
        manifest_id=manifest["manifest_id"],
        manifest_sha256=digest,
        checked_entries=0,
        structural_errors=[],
        missing_payload=[],
        digest_mismatches=[],
        extra_payload=[],
        sqlite_integrity={},
        blocking_failures=[],
        warnings=[],
    )
    verify.artifact_digest = verify.compute_digest()
    restore = RestoreDrillReport(
        report_schema=RESTORE_DRILL_REPORT_SCHEMA,
        schema_version=REPORT_SCHEMA_VERSION,
        status="completed",
        backup_root=str(tmp_path / "bound-backup"),
        restore_destination=str(tmp_path / "bound-restore"),
        manifest_id=manifest["manifest_id"],
        manifest_sha256=digest,
        manifest_valid=True,
        payload_verified=True,
        files_restored=1,
        restored_digests_verified=True,
        sqlite_integrity_verified=True,
        source_unchanged=True,
        production_restore_performed=False,
        profile_activation_authorized=False,
        blocking_failures=[],
        warnings=[],
        unproven_guarantees=[],
        note="typed fixture",
    )
    restore.artifact_digest = restore.compute_digest()
    verification_path = tmp_path / "verification.json"
    restore_path = tmp_path / "restore.json"
    verification_path.write_text(json.dumps(verify.to_dict()), encoding="utf-8")
    restore_path.write_text(json.dumps(restore.to_dict()), encoding="utf-8")
    return verification_path, restore_path


# -- fixtures ----------------------------------------------------------------------------


def _profile(profile_id="target_profile", **overrides) -> IntelligenceProfile:
    base = {
        "profile_id": profile_id,
        "profile_version": 1,
        "display_name": overrides.pop("display_name", "Target Profile"),
        "status": overrides.pop("status", "experimental"),
        "source_policy": {"allowed_source_types": ["repo_source_pack"]},
        "collector_policy": {"enabled_collectors": ["repository_intake"]},
        "graph_policy": {"enabled": True},
        "correlation_policy": {"enabled": True, "priority": "normal"},
        "privacy_policy": {"local_only": True},
        "publishing_policy": {"public_publishing": False},
        "backup_policy": {
            "backup_required_on_activation": True,
            "data_root": "/data/profiles/target_profile",
        },
        "dead_mans_switch_policy": {"eligible": False},
    }
    base.update(overrides)
    p = IntelligenceProfile.from_dict(base)
    p.validate()
    return p


# Any 64-lowercase-hex value is a syntactically valid manifest content digest for fixtures.
FAKE_MANIFEST_SHA = "0123456789abcdef" * 4


def _passing_evidence() -> TransitionEvidence:
    return TransitionEvidence(
        backup_manifest_id="lhbackup-abc123",
        backup_manifest_sha256=FAKE_MANIFEST_SHA,
        backup_verification_status=VERIFICATION_PASS_STATUS,
        restore_drill_status=RESTORE_DRILL_PASS_STATUS,
        evidence_timestamp="2026-07-10T00:00:00+00:00",
    )


# -- contract: creation, validation, schema, digest -------------------------------------


def test_valid_preview_is_ready_for_review():
    preview = build_transition_preview(
        target_profile=_profile("target_profile"),
        current_profile=_profile("current_profile", display_name="Current Profile"),
        evidence=_passing_evidence(),
    )
    assert preview.transition_status == STATUS_READY
    assert preview.blocking_reasons == []


def test_valid_preview_validates():
    preview = build_transition_preview(target_profile=_profile(), evidence=_passing_evidence())
    preview.validate()  # must not raise


def test_unknown_schema_version_rejected():
    preview = build_transition_preview(target_profile=_profile(), evidence=_passing_evidence())
    preview.schema_version = PROFILE_TRANSITION_PREVIEW_SCHEMA_VERSION + 1
    with pytest.raises(ProfileTransitionError):
        preview.validate()


def test_serialization_is_deterministic_across_builds():
    # Two independent builds of the same transition + evidence must share an artifact_digest,
    # even though preview_id and created_at differ per build.
    kwargs = dict(
        target_profile=_profile("target_profile"),
        current_profile=_profile("current_profile", display_name="Current Profile"),
        evidence=_passing_evidence(),
    )
    a = build_transition_preview(**kwargs)
    b = build_transition_preview(**kwargs)
    assert a.preview_id != b.preview_id
    assert a.artifact_digest == b.artifact_digest


def test_digest_is_sha256_hex():
    preview = build_transition_preview(target_profile=_profile(), evidence=_passing_evidence())
    assert preview.digest_algorithm == DIGEST_ALGORITHM
    assert len(preview.artifact_digest) == 64
    int(preview.artifact_digest, 16)  # hex


def test_to_dict_from_dict_roundtrip():
    preview = build_transition_preview(target_profile=_profile(), evidence=_passing_evidence())
    restored = ProfileTransitionPreview.from_dict(preview.to_dict())
    assert restored.to_dict() == preview.to_dict()


def test_dict_omits_no_required_identity_fields():
    d = build_transition_preview(target_profile=_profile(), evidence=_passing_evidence()).to_dict()
    for key in (
        "schema_version",
        "preview_id",
        "created_at",
        "generator_version",
        "target_profile_id",
        "transition_status",
        "artifact_digest",
        "deterministic_serialization_version",
    ):
        assert key in d


# -- comparison --------------------------------------------------------------------------


def test_identical_profiles_report_no_changes():
    same = _profile("same_profile")
    other = _profile("same_profile")
    diff = compare_profiles(same, other)
    for dimension in diff.values():
        assert dimension["changed"] is False


def test_changed_collectors_detected():
    current = _profile("p", collector_policy={"enabled_collectors": ["a"]})
    target = _profile("p", collector_policy={"enabled_collectors": ["a", "b"]})
    diff = compare_profiles(current, target)
    assert diff["collectors"]["changed"] is True
    assert "b" in diff["collectors"]["added"]


def test_changed_sources_detected():
    current = _profile("p", source_policy={"allowed_source_types": ["x"]})
    target = _profile("p", source_policy={"allowed_source_types": ["y"]})
    diff = compare_profiles(current, target)
    assert diff["sources"]["changed"] is True
    assert "y" in diff["sources"]["added"]
    assert "x" in diff["sources"]["removed"]


def test_changed_privacy_policy_detected():
    current = _profile("p", privacy_policy={"local_only": True})
    target = _profile("p", privacy_policy={"local_only": False})
    diff = compare_profiles(current, target)
    assert diff["privacy"]["changed"] is True
    assert diff["local_only"]["changed"] is True


def test_changed_publishing_policy_detected():
    current = _profile("p", publishing_policy={"public_publishing": False})
    # A public-publishing profile cannot be local_only, so relax privacy too.
    target = _profile(
        "p",
        publishing_policy={"public_publishing": True},
        privacy_policy={"local_only": False},
    )
    diff = compare_profiles(current, target)
    assert diff["publishing"]["changed"] is True


def test_changed_dms_eligibility_detected():
    current = _profile("p", dead_mans_switch_policy={"eligible": False})
    target = _profile("p", dead_mans_switch_policy={"eligible": True})
    diff = compare_profiles(current, target)
    assert diff["dms_eligibility"]["changed"] is True
    assert diff["dms_eligibility"]["to"] is True


def test_changed_correlation_rules_detected():
    current = _profile("p", correlation_policy={"enabled": True, "priority": "low"})
    target = _profile("p", correlation_policy={"enabled": True, "priority": "high"})
    diff = compare_profiles(current, target)
    assert diff["correlation"]["changed"] is True


def test_comparison_against_no_current_profile_marks_introductions():
    # No active profile yet (activation not implemented) -> everything is introduced.
    diff = compare_profiles(None, _profile("p"))
    assert diff["collectors"]["changed"] is True
    assert diff["collectors"]["removed"] == []


# -- evidence handling -------------------------------------------------------------------


def test_missing_all_evidence_is_incomplete():
    preview = build_transition_preview(target_profile=_profile(), evidence=None)
    assert preview.transition_status == STATUS_INCOMPLETE
    assert preview.blocking_reasons  # non-empty


def test_partial_evidence_is_incomplete():
    ev = TransitionEvidence(
        backup_manifest_id="lhbackup-abc123",
        backup_verification_status=VERIFICATION_PASS_STATUS,
        restore_drill_status=None,  # restore drill not performed
        evidence_timestamp="2026-07-10T00:00:00+00:00",
    )
    preview = build_transition_preview(target_profile=_profile(), evidence=ev)
    assert preview.transition_status == STATUS_INCOMPLETE


def test_backup_create_embedded_report_status_is_not_ready():
    # backup-create writes reports/verification.json with status "passed". That embed is
    # a different contract and must not make a transition preview ready_for_review.
    ev = TransitionEvidence(
        backup_manifest_id="lhbackup-abc123",
        backup_manifest_sha256=FAKE_MANIFEST_SHA,
        backup_verification_status="passed",
        restore_drill_status=RESTORE_DRILL_PASS_STATUS,
        evidence_timestamp="2026-07-10T00:00:00+00:00",
    )
    preview = build_transition_preview(target_profile=_profile(), evidence=ev)
    assert preview.transition_status == STATUS_BLOCKED


def test_passed_verification_warns_and_is_not_ready():
    ev = TransitionEvidence(
        backup_manifest_id="lhbackup-abc123",
        backup_manifest_sha256=FAKE_MANIFEST_SHA,
        backup_verification_status="passed",
        restore_drill_status=RESTORE_DRILL_PASS_STATUS,
        evidence_timestamp="2026-07-10T00:00:00+00:00",
    )
    preview = build_transition_preview(target_profile=_profile(), evidence=ev)
    assert preview.transition_status == STATUS_BLOCKED
    joined = " ".join(preview.warnings)
    assert "backup-verify" in joined
    assert "verified" in joined
    assert "profile-activate" in joined


def test_failed_verification_is_blocked():
    ev = TransitionEvidence(
        backup_manifest_id="lhbackup-abc123",
        backup_manifest_sha256=FAKE_MANIFEST_SHA,
        backup_verification_status="failed",
        restore_drill_status=RESTORE_DRILL_PASS_STATUS,
        evidence_timestamp="2026-07-10T00:00:00+00:00",
    )
    preview = build_transition_preview(target_profile=_profile(), evidence=ev)
    assert preview.transition_status == STATUS_BLOCKED


def test_failed_restore_drill_is_blocked():
    ev = TransitionEvidence(
        backup_manifest_id="lhbackup-abc123",
        backup_manifest_sha256=FAKE_MANIFEST_SHA,
        backup_verification_status=VERIFICATION_PASS_STATUS,
        restore_drill_status="failed",
        evidence_timestamp="2026-07-10T00:00:00+00:00",
    )
    preview = build_transition_preview(target_profile=_profile(), evidence=ev)
    assert preview.transition_status == STATUS_BLOCKED


def test_unknown_evidence_status_is_not_ready():
    ev = TransitionEvidence(
        backup_manifest_id="lhbackup-abc123",
        backup_manifest_sha256=FAKE_MANIFEST_SHA,
        backup_verification_status="mystery",
        restore_drill_status=RESTORE_DRILL_PASS_STATUS,
        evidence_timestamp="2026-07-10T00:00:00+00:00",
    )
    preview = build_transition_preview(target_profile=_profile(), evidence=ev)
    assert preview.transition_status != STATUS_READY


def test_load_evidence_from_reports(tmp_path):
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps({"manifest_id": "lhbackup-xyz", "created_at": "2026-07-10T00:00:00+00:00"}),
        encoding="utf-8",
    )
    verification, restore = _write_typed_bound_reports(tmp_path, manifest)

    ev = load_evidence(
        manifest_path=manifest,
        verification_report_path=verification,
        restore_report_path=restore,
    )
    assert ev.backup_manifest_id == "lhbackup-xyz"
    assert ev.backup_verification_status == "verified"
    assert ev.restore_drill_status == "completed"


def test_load_evidence_unreadable_path_fails_closed(tmp_path):
    with pytest.raises(TransitionEvidenceError):
        load_evidence(manifest_path=tmp_path / "does-not-exist.json")


def test_load_evidence_unparseable_report_fails_closed(tmp_path):
    bad = tmp_path / "manifest.json"
    bad.write_text("{ not json", encoding="utf-8")
    with pytest.raises(TransitionEvidenceError):
        load_evidence(manifest_path=bad)


# -- security / fail-closed --------------------------------------------------------------


def test_malformed_target_profile_is_invalid():
    bad = IntelligenceProfile.from_dict(
        {"profile_id": "BAD ID", "display_name": "x", "status": "experimental"}
    )
    preview = build_transition_preview(target_profile=bad, evidence=_passing_evidence())
    assert preview.transition_status == STATUS_INVALID


def test_malformed_current_profile_is_invalid():
    bad = IntelligenceProfile.from_dict(
        {"profile_id": "BAD ID", "display_name": "x", "status": "experimental"}
    )
    preview = build_transition_preview(
        target_profile=_profile(), current_profile=bad, evidence=_passing_evidence()
    )
    assert preview.transition_status == STATUS_INVALID


def test_ready_requires_evidence_invariant():
    # No combination of a valid profile + absent evidence may yield ready_for_review.
    for ev in (None, TransitionEvidence()):
        preview = build_transition_preview(target_profile=_profile(), evidence=ev)
        assert preview.transition_status != STATUS_READY


def test_from_registry_unknown_profile_rejected():
    with pytest.raises(ProfileTransitionError):
        generate_transition_preview_from_registry(
            target_profile_id="definitely_not_a_real_profile_xyz",
        )


def test_from_registry_reads_bundled_target_and_stays_non_ready_without_evidence():
    # Real bundled profile resolves, but with no evidence the preview must not be ready.
    preview = generate_transition_preview_from_registry(
        target_profile_id="scientific_preservation",
    )
    assert preview.target_profile_id == "scientific_preservation"
    assert preview.transition_status != STATUS_READY


def test_required_conditions_present_when_ready():
    preview = build_transition_preview(
        target_profile=_profile("target_profile"),
        current_profile=_profile("current_profile", display_name="Current Profile"),
        evidence=_passing_evidence(),
    )
    assert preview.required_operator_approvals
    assert preview.required_backup_evidence
    assert preview.required_security_conditions


def test_privacy_downgrade_requires_operator_approval():
    current = _profile("p", privacy_policy={"local_only": True})
    target = _profile(
        "p",
        privacy_policy={"local_only": False},
        publishing_policy={"public_publishing": True},
    )
    preview = build_transition_preview(
        target_profile=target, current_profile=current, evidence=_passing_evidence()
    )
    joined = " ".join(preview.required_operator_approvals).lower()
    assert "privacy" in joined or "publishing" in joined


# -- producer-owned execution facts (LHP-PROFILE-14A) --------------------------------------


def test_contract_constants_reflect_serialization_v2():
    assert DETERMINISTIC_SERIALIZATION_VERSION == 2
    assert GENERATOR_VERSION == "1.1.0"
    preview = build_transition_preview(target_profile=_profile(), evidence=_passing_evidence())
    assert preview.deterministic_serialization_version == 2


def test_target_policy_snapshot_derived_verbatim_from_target():
    preview = build_transition_preview(target_profile=_profile(), evidence=_passing_evidence())
    assert preview.target_policy_snapshot == {
        "local_only": True,
        "dms_eligibility": False,
        "data_root": "/data/profiles/target_profile",
        "schema_migration_required": False,
    }


def test_target_policy_snapshot_carries_exactly_the_contract_keys():
    preview = build_transition_preview(target_profile=_profile(), evidence=_passing_evidence())
    assert set(preview.target_policy_snapshot) == set(TARGET_POLICY_SNAPSHOT_KEYS)


def test_snapshot_reflects_dms_eligible_target():
    target = _profile("p", dead_mans_switch_policy={"eligible": True})
    preview = build_transition_preview(target_profile=target, evidence=_passing_evidence())
    assert preview.target_policy_snapshot["dms_eligibility"] is True


def test_snapshot_and_profile_digest_absent_when_target_invalid():
    bad = IntelligenceProfile.from_dict(
        {"profile_id": "BAD ID", "display_name": "x", "status": "experimental"}
    )
    preview = build_transition_preview(target_profile=bad, evidence=_passing_evidence())
    assert preview.target_policy_snapshot is None
    assert preview.target_profile_sha256 is None
    assert preview.transition_status == STATUS_INVALID


def test_new_fields_serialized_at_documented_locations():
    d = build_transition_preview(target_profile=_profile(), evidence=_passing_evidence()).to_dict()
    for key in (
        "target_policy_snapshot",
        "target_profile_sha256",
        "lighthouse_version",
        "repository_commit",
    ):
        assert key in d
    assert "backup_manifest_sha256" in d["evidence"]


def test_changes_block_serializes_all_eight_categories():
    d = build_transition_preview(target_profile=_profile(), evidence=_passing_evidence()).to_dict()
    assert set(d["changes"]) == {
        "collectors_changed",
        "sources_changed",
        "graph_entities_changed",
        "correlation_policy_changed",
        "publishing_policy_changed",
        "privacy_policy_changed",
        "local_only_changed",
        "dms_eligibility_changed",
    }
    # Foundation transition (no current profile): local_only is introduced, DMS stays False.
    assert d["changes"]["local_only_changed"] == {"changed": True, "from": False, "to": True}
    assert d["changes"]["dms_eligibility_changed"] == {"changed": False, "from": False, "to": False}


def test_digest_covers_each_new_field():
    base = build_transition_preview(target_profile=_profile(), evidence=_passing_evidence())
    original = base.compute_digest()
    mutations = [
        ("target_policy_snapshot", {**base.target_policy_snapshot, "local_only": False}),
        ("target_profile_sha256", "e" * 64),
        ("lighthouse_version", "9.9.9-mutated"),
        ("repository_commit", "d" * 40),
        ("backup_manifest_sha256", "c" * 64),
        ("local_only_changed", {"changed": True, "from": True, "to": False}),
        ("dms_eligibility_changed", {"changed": True, "from": False, "to": True}),
    ]
    for field_name, new_value in mutations:
        mutated = ProfileTransitionPreview.from_dict(base.to_dict())
        setattr(mutated, field_name, new_value)
        assert mutated.compute_digest() != original, field_name


def test_volatile_fields_stay_digest_excluded():
    # preview_id/created_at/evidence_timestamp are volatile; the digest must not move.
    base = build_transition_preview(target_profile=_profile(), evidence=_passing_evidence())
    twin = ProfileTransitionPreview.from_dict(base.to_dict())
    twin.preview_id = "lhtransition-different"
    twin.created_at = "2030-01-01T00:00:00+00:00"
    twin.evidence_timestamp = "2030-01-01T00:00:00+00:00"
    assert twin.compute_digest() == base.compute_digest()


def test_future_serialization_version_rejected():
    preview = build_transition_preview(target_profile=_profile(), evidence=_passing_evidence())
    preview.deterministic_serialization_version = DETERMINISTIC_SERIALIZATION_VERSION + 1
    with pytest.raises(ProfileTransitionError):
        preview.validate()


def _legacy_v1_artifact(status: str) -> dict:
    """A pre-14A (serialization v1) artifact dict, as old code would have written it."""
    return {
        "schema_version": 1,
        "preview_id": "lhtransition-legacy",
        "created_at": "2026-07-01T00:00:00+00:00",
        "generator_version": "1.0.0",
        "deterministic_serialization_version": 1,
        "current_profile_id": None,
        "current_profile_version": None,
        "target_profile_id": "p",
        "target_profile_version": 1,
        "transition_status": status,
        "changes": {},
        "evidence": {
            "backup_manifest_id": "lhbackup-legacy",
            "backup_verification_status": "verified",
            "restore_drill_status": "completed",
            "evidence_timestamp": None,
        },
        "requirements": {},
        "warnings": [],
        "blocking_reasons": [],
        "artifact_digest": "a" * 64,
        "digest_algorithm": "sha256",
    }


def test_legacy_v1_non_ready_artifact_still_validates():
    # Additive evolution: old artifacts that are NOT ready keep loading and validating.
    restored = ProfileTransitionPreview.from_dict(_legacy_v1_artifact(STATUS_INCOMPLETE))
    restored.validate()
    assert restored.target_policy_snapshot is None
    assert restored.deterministic_serialization_version == 1


def test_legacy_v1_ready_artifact_fails_closed():
    # Intentional, unconditional strictening: ready_for_review now REQUIRES the producer
    # facts. Gating this on the self-attested serialization version would be a permanent
    # forgeable downgrade knob; previews are ephemeral — regenerate instead.
    with pytest.raises(ProfileTransitionError):
        ProfileTransitionPreview.from_dict(_legacy_v1_artifact(STATUS_READY)).validate()


def test_ready_requires_each_producer_fact():
    preview = build_transition_preview(target_profile=_profile(), evidence=_passing_evidence())
    assert preview.transition_status == STATUS_READY
    for field_name in (
        "target_policy_snapshot",
        "backup_manifest_sha256",
        "target_profile_sha256",
    ):
        clone = ProfileTransitionPreview.from_dict(preview.to_dict())
        setattr(clone, field_name, None)
        with pytest.raises(ProfileTransitionError):
            clone.validate()


def test_snapshot_with_unknown_or_missing_keys_rejected():
    preview = build_transition_preview(target_profile=_profile(), evidence=_passing_evidence())
    extra = ProfileTransitionPreview.from_dict(preview.to_dict())
    extra.target_policy_snapshot = {**extra.target_policy_snapshot, "smuggled": True}
    with pytest.raises(ProfileTransitionError):
        extra.validate()
    short = ProfileTransitionPreview.from_dict(preview.to_dict())
    short.target_policy_snapshot = {"local_only": True}
    with pytest.raises(ProfileTransitionError):
        short.validate()


def test_snapshot_booleans_must_be_real_booleans():
    # An int 1 must not pass for True in a fail-closed contract check.
    preview = build_transition_preview(target_profile=_profile(), evidence=_passing_evidence())
    clone = ProfileTransitionPreview.from_dict(preview.to_dict())
    clone.target_policy_snapshot = {**clone.target_policy_snapshot, "local_only": 1}
    with pytest.raises(ProfileTransitionError):
        clone.validate()


def test_malformed_content_digest_fields_rejected():
    preview = build_transition_preview(target_profile=_profile(), evidence=_passing_evidence())
    for field_name in ("backup_manifest_sha256", "target_profile_sha256"):
        for bad in ("abc", "g" * 64, "0123456789ABCDEF" * 4):
            clone = ProfileTransitionPreview.from_dict(preview.to_dict())
            setattr(clone, field_name, bad)
            with pytest.raises(ProfileTransitionError):
                clone.validate()


def test_target_profile_digest_recomputable_from_profile_content():
    target = _profile()
    preview = build_transition_preview(target_profile=target, evidence=_passing_evidence())
    canonical = json.dumps(target.to_dict(), sort_keys=True, separators=(",", ":"))
    assert preview.target_profile_sha256 == hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def test_target_profile_digest_covers_undiffed_policy_blocks():
    # compare_profiles does not diff observation/temporal/interface policy; the profile
    # content digest (and therefore the preview digest) must still expose such drift.
    a = build_transition_preview(target_profile=_profile(), evidence=_passing_evidence())
    b = build_transition_preview(
        target_profile=_profile(observation_policy={"max_events": 10}),
        evidence=_passing_evidence(),
    )
    assert a.target_profile_sha256 != b.target_profile_sha256
    assert a.artifact_digest != b.artifact_digest


def test_provenance_stamps_come_from_detection_helpers(monkeypatch):
    import modules.profile_transition as pt

    monkeypatch.setattr(pt, "detect_lighthouse_version", lambda: "1.2.3")
    monkeypatch.setattr(pt, "detect_repository_commit", lambda: "c" * 40)
    preview = build_transition_preview(target_profile=_profile(), evidence=_passing_evidence())
    assert preview.lighthouse_version == "1.2.3"
    assert preview.repository_commit == "c" * 40
    preview.validate()


def test_provenance_stamps_tolerate_unavailable_environment(monkeypatch):
    # No dist metadata / no git (deployed s9pk): stamps are None and never block readiness.
    import modules.profile_transition as pt

    monkeypatch.setattr(pt, "detect_lighthouse_version", lambda: None)
    monkeypatch.setattr(pt, "detect_repository_commit", lambda: None)
    preview = build_transition_preview(target_profile=_profile(), evidence=_passing_evidence())
    assert preview.transition_status == STATUS_READY
    preview.validate()


def test_load_evidence_computes_manifest_content_digest(tmp_path):
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"manifest_id": "lhbackup-xyz"}), encoding="utf-8")
    ev = load_evidence(manifest_path=manifest)
    assert ev.backup_manifest_sha256 == hashlib.sha256(manifest.read_bytes()).hexdigest()


def test_manifest_tamper_under_same_id_changes_preview_digest(tmp_path):
    # Id-only binding would miss a byte-swap that keeps manifest_id; the content digest
    # must surface it in both the evidence block and the preview artifact_digest.
    manifest = tmp_path / "manifest.json"

    def _preview() -> ProfileTransitionPreview:
        ev = load_evidence(manifest_path=manifest)
        return build_transition_preview(
            target_profile=_profile(),
            evidence=TransitionEvidence(
                backup_manifest_id=ev.backup_manifest_id,
                backup_manifest_sha256=ev.backup_manifest_sha256,
                backup_verification_status=VERIFICATION_PASS_STATUS,
                restore_drill_status=RESTORE_DRILL_PASS_STATUS,
            ),
        )

    manifest.write_text(json.dumps({"manifest_id": "lhbackup-xyz"}), encoding="utf-8")
    before = _preview()
    manifest.write_text(
        json.dumps({"manifest_id": "lhbackup-xyz", "entries": ["tampered"]}), encoding="utf-8"
    )
    after = _preview()
    assert before.backup_manifest_sha256 != after.backup_manifest_sha256
    assert before.artifact_digest != after.artifact_digest


def test_absent_manifest_leaves_content_digest_none():
    ev = load_evidence()
    assert ev.backup_manifest_sha256 is None
    assert not ev.is_complete()


def test_evidence_without_manifest_digest_is_incomplete():
    # Hand-built evidence lacking the content fingerprint can never look complete.
    ev = TransitionEvidence(
        backup_manifest_id="lhbackup-abc123",
        backup_verification_status=VERIFICATION_PASS_STATUS,
        restore_drill_status=RESTORE_DRILL_PASS_STATUS,
    )
    preview = build_transition_preview(target_profile=_profile(), evidence=ev)
    assert preview.transition_status == STATUS_INCOMPLETE


def test_gateway_consumer_contract_keys_and_frozen_vocabulary():
    """Pin every preview key path and prose token the SHIPPED Gateway consumers read.

    The typed snapshot retires the Gateway's string-token recovery only when the Gateway
    companion change lands (PROFILE-14B). Until then this producer must keep emitting the
    legacy vocabulary VERBATIM — do not rewrite these strings or key paths.
    """
    preview = build_transition_preview(
        target_profile=_profile("target_profile"),
        current_profile=_profile(
            "current_profile",
            display_name="Current Profile",
            dead_mans_switch_policy={"eligible": True},
        ),
        evidence=_passing_evidence(),
    )
    d = preview.to_dict()
    # Structural binding keys (P-04 approval, P-05 intake, P-07 plan, P-11 authorization).
    for key in (
        "schema_version",
        "preview_id",
        "created_at",
        "artifact_digest",
        "target_profile_id",
        "target_profile_version",
        "transition_status",
    ):
        assert key in d
    assert d["schema_version"] == 1
    # Foundation detection (P-07/P-11): both keys present; null current = foundation.
    assert "current_profile_id" in d and "current_profile_version" in d
    # Evidence keys + closed pass-token sets (P-05/P-07 frozensets).
    for key in (
        "backup_manifest_id",
        "backup_verification_status",
        "restore_drill_status",
        "evidence_timestamp",
    ):
        assert key in d["evidence"]
    assert d["evidence"]["backup_verification_status"] == "verified"
    assert d["evidence"]["restore_drill_status"] == "completed"
    # The six legacy change categories, each with a real-bool `changed` (P-07 classifier).
    for key in (
        "collectors_changed",
        "sources_changed",
        "graph_entities_changed",
        "correlation_policy_changed",
        "publishing_policy_changed",
        "privacy_policy_changed",
    ):
        assert isinstance(d["changes"][key].get("changed"), bool)
    # P-11 reads changes.privacy_policy_changed.to.local_only directly.
    assert d["changes"]["privacy_policy_changed"]["to"].get("local_only") is True
    # P-11 token-recovery vocabulary — FROZEN verbatim until the 14B Gateway companion.
    assert (
        "operator approval: dead-man's-switch eligibility change"
        in d["requirements"]["required_operator_approvals"]
    )
    assert any(
        condition.startswith("target data_root resolves under /data (")
        for condition in d["requirements"]["required_security_conditions"]
    )
