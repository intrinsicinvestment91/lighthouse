"""Unit tests for the IntelligenceProfile contract (modules/profiles.py)."""

import pytest

from modules.profiles import (
    INTELLIGENCE_PROFILE_SCHEMA_VERSION,
    IntelligenceProfile,
)


def _valid_profile_dict(**overrides):
    base = {
        "schema_version": 1,
        "profile_id": "test_profile",
        "profile_version": 1,
        "display_name": "Test Profile",
        "description": "A test profile.",
        "mission": "test mission",
        "status": "draft",
        "backup_policy": {"backup_required_on_activation": True},
    }
    base.update(overrides)
    return base


def test_valid_profile_accepted():
    profile = IntelligenceProfile.from_dict(_valid_profile_dict())
    profile.validate()
    assert profile.profile_id == "test_profile"


def test_missing_profile_id_fails():
    profile = IntelligenceProfile.from_dict(_valid_profile_dict(profile_id="   "))
    with pytest.raises(ValueError, match="profile_id must be non-empty"):
        profile.validate()


def test_invalid_profile_id_fails():
    profile = IntelligenceProfile.from_dict(_valid_profile_dict(profile_id="Bad-ID"))
    with pytest.raises(ValueError, match="invalid profile_id"):
        profile.validate()


def test_missing_display_name_fails():
    profile = IntelligenceProfile.from_dict(_valid_profile_dict(display_name=" "))
    with pytest.raises(ValueError, match="display_name must be non-empty"):
        profile.validate()


def test_schema_version_below_one_fails():
    profile = IntelligenceProfile.from_dict(_valid_profile_dict(schema_version=0))
    with pytest.raises(ValueError, match="schema_version must be >= 1"):
        profile.validate()


def test_schema_version_above_current_fails():
    profile = IntelligenceProfile.from_dict(
        _valid_profile_dict(schema_version=INTELLIGENCE_PROFILE_SCHEMA_VERSION + 1)
    )
    with pytest.raises(ValueError, match="unsupported schema_version"):
        profile.validate()


def test_profile_version_below_one_fails():
    profile = IntelligenceProfile.from_dict(_valid_profile_dict(profile_version=0))
    with pytest.raises(ValueError, match="profile_version must be >= 1"):
        profile.validate()


def test_invalid_status_fails():
    profile = IntelligenceProfile.from_dict(_valid_profile_dict(status="production"))
    with pytest.raises(ValueError, match="invalid status"):
        profile.validate()


def test_forbidden_executable_key_rejected():
    profile = IntelligenceProfile.from_dict(
        _valid_profile_dict(collector_policy={"command": "rm -rf /"})
    )
    with pytest.raises(ValueError, match="executable key"):
        profile.validate()


def test_forbidden_nested_executable_key_rejected():
    profile = IntelligenceProfile.from_dict(
        _valid_profile_dict(source_policy={"nested": {"exec": "payload"}})
    )
    with pytest.raises(ValueError, match="executable key"):
        profile.validate()


def test_private_profile_with_public_publishing_rejected():
    profile = IntelligenceProfile.from_dict(
        _valid_profile_dict(
            privacy_policy={"local_only": True},
            publishing_policy={"public_publishing": True},
        )
    )
    with pytest.raises(ValueError, match="must not enable public_publishing"):
        profile.validate()


def test_dms_defaults_disabled():
    profile = IntelligenceProfile.from_dict(_valid_profile_dict())
    profile.validate()
    assert profile.dead_mans_switch_policy.get("eligible", False) is False


def test_dms_eligible_true_allowed():
    profile = IntelligenceProfile.from_dict(
        _valid_profile_dict(dead_mans_switch_policy={"eligible": True})
    )
    profile.validate()  # eligibility is declarative and permitted


def test_dms_enrollment_rejected():
    profile = IntelligenceProfile.from_dict(
        _valid_profile_dict(dead_mans_switch_policy={"eligible": True, "enrolled": True})
    )
    with pytest.raises(ValueError, match="not permitted"):
        profile.validate()


def test_dms_eligible_must_be_boolean():
    profile = IntelligenceProfile.from_dict(
        _valid_profile_dict(dead_mans_switch_policy={"eligible": "yes"})
    )
    with pytest.raises(ValueError, match="eligible must be a boolean"):
        profile.validate()


def test_backup_required_missing_fails():
    profile = IntelligenceProfile.from_dict(_valid_profile_dict(backup_policy={}))
    with pytest.raises(ValueError, match="backup_required_on_activation must be a boolean"):
        profile.validate()


def test_data_root_outside_boundary_fails():
    profile = IntelligenceProfile.from_dict(
        _valid_profile_dict(
            backup_policy={"backup_required_on_activation": True, "data_root": "/tmp/escape"}
        )
    )
    with pytest.raises(ValueError, match="must resolve under /data"):
        profile.validate()


def test_data_root_inside_boundary_ok():
    profile = IntelligenceProfile.from_dict(
        _valid_profile_dict(
            backup_policy={
                "backup_required_on_activation": True,
                "data_root": "/data/profiles/test_profile",
            }
        )
    )
    profile.validate()


def test_invalid_compatibility_key_fails():
    profile = IntelligenceProfile.from_dict(_valid_profile_dict(compatibility={"bogus_key": 1}))
    with pytest.raises(ValueError, match="invalid compatibility key"):
        profile.validate()


def test_policy_block_must_be_dict():
    profile = IntelligenceProfile.from_dict(_valid_profile_dict(graph_policy=["not", "a", "dict"]))
    with pytest.raises(ValueError, match="graph_policy must be a dict"):
        profile.validate()


def test_to_dict_roundtrip():
    profile = IntelligenceProfile.from_dict(_valid_profile_dict())
    restored = IntelligenceProfile.from_dict(profile.to_dict())
    assert restored.profile_id == profile.profile_id
    assert restored.profile_version == profile.profile_version
    assert restored.to_dict() == profile.to_dict()
