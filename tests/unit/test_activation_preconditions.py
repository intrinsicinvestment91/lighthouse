"""Tests for modules/activation_preconditions.py (LHP-PROFILE-14B).

Must prove: unknown/missing/failed inputs never become ready; malformed inputs are
invalid; the evaluator is deterministic, non-raising on content, read-only, and
activates nothing.
"""

import dataclasses

import pytest

from modules import activation_preconditions as ap
from modules.activation_preconditions import (
    ActivationPreconditionsResult,
    evaluate_activation_preconditions,
    evaluate_activation_preconditions_from_registry,
)
from modules.active_profile_state import (
    ActivationEvidenceReferences,
    build_active_profile_state,
    no_active_profile_state,
)
from modules.profile_registry import get_profile
from modules.profile_transition import (
    TransitionEvidence,
    build_transition_preview,
    profile_content_sha256,
)
from modules.profiles import IntelligenceProfile

FAKE_MANIFEST_SHA = "0123456789abcdef" * 4
TZ_TS = "2026-07-12T12:00:00+00:00"
SOURCE = "operator_approved_transition"
TEST_EVIDENCE = ActivationEvidenceReferences(
    transition_preview_id="preview-preconditions-test",
    transition_preview_digest="1" * 64,
    backup_manifest_id="lhbackup-preconditions-test",
    backup_manifest_sha256=FAKE_MANIFEST_SHA,
    verification_report_digest="3" * 64,
    restore_report_digest="4" * 64,
    source_data_root="/tmp/preconditions-data",
    backup_root="/tmp/preconditions-backup",
)


def _profile(profile_id: str = "target_profile", **overrides) -> IntelligenceProfile:
    base = {
        "profile_id": profile_id,
        "profile_version": overrides.pop("profile_version", 1),
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
            "data_root": f"/data/profiles/{profile_id}",
        },
        "dead_mans_switch_policy": {"eligible": False},
    }
    base.update(overrides)
    profile = IntelligenceProfile.from_dict(base)
    profile.validate()
    return profile


def _passing_evidence() -> TransitionEvidence:
    return TransitionEvidence(
        backup_manifest_id="lhbackup-0001",
        backup_manifest_sha256=FAKE_MANIFEST_SHA,
        backup_verification_status="verified",
        restore_drill_status="completed",
        evidence_timestamp=TZ_TS,
    )


def _ready_preview(profile: IntelligenceProfile, current: IntelligenceProfile | None = None):
    return build_transition_preview(
        profile, current_profile=current, evidence=_passing_evidence()
    ).to_dict()


# ---------------------------------------------------------------- ready paths


def test_foundation_transition_is_ready():
    profile = _profile()
    result = evaluate_activation_preconditions(
        _ready_preview(profile),
        current_state=no_active_profile_state(),
        target_profile=profile,
    )
    assert result.decision == "ready"
    assert result.reasons == ()


def test_switch_with_matching_current_state_is_ready():
    current = _profile("current_profile")
    target = _profile()
    state = build_active_profile_state(
        current, activated_at=TZ_TS, activation_source=SOURCE, evidence=TEST_EVIDENCE
    )
    result = evaluate_activation_preconditions(
        _ready_preview(target, current=current), current_state=state, target_profile=target
    )
    assert result.decision == "ready"
    assert result.reasons == ()


def test_preview_target_sha_matches_profile_content_sha256():
    profile = _profile()
    preview = _ready_preview(profile)
    assert preview["target_profile_sha256"] == profile_content_sha256(profile)


# ---------------------------------------------------------------- blocked: preview status


def test_incomplete_preview_blocks():
    profile = _profile()
    preview = build_transition_preview(profile).to_dict()  # no evidence at all
    result = evaluate_activation_preconditions(
        preview, current_state=no_active_profile_state(), target_profile=profile
    )
    assert result.decision == "blocked"
    assert "preview_not_ready:incomplete" in result.reasons


