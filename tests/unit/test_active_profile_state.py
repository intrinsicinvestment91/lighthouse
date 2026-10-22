"""Tests for modules/active_profile_state.py — active_profile_state.v1 (LHP-PROFILE-14B).

The contract must prove: absence is explicit (never inferred, never a default), no half
states exist, every fact is digest-covered, tampered records fail validation, and the
module exposes no activation or persistence surface.
"""

import json

import pytest

from modules import active_profile_state as aps
from modules.active_profile_state import (
    ACTIVE_PROFILE_STATE_SCHEMA_VERSION,
    ActivationEvidenceReferences,
    ActiveProfileState,
    ActiveProfileStateError,
    build_active_profile_state,
    no_active_profile_state,
)
from modules.profile_transition import profile_content_sha256
from modules.profiles import IntelligenceProfile

TZ_TS = "2026-07-12T12:00:00+00:00"
SOURCE = "operator_approved_transition"
TEST_EVIDENCE = ActivationEvidenceReferences(
    transition_preview_id="preview-unit-test",
    transition_preview_digest="1" * 64,
    backup_manifest_id="lhbackup-unit-test",
    backup_manifest_sha256="2" * 64,
    verification_report_digest="3" * 64,
    restore_report_digest="4" * 64,
    source_data_root="/tmp/unit-test-data",
    backup_root="/tmp/unit-test-backup",
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


def _active_state() -> ActiveProfileState:
    return build_active_profile_state(
        _profile(), activated_at=TZ_TS, activation_source=SOURCE, evidence=TEST_EVIDENCE
    )


# ---------------------------------------------------------------- explicit absence


def test_no_active_profile_state_is_explicit_and_valid():
    state = no_active_profile_state()
    assert state.activation_state == "no_active_profile"
    assert state.active_profile_id is None
    assert state.active_profile_version is None
    assert state.activated_at is None
    assert state.activation_source is None
    assert state.active_profile_sha256 is None
    assert state.transition_preview_id is None
    assert state.backup_root is None
    assert len(state.artifact_digest) == 64
    state.validate()


def test_from_dict_empty_raises_absence_is_never_inferred():
    with pytest.raises(ActiveProfileStateError, match="missing required keys"):
        ActiveProfileState.from_dict({})


def test_from_dict_missing_key_raises():
    data = no_active_profile_state().to_dict()
    del data["activation_state"]
    with pytest.raises(ActiveProfileStateError, match="activation_state"):
        ActiveProfileState.from_dict(data)


def test_from_dict_unexpected_key_raises():
    data = no_active_profile_state().to_dict()
    data["extra"] = "smuggled"
    with pytest.raises(ActiveProfileStateError, match="unexpected keys"):
        ActiveProfileState.from_dict(data)


@pytest.mark.parametrize("bad", [None, [], "active", 7])
def test_from_dict_non_dict_raises(bad):
    with pytest.raises(ActiveProfileStateError, match="must be an object"):
        ActiveProfileState.from_dict(bad)


def test_unknown_activation_state_rejected():
    state = no_active_profile_state()
    state.activation_state = "maybe_active"
    with pytest.raises(ActiveProfileStateError, match="activation_state"):
        state.validate()


# ---------------------------------------------------------------- no half states


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("active_profile_id", "target_profile"),
        ("active_profile_version", 1),
        ("activated_at", TZ_TS),
        ("activation_source", SOURCE),
        ("active_profile_sha256", "ab" * 32),
        ("transition_preview_id", "preview"),
        ("backup_root", "/tmp/backup"),
    ],
)
def test_absent_state_with_any_identity_field_rejected(field, value):
    state = no_active_profile_state()
    setattr(state, field, value)
    with pytest.raises(ActiveProfileStateError, match=f"{field}=None"):
        state.validate()


@pytest.mark.parametrize(
    "field",
    [
        "active_profile_id",
        "active_profile_version",
        "activated_at",
        "activation_source",
        "active_profile_sha256",
        "transition_preview_digest",
        "backup_manifest_id",
    ],
)
def test_active_state_with_any_identity_field_missing_rejected(field):
    state = _active_state()
    setattr(state, field, None)
    with pytest.raises(ActiveProfileStateError):
        state.validate()


# ---------------------------------------------------------------- fail-closed field rules


def test_schema_version_bool_trap_rejected():
    state = no_active_profile_state()
    state.schema_version = True
    with pytest.raises(ActiveProfileStateError, match="schema_version must be an integer"):
        state.validate()


def test_future_schema_version_rejected():
    state = no_active_profile_state()
    state.schema_version = ACTIVE_PROFILE_STATE_SCHEMA_VERSION + 1
    with pytest.raises(ActiveProfileStateError, match="unsupported schema_version"):
        state.validate()


def test_profile_version_bool_trap_rejected():
    state = _active_state()
    state.active_profile_version = True
    with pytest.raises(ActiveProfileStateError, match="integer >= 1"):
        state.validate()


def test_naive_activated_at_rejected():
    with pytest.raises(ActiveProfileStateError, match="timezone-aware"):
        build_active_profile_state(
            _profile(),
            activated_at="2026-07-12T12:00:00",
            activation_source=SOURCE,
            evidence=TEST_EVIDENCE,
        )


def test_non_iso_activated_at_rejected():
    with pytest.raises(ActiveProfileStateError, match="ISO-8601"):
        build_active_profile_state(
            _profile(),
            activated_at="last tuesday",
            activation_source=SOURCE,
            evidence=TEST_EVIDENCE,
        )


