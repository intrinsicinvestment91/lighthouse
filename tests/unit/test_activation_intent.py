"""Tests for modules/activation_intent.py — activation_intent.v1 (LHP-PROFILE-14B).

Must prove: the intent embeds the true evaluator result (honest artifact), ready is
strictened (all references or fail), the authorizes-nothing pin is identity-checked,
every content field is digest-covered, tampering fails validation, and the module has
no activation surface.
"""

import pytest

from modules import activation_intent as ai
from modules.activation_intent import (
    ActivationIntent,
    ActivationIntentError,
    build_activation_intent,
    generate_activation_intent_from_registry,
)
from modules.activation_preconditions import evaluate_activation_preconditions
from modules.active_profile_state import (
    ActivationEvidenceReferences,
    build_active_profile_state,
    no_active_profile_state,
)
from modules.profile_registry import get_profile
from modules.profile_transition import TransitionEvidence, build_transition_preview
from modules.profiles import IntelligenceProfile

FAKE_MANIFEST_SHA = "0123456789abcdef" * 4
TZ_TS = "2026-07-12T12:00:00+00:00"
SOURCE = "operator_approved_transition"
TEST_EVIDENCE = ActivationEvidenceReferences(
    transition_preview_id="preview-intent-test",
    transition_preview_digest="1" * 64,
    backup_manifest_id="lhbackup-intent-test",
    backup_manifest_sha256=FAKE_MANIFEST_SHA,
    verification_report_digest="3" * 64,
    restore_report_digest="4" * 64,
    source_data_root="/tmp/intent-data",
    backup_root="/tmp/intent-backup",
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


def _ready_intent() -> ActivationIntent:
    profile = _profile()
    return build_activation_intent(
        _ready_preview(profile),
        current_state=no_active_profile_state(),
        target_profile=profile,
    )


# ---------------------------------------------------------------- honest artifact


def test_build_embeds_evaluator_result_verbatim():
    profile = _profile()
    preview = _ready_preview(profile)
    state = no_active_profile_state()
    expected = evaluate_activation_preconditions(
        preview, current_state=state, target_profile=profile
    )
    intent = build_activation_intent(preview, current_state=state, target_profile=profile)
    assert intent.preconditions_decision == expected.decision == "ready"
    assert intent.preconditions_reasons == expected.reasons == ()


def test_ready_intent_binds_every_reference():
    profile = _profile()
    preview = _ready_preview(profile)
    intent = build_activation_intent(
        preview, current_state=no_active_profile_state(), target_profile=profile
    )
    assert intent.target_profile_id == "target_profile"
    assert intent.target_profile_version == 1
    assert intent.current_profile_id is None
    assert intent.current_profile_version is None
    assert intent.target_profile_sha256 == preview["target_profile_sha256"]
    assert intent.preview_id == preview["preview_id"]
    assert intent.preview_digest == preview["artifact_digest"]
    assert intent.backup_manifest_id == "lhbackup-0001"
    assert intent.backup_manifest_sha256 == FAKE_MANIFEST_SHA
    assert intent.authorizes_activation is False
    assert intent.intent_id.startswith("lhintent-")


def test_blocked_transition_still_yields_valid_intent():
    profile = _profile()
    intent = build_activation_intent(
        _ready_preview(profile),
        current_state=no_active_profile_state(),
        target_profile=None,
    )
    assert intent.preconditions_decision == "blocked"
    assert "target_profile_missing" in intent.preconditions_reasons
    intent.validate()


def test_invalid_preview_yields_valid_invalid_intent():
    preview = {"target_profile_id": "target_profile", "target_profile_version": 1}
    intent = build_activation_intent(
        preview, current_state=no_active_profile_state(), target_profile=_profile()
    )
    assert intent.preconditions_decision == "invalid"
    assert "preview_malformed" in intent.preconditions_reasons
    intent.validate()


def test_current_pair_recorded_from_declared_state():
    current = _profile("current_profile")
    target = _profile()
    state = build_active_profile_state(
        current, activated_at=TZ_TS, activation_source=SOURCE, evidence=TEST_EVIDENCE
    )
    intent = build_activation_intent(
        _ready_preview(target, current=current), current_state=state, target_profile=target
    )
    assert intent.current_profile_id == "current_profile"
    assert intent.current_profile_version == 1
    assert intent.preconditions_decision == "ready"


def test_malformed_preview_sha_is_dropped_not_carried():
    # The preview contract never inspects its own artifact_digest (Gateway-trust rule),
    # so this artifact still validates — but the evaluator blocks it (nothing can bind
    # by a malformed digest) and the intent refuses to carry the garbage value.
    profile = _profile()
    preview = _ready_preview(profile)
    preview["artifact_digest"] = "NOT-A-DIGEST"
    intent = build_activation_intent(
        preview, current_state=no_active_profile_state(), target_profile=profile
    )
    assert intent.preview_digest is None
    assert intent.preconditions_decision == "blocked"
    assert "preview_digest_malformed" in intent.preconditions_reasons


# ---------------------------------------------------------------- build fail-closed


def test_build_requires_valid_current_state():
    state = no_active_profile_state()
    state.active_profile_id = "half_state"
    with pytest.raises(ActivationIntentError, match="invalid current state"):
        build_activation_intent(
            _ready_preview(_profile()), current_state=state, target_profile=_profile()
        )


def test_build_rejects_non_state_current():
    with pytest.raises(ActivationIntentError, match="must be an ActiveProfileState"):
        build_activation_intent(
            _ready_preview(_profile()), current_state=None, target_profile=_profile()
        )


def test_build_requires_a_target_reference():
    with pytest.raises(ActivationIntentError, match="does not identify a target"):
        build_activation_intent(
            {"nonsense": 1},
            current_state=no_active_profile_state(),
            target_profile=_profile(),
        )


def test_build_rejects_non_dict_preview():
    with pytest.raises(ActivationIntentError, match="preview must be an object"):
        build_activation_intent(
            "garbage", current_state=no_active_profile_state(), target_profile=_profile()
        )


# ---------------------------------------------------------------- validation matrix


def test_ready_without_references_fails_closed():
    intent = _ready_intent()
    intent.backup_manifest_sha256 = None
    intent.artifact_digest = intent.compute_digest()  # even a re-stamped digest cannot help
    with pytest.raises(ActivationIntentError, match="requires every reference"):
        intent.validate()


def test_ready_with_reasons_rejected():
    intent = _ready_intent()
    intent.preconditions_reasons = ("leftover_reason",)
    intent.artifact_digest = intent.compute_digest()
    with pytest.raises(ActivationIntentError, match="no blocking reasons"):
        intent.validate()


def test_nonready_without_reasons_rejected():
    intent = _ready_intent()
    intent.preconditions_decision = "blocked"
    intent.artifact_digest = intent.compute_digest()
    with pytest.raises(ActivationIntentError, match="at least one reason"):
        intent.validate()


def test_unknown_decision_rejected():
    intent = _ready_intent()
    intent.preconditions_decision = "approved"
    intent.artifact_digest = intent.compute_digest()
    with pytest.raises(ActivationIntentError, match="preconditions_decision"):
        intent.validate()


@pytest.mark.parametrize("pin_value", [True, 1, 0, "false", None])
def test_authorizes_activation_pin_is_identity_checked(pin_value):
    intent = _ready_intent()
    intent.authorizes_activation = pin_value
    intent.artifact_digest = intent.compute_digest()
    with pytest.raises(ActivationIntentError, match="never an authorization"):
        intent.validate()


def test_half_current_pair_rejected():
    intent = _ready_intent()
    intent.current_profile_id = "current_profile"  # version stays None
    intent.artifact_digest = intent.compute_digest()
    with pytest.raises(ActivationIntentError, match="half declarations are rejected"):
        intent.validate()


def test_future_schema_version_rejected():
    data = _ready_intent().to_dict()
    data["schema_version"] = 2
    with pytest.raises(ActivationIntentError, match="unsupported schema_version"):
        ActivationIntent.from_dict(data)


def test_naive_created_at_rejected():
    profile = _profile()
    with pytest.raises(ActivationIntentError, match="timezone-aware"):
        build_activation_intent(
            _ready_preview(profile),
            current_state=no_active_profile_state(),
            target_profile=profile,
            created_at="2026-07-12T12:00:00",
        )


def test_missing_key_rejected():
    data = _ready_intent().to_dict()
    del data["preconditions_decision"]
    with pytest.raises(ActivationIntentError, match="missing required keys"):
        ActivationIntent.from_dict(data)


def test_unexpected_key_rejected():
    data = _ready_intent().to_dict()
    data["approved"] = True
    with pytest.raises(ActivationIntentError, match="unexpected keys"):
        ActivationIntent.from_dict(data)


@pytest.mark.parametrize("bad", [None, [], "intent", 7])
def test_non_dict_rejected(bad):
    with pytest.raises(ActivationIntentError, match="must be an object"):
        ActivationIntent.from_dict(bad)


# ---------------------------------------------------------------- digest + idempotency


def test_volatile_handles_are_digest_excluded():
    profile = _profile()
    preview = _ready_preview(profile)
    a = build_activation_intent(
        preview,
        current_state=no_active_profile_state(),
        target_profile=profile,
        intent_id="lhintent-aaaaaaaaaaaaaaaa",
        created_at=TZ_TS,
    )
    b = build_activation_intent(
        preview,
        current_state=no_active_profile_state(),
        target_profile=profile,
        intent_id="lhintent-bbbbbbbbbbbbbbbb",
        created_at="2026-07-13T09:00:00+00:00",
    )
    assert a.intent_id != b.intent_id
    assert a.created_at != b.created_at
    assert a.artifact_digest == b.artifact_digest  # the producer-side idempotency identity


def test_different_intended_transitions_have_different_identities():
    state = no_active_profile_state()
    profile = _profile()
    other = _profile(display_name="Different Content")
    a = build_activation_intent(
        _ready_preview(profile), current_state=state, target_profile=profile
    )
    b = build_activation_intent(_ready_preview(other), current_state=state, target_profile=other)
    assert a.artifact_digest != b.artifact_digest


@pytest.mark.parametrize(
    ("field", "tampered"),
    [
        ("schema_version", 2),
        ("current_profile_id", "other_profile"),
        ("target_profile_id", "other_profile"),
        ("target_profile_version", 2),
        ("target_profile_sha256", "cd" * 32),
        ("preview_id", "lhtransition-other"),
        ("preview_digest", "cd" * 32),
        ("backup_manifest_id", "lhbackup-other"),
        ("backup_manifest_sha256", "cd" * 32),
        ("preconditions_decision", "blocked"),
        ("preconditions_reasons", ["injected_reason"]),
        ("authorizes_activation", True),
    ],
)
def test_every_content_field_edit_fails_validation(field, tampered):
    data = _ready_intent().to_dict()
    assert data[field] != tampered
    data[field] = tampered
    with pytest.raises(ActivationIntentError):
        ActivationIntent.from_dict(data)


def test_tampered_digest_rejected():
    data = _ready_intent().to_dict()
    data["artifact_digest"] = "ef" * 32
    with pytest.raises(ActivationIntentError, match="artifact_digest mismatch"):
        ActivationIntent.from_dict(data)


def test_roundtrip():
    intent = _ready_intent()
    restored = ActivationIntent.from_dict(intent.to_dict())
    assert restored == intent


# ---------------------------------------------------------------- registry wrapper


def test_registry_generate_ready_for_bundled_profile():
    bundled = get_profile("scientific_preservation")
    intent = generate_activation_intent_from_registry(
        _ready_preview(bundled), current_state=no_active_profile_state()
    )
    assert intent.preconditions_decision == "ready"
    assert intent.target_profile_id == "scientific_preservation"


def test_registry_generate_unknown_target_builds_blocked_intent():
    profile = _profile()  # not bundled
    intent = generate_activation_intent_from_registry(
        _ready_preview(profile), current_state=no_active_profile_state()
    )
    assert intent.preconditions_decision == "blocked"
    assert "target_profile_missing" in intent.preconditions_reasons


# ---------------------------------------------------------------- no activation surface


def test_module_has_no_activation_surface():
    for forbidden in (
        "activate",
        "activate_profile",
        "set_active_profile",
        "apply_intent",
        "execute_intent",
        "submit_intent",
        "approve_intent",
    ):
        assert not hasattr(ai, forbidden), forbidden