def test_failing_verification_blocks_with_both_reasons():
    profile = _profile()
    evidence = _passing_evidence()
    evidence.backup_verification_status = "failed"
    preview = build_transition_preview(profile, evidence=evidence).to_dict()
    result = evaluate_activation_preconditions(
        preview, current_state=no_active_profile_state(), target_profile=profile
    )
    assert result.decision == "blocked"
    assert "preview_not_ready:blocked" in result.reasons
    assert "evidence_not_passing:backup_verification_status" in result.reasons


def test_failing_restore_drill_blocks():
    profile = _profile()
    evidence = _passing_evidence()
    evidence.restore_drill_status = "failed"
    preview = build_transition_preview(profile, evidence=evidence).to_dict()
    result = evaluate_activation_preconditions(
        preview, current_state=no_active_profile_state(), target_profile=profile
    )
    assert result.decision == "blocked"
    assert "evidence_not_passing:restore_drill_status" in result.reasons


# ---------------------------------------------------------------- blocked: producer facts

# A built preview with valid profiles always carries the producer facts, so a fact can only
# be absent on a non-ready artifact (e.g. legacy/hand-built). Forcing status to "blocked"
# keeps the artifact contract-valid (non-ready previews may lack facts; the preview's own
# validate() never recomputes the digest — the Gateway-trust rule).


@pytest.mark.parametrize(
    ("fact", "token"),
    [
        ("target_policy_snapshot", "producer_fact_missing:target_policy_snapshot"),
        ("target_profile_sha256", "producer_fact_missing:target_profile_sha256"),
    ],
)
def test_missing_producer_fact_blocks(fact, token):
    profile = _profile()
    preview = _ready_preview(profile)
    preview["transition_status"] = "blocked"
    preview[fact] = None
    result = evaluate_activation_preconditions(
        preview, current_state=no_active_profile_state(), target_profile=profile
    )
    assert result.decision == "blocked"
    assert token in result.reasons


def test_missing_manifest_fingerprint_blocks():
    profile = _profile()
    preview = _ready_preview(profile)
    preview["transition_status"] = "blocked"
    preview["evidence"]["backup_manifest_sha256"] = None
    result = evaluate_activation_preconditions(
        preview, current_state=no_active_profile_state(), target_profile=profile
    )
    assert result.decision == "blocked"
    assert "producer_fact_missing:backup_manifest_sha256" in result.reasons


def test_missing_evidence_references_block():
    profile = _profile()
    preview = _ready_preview(profile)
    preview["transition_status"] = "incomplete"
    preview["evidence"]["backup_manifest_id"] = None
    preview["evidence"]["backup_verification_status"] = None
    preview["evidence"]["restore_drill_status"] = None
    result = evaluate_activation_preconditions(
        preview, current_state=no_active_profile_state(), target_profile=profile
    )
    assert result.decision == "blocked"
    assert "evidence_missing:backup_manifest_id" in result.reasons
    assert "evidence_missing:backup_verification_status" in result.reasons
    assert "evidence_missing:restore_drill_status" in result.reasons


def test_malformed_preview_digest_blocks():
    # preview.validate() never inspects artifact_digest (the Gateway-trust rule), so the
    # artifact stays contract-valid — but nothing downstream can bind by a garbage digest.
    profile = _profile()
    preview = _ready_preview(profile)
    preview["artifact_digest"] = "NOT-A-DIGEST"
    result = evaluate_activation_preconditions(
        preview, current_state=no_active_profile_state(), target_profile=profile
    )
    assert result.decision == "blocked"
    assert "preview_digest_malformed" in result.reasons


def test_tampered_ready_preview_without_facts_is_invalid():
    # Nulling a producer fact while claiming ready violates the preview contract itself
    # (14A READY-strictening) — the evaluator reports it as structurally invalid.
    profile = _profile()
    preview = _ready_preview(profile)
    preview["target_policy_snapshot"] = None
    result = evaluate_activation_preconditions(
        preview, current_state=no_active_profile_state(), target_profile=profile
    )
    assert result.decision == "invalid"
    assert result.reasons == ("preview_malformed",)