def test_unknown_activation_source_rejected():
    with pytest.raises(ActiveProfileStateError, match="activation_source"):
        build_active_profile_state(
            _profile(), activated_at=TZ_TS, activation_source="cron_job", evidence=TEST_EVIDENCE
        )


def test_invalid_profile_id_rejected():
    state = _active_state()
    state.active_profile_id = "Not-Valid"
    with pytest.raises(ActiveProfileStateError, match="active_profile_id"):
        state.validate()


@pytest.mark.parametrize("bad_sha", ["short", "AB" * 32, "xy" * 32, ""])
def test_malformed_active_profile_sha256_rejected(bad_sha):
    state = _active_state()
    state.active_profile_sha256 = bad_sha
    with pytest.raises(ActiveProfileStateError, match="active_profile_sha256"):
        state.validate()


# ---------------------------------------------------------------- digest + tamper evidence


def test_digest_is_deterministic():
    a = build_active_profile_state(
        _profile(), activated_at=TZ_TS, activation_source=SOURCE, evidence=TEST_EVIDENCE
    )
    b = build_active_profile_state(
        _profile(), activated_at=TZ_TS, activation_source=SOURCE, evidence=TEST_EVIDENCE
    )
    assert a.artifact_digest == b.artifact_digest
    assert no_active_profile_state().artifact_digest == no_active_profile_state().artifact_digest


@pytest.mark.parametrize(
    ("field", "tampered"),
    [
        # For fields whose only alternatives are outside their allowed set, the earlier
        # fail-closed rule fires before the digest check — either way the edit cannot survive.
        ("schema_version", 2),
        ("activation_state", "no_active_profile"),
        ("active_profile_id", "other_profile"),
        ("active_profile_version", 2),
        ("activated_at", "2026-07-13T12:00:00+00:00"),
        ("activation_source", "cron_job"),
        ("active_profile_sha256", "cd" * 32),
    ],
)
def test_every_content_field_edit_fails_validation(field, tampered):
    data = _active_state().to_dict()
    assert data[field] != tampered
    data[field] = tampered
    with pytest.raises(ActiveProfileStateError):
        ActiveProfileState.from_dict(data)


def test_absence_flip_tamper_is_detected():
    data = no_active_profile_state().to_dict()
    data["activation_state"] = "active"
    data["active_profile_id"] = "target_profile"
    data["active_profile_version"] = 1
    data["activated_at"] = TZ_TS
    data["activation_source"] = SOURCE
    data["active_profile_sha256"] = "ab" * 32
    data["transition_preview_id"] = TEST_EVIDENCE.transition_preview_id
    data["transition_preview_digest"] = TEST_EVIDENCE.transition_preview_digest
    data["backup_manifest_id"] = TEST_EVIDENCE.backup_manifest_id
    data["backup_manifest_sha256"] = TEST_EVIDENCE.backup_manifest_sha256
    data["verification_report_digest"] = TEST_EVIDENCE.verification_report_digest
    data["restore_report_digest"] = TEST_EVIDENCE.restore_report_digest
    data["source_data_root"] = TEST_EVIDENCE.source_data_root
    data["backup_root"] = TEST_EVIDENCE.backup_root
    # digest left as the absent record's digest — the flip must not survive
    with pytest.raises(ActiveProfileStateError, match="artifact_digest mismatch"):
        ActiveProfileState.from_dict(data)


def test_stale_digest_after_content_change_rejected():
    state = _active_state()
    state.active_profile_version = 2
    with pytest.raises(ActiveProfileStateError, match="artifact_digest mismatch"):
        state.validate()


# ---------------------------------------------------------------- roundtrip + factory


def test_active_state_roundtrip():
    state = _active_state()
    restored = ActiveProfileState.from_dict(state.to_dict())
    assert restored == state


def test_absent_state_roundtrip():
    state = no_active_profile_state()
    restored = ActiveProfileState.from_dict(state.to_dict())
    assert restored == state


def test_factory_derives_identity_from_profile_content():
    profile = _profile()
    state = build_active_profile_state(
        profile, activated_at=TZ_TS, activation_source=SOURCE, evidence=TEST_EVIDENCE
    )
    assert state.active_profile_id == profile.profile_id
    assert state.active_profile_version == profile.profile_version
    assert state.active_profile_sha256 == profile_content_sha256(profile)


def test_factory_rejects_invalid_profile():
    broken = IntelligenceProfile(profile_id="target_profile", status="bogus")
    with pytest.raises(ActiveProfileStateError, match="profile does not validate"):
        build_active_profile_state(
            broken, activated_at=TZ_TS, activation_source=SOURCE, evidence=TEST_EVIDENCE
        )


def test_to_dict_of_invalid_record_raises():
    state = ActiveProfileState(activation_state="active")
    with pytest.raises(ActiveProfileStateError):
        state.to_dict()


# ---------------------------------------------------------------- no activation surface


def test_module_has_no_activation_or_persistence_surface():
    for forbidden in (
        "activate",
        "activate_profile",
        "set_active_profile",
        "write_active_profile",
        "save_state",
        "store_state",
        "persist_state",
        "write_state",
        "load_state",
        "read_state",
    ):
        assert not hasattr(aps, forbidden), forbidden


def test_active_profile_state_schema_mirror_pins_contract():
    from pathlib import Path

    mirror = json.loads(
        Path("docs/schemas/active_profile_state.v1.schema.json").read_text(encoding="utf-8")
    )
    assert set(mirror["required"]) == aps._EXPECTED_KEYS
    assert set(mirror["properties"]) == aps._EXPECTED_KEYS
    assert mirror["additionalProperties"] is False