# ---------------------------------------------------------------- blocked: target profile


def test_missing_target_profile_blocks():
    profile = _profile()
    result = evaluate_activation_preconditions(
        _ready_preview(profile),
        current_state=no_active_profile_state(),
        target_profile=None,
    )
    assert result.decision == "blocked"
    assert "target_profile_missing" in result.reasons


def test_invalid_target_profile_blocks():
    profile = _profile()
    broken = IntelligenceProfile(profile_id="target_profile", status="bogus")
    result = evaluate_activation_preconditions(
        _ready_preview(profile),
        current_state=no_active_profile_state(),
        target_profile=broken,
    )
    assert result.decision == "blocked"
    assert "target_profile_invalid" in result.reasons


def test_wrong_target_profile_blocks():
    profile = _profile()
    other = _profile("other_profile")
    result = evaluate_activation_preconditions(
        _ready_preview(profile),
        current_state=no_active_profile_state(),
        target_profile=other,
    )
    assert result.decision == "blocked"
    assert "target_profile_mismatch" in result.reasons


def test_edited_target_profile_content_blocks_on_digest():
    profile = _profile()
    preview = _ready_preview(profile)
    edited = _profile(display_name="Edited In Place")  # same id+version, different content
    result = evaluate_activation_preconditions(
        preview, current_state=no_active_profile_state(), target_profile=edited
    )
    assert result.decision == "blocked"
    assert "target_profile_digest_mismatch" in result.reasons


# ---------------------------------------------------------------- blocked: current state


def test_state_active_but_preview_declares_foundation_blocks():
    profile = _profile()
    state = build_active_profile_state(
        _profile("current_profile"),
        activated_at=TZ_TS,
        activation_source=SOURCE,
        evidence=TEST_EVIDENCE,
    )
    result = evaluate_activation_preconditions(
        _ready_preview(profile), current_state=state, target_profile=profile
    )
    assert result.decision == "blocked"
    assert "current_state_mismatch" in result.reasons


def test_state_active_with_different_profile_blocks():
    declared_current = _profile("current_profile")
    actually_active = _profile("another_profile")
    target = _profile()
    state = build_active_profile_state(
        actually_active, activated_at=TZ_TS, activation_source=SOURCE, evidence=TEST_EVIDENCE
    )
    result = evaluate_activation_preconditions(
        _ready_preview(target, current=declared_current),
        current_state=state,
        target_profile=target,
    )
    assert result.decision == "blocked"
    assert "current_state_mismatch" in result.reasons


def test_preview_declares_current_but_state_absent_blocks():
    current = _profile("current_profile")
    target = _profile()
    result = evaluate_activation_preconditions(
        _ready_preview(target, current=current),
        current_state=no_active_profile_state(),
        target_profile=target,
    )
    assert result.decision == "blocked"
    assert "current_state_mismatch" in result.reasons


# ---------------------------------------------------------------- invalid inputs


@pytest.mark.parametrize("garbage", [None, "preview", 7, [], {"nonsense": 1}])
def test_malformed_preview_is_invalid(garbage):
    result = evaluate_activation_preconditions(
        garbage, current_state=no_active_profile_state(), target_profile=_profile()
    )
    assert result.decision == "invalid"
    assert "preview_malformed" in result.reasons


def test_broken_current_state_is_invalid():
    profile = _profile()
    state = no_active_profile_state()
    state.active_profile_id = "half_state"  # inconsistent with declared absence
    result = evaluate_activation_preconditions(
        _ready_preview(profile), current_state=state, target_profile=profile
    )
    assert result.decision == "invalid"
    assert "current_state_invalid" in result.reasons


def test_wrong_type_current_state_is_invalid():
    profile = _profile()
    result = evaluate_activation_preconditions(
        _ready_preview(profile), current_state=None, target_profile=profile
    )
    assert result.decision == "invalid"
    assert "current_state_invalid" in result.reasons


def test_invalid_inputs_collect_all_reasons_in_order():
    state = no_active_profile_state()
    state.active_profile_id = "half_state"
    result = evaluate_activation_preconditions(
        {"nonsense": 1}, current_state=state, target_profile=_profile()
    )
    assert result.decision == "invalid"
    assert result.reasons == ("preview_malformed", "current_state_invalid")


# ---------------------------------------------------------------- never-ready sweep


def _degraded_scenarios():
    profile = _profile()
    ready = _ready_preview(profile)
    no_evidence = build_transition_preview(profile).to_dict()
    active_state = build_active_profile_state(
        _profile("current_profile"),
        activated_at=TZ_TS,
        activation_source=SOURCE,
        evidence=TEST_EVIDENCE,
    )
    return [
        ("malformed_preview", {"x": 1}, no_active_profile_state(), profile),
        ("not_ready_preview", no_evidence, no_active_profile_state(), profile),
        ("missing_target", ready, no_active_profile_state(), None),
        ("wrong_target", ready, no_active_profile_state(), _profile("other_profile")),
        ("edited_target", ready, no_active_profile_state(), _profile(display_name="Edited")),
        ("state_disagrees", ready, active_state, profile),
        ("wrong_type_state", ready, None, profile),
    ]


@pytest.mark.parametrize(
    ("label", "preview", "state", "target"),
    _degraded_scenarios(),
    ids=[s[0] for s in _degraded_scenarios()],
)
def test_no_degraded_input_ever_becomes_ready(label, preview, state, target):
    result = evaluate_activation_preconditions(preview, current_state=state, target_profile=target)
    assert result.decision != "ready"
    assert result.reasons  # never silently non-ready


# ---------------------------------------------------------------- behavior properties


def test_evaluation_is_deterministic():
    profile = _profile()
    preview = _ready_preview(profile)
    state = no_active_profile_state()
    first = evaluate_activation_preconditions(preview, current_state=state, target_profile=profile)
    second = evaluate_activation_preconditions(preview, current_state=state, target_profile=profile)
    assert first == second


def test_result_is_immutable_and_serializable():
    result = ActivationPreconditionsResult("blocked", ("target_profile_missing",))
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.decision = "ready"
    assert result.to_dict() == {"decision": "blocked", "reasons": ["target_profile_missing"]}


def test_evaluator_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    profile = _profile()
    evaluate_activation_preconditions(
        _ready_preview(profile),
        current_state=no_active_profile_state(),
        target_profile=profile,
    )
    assert list(tmp_path.iterdir()) == []


def test_module_has_no_activation_surface():
    for forbidden in ("activate", "activate_profile", "set_active_profile", "apply_profile"):
        assert not hasattr(ap, forbidden), forbidden


# ---------------------------------------------------------------- registry wrapper


def test_registry_wrapper_resolves_bundled_profile():
    bundled = get_profile("scientific_preservation")
    preview = _ready_preview(bundled)
    result = evaluate_activation_preconditions_from_registry(
        preview, current_state=no_active_profile_state()
    )
    assert result.decision == "ready"


def test_registry_wrapper_unknown_target_blocks():
    profile = _profile()  # not a bundled profile id
    preview = _ready_preview(profile)
    result = evaluate_activation_preconditions_from_registry(
        preview, current_state=no_active_profile_state()
    )
    assert result.decision == "blocked"
    assert "target_profile_missing" in result.reasons


def test_registry_wrapper_malformed_preview_is_invalid():
    result = evaluate_activation_preconditions_from_registry(
        "garbage", current_state=no_active_profile_state()
    )
    assert result.decision == "invalid"
