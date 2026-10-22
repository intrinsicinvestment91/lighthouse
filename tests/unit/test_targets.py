import json

import pytest

from modules.targets import (
    TARGET_SCHEMA_VERSION,
    TargetConfig,
    TargetProfile,
    load_targets,
    validate_targets,
)


def _write_targets(tmp_path, payload):
    path = tmp_path / "targets.json"
    path.write_text(json.dumps(payload))
    return path


def _valid_profile_dict(**overrides):
    base = {
        "name": "fusion_research",
        "entities": ["Helion Energy"],
        "keywords": ["fusion"],
        "signals": ["sec", "patent"],
        "regions": ["us"],
        "priority": "high",
        "enabled": True,
        "metadata": {},
        "schema_version": TARGET_SCHEMA_VERSION,
    }
    base.update(overrides)
    return base


# TargetProfile tests


def test_valid_target_validates():
    profile = TargetProfile.from_dict(_valid_profile_dict())
    profile.validate()
    assert profile.name == "fusion_research"


def test_missing_name_fails():
    profile = TargetProfile.from_dict(_valid_profile_dict(name="  "))
    with pytest.raises(ValueError, match="name must be non-empty"):
        profile.validate()


def test_invalid_priority_fails():
    profile = TargetProfile.from_dict(_valid_profile_dict(priority="critical"))
    with pytest.raises(ValueError, match="priority must be one of"):
        profile.validate()


def test_invalid_signal_fails():
    profile = TargetProfile.from_dict(_valid_profile_dict(signals=["sec", "velocity"]))
    with pytest.raises(ValueError, match="invalid signal: velocity"):
        profile.validate()


def test_target_with_no_entities_keywords_regions_fails():
    profile = TargetProfile.from_dict(
        _valid_profile_dict(entities=[], keywords=[], regions=[], signals=["sec"])
    )
    with pytest.raises(
        ValueError,
        match="target must include at least one entity, keyword, or region",
    ):
        profile.validate()


def test_defaults_normalize_safely():
    profile = TargetProfile(
        name="  test_target  ",
        entities=None,
        keywords=None,
        signals=None,
        regions=None,
        priority="",
        metadata=None,
        schema_version=None,
    )
    assert profile.entities == []
    assert profile.keywords == []
    assert profile.signals == []
    assert profile.regions == []
    assert profile.metadata == {}
    assert profile.priority == "medium"
    assert profile.schema_version == TARGET_SCHEMA_VERSION
    assert profile.name == "test_target"


def test_target_serialization_round_trip():
    original = TargetProfile.from_dict(_valid_profile_dict())
    encoded = original.to_dict()
    decoded = TargetProfile.from_dict(encoded)
    assert decoded.to_dict() == encoded


def test_signals_normalize_to_lowercase():
    profile = TargetProfile.from_dict(_valid_profile_dict(signals=["SEC", " Patent "]))
    assert profile.signals == ["sec", "patent"]


def test_regions_normalize_to_lowercase():
    profile = TargetProfile.from_dict(_valid_profile_dict(regions=["US", " eu "]))
    assert profile.regions == ["us", "eu"]


# TargetConfig tests


def test_valid_config_validates():
    config = TargetConfig.from_dict(
        {
            "schema_version": TARGET_SCHEMA_VERSION,
            "targets": [_valid_profile_dict()],
        }
    )
    config.validate()
    assert len(config.targets) == 1


def test_duplicate_target_names_fail_case_insensitively():
    config = TargetConfig.from_dict(
        {
            "schema_version": TARGET_SCHEMA_VERSION,
            "targets": [
                _valid_profile_dict(name="Fusion"),
                _valid_profile_dict(name="fusion"),
            ],
        }
    )
    with pytest.raises(ValueError, match="duplicate target name: fusion"):
        config.validate()


def test_disabled_targets_excluded_from_enabled_targets():
    config = TargetConfig.from_dict(
        {
            "schema_version": TARGET_SCHEMA_VERSION,
            "targets": [
                _valid_profile_dict(name="active", enabled=True),
                _valid_profile_dict(name="inactive", enabled=False),
            ],
        }
    )
    enabled = config.enabled_targets()
    assert len(enabled) == 1
    assert enabled[0].name == "active"


def test_dict_targets_converted_to_target_profile():
    config = TargetConfig.from_dict(
        {
            "schema_version": TARGET_SCHEMA_VERSION,
            "targets": [_valid_profile_dict()],
        }
    )
    assert all(isinstance(t, TargetProfile) for t in config.targets)


def test_config_serialization_round_trip():
    original = TargetConfig.from_dict(
        {
            "schema_version": TARGET_SCHEMA_VERSION,
            "targets": [_valid_profile_dict()],
        }
    )
    encoded = original.to_dict()
    decoded = TargetConfig.from_dict(encoded)
    assert decoded.to_dict() == encoded


# Loader helper tests


def test_load_targets_loads_valid_json(tmp_path):
    path = _write_targets(
        tmp_path,
        {
            "schema_version": TARGET_SCHEMA_VERSION,
            "targets": [_valid_profile_dict()],
        },
    )
    config = load_targets(path)
    assert len(config.targets) == 1
    assert config.targets[0].name == "fusion_research"


def test_validate_targets_returns_valid_true_for_good_config(tmp_path):
    path = _write_targets(
        tmp_path,
        {
            "schema_version": TARGET_SCHEMA_VERSION,
            "targets": [_valid_profile_dict()],
        },
    )
    result = validate_targets(path)
    assert result["valid"] is True
    assert result["errors"] == []
    assert result["target_count"] == 1
    assert result["enabled_count"] == 1


def test_validate_targets_returns_valid_false_for_bad_config(tmp_path):
    path = _write_targets(
        tmp_path,
        {
            "schema_version": TARGET_SCHEMA_VERSION,
            "targets": [_valid_profile_dict(signals=["not_a_signal"])],
        },
    )
    result = validate_targets(path)
    assert result["valid"] is False
    assert len(result["errors"]) == 1
    assert result["target_count"] == 0
    assert result["enabled_count"] == 0


def test_validate_targets_returns_warnings_for_disabled_no_signals_no_keywords(tmp_path):
    path = _write_targets(
        tmp_path,
        {
            "schema_version": TARGET_SCHEMA_VERSION,
            "targets": [
                {
                    "name": "sparse_target",
                    "entities": ["Example Corp"],
                    "keywords": [],
                    "signals": [],
                    "regions": ["us"],
                    "enabled": False,
                }
            ],
        },
    )
    result = validate_targets(path)
    assert result["valid"] is True
    assert len(result["warnings"]) == 3
    assert any("has no signals" in w for w in result["warnings"])
    assert any("is disabled" in w for w in result["warnings"])
    assert any("has no keywords" in w for w in result["warnings"])
    assert result["target_count"] == 1
    assert result["enabled_count"] == 0


# Seed file tests


def test_load_seed_targets_json_passes():
    config = load_targets("data/targets.json")
    config.validate()
    assert len(config.targets) >= 1


def test_seed_file_has_at_least_one_enabled_target():
    config = load_targets("data/targets.json")
    assert len(config.enabled_targets()) >= 1


def test_seed_government_defense_labs_profile():
    config = load_targets("data/targets.json")
    profile = next((t for t in config.targets if t.name == "government_defense_labs"), None)
    assert profile is not None
    assert profile.enabled is True
    assert "DARPA" in profile.entities
    assert "sec" not in profile.signals
    assert "flight" in profile.signals
    assert "adsb" in profile.signals
    assert "archive" in profile.signals
    assert "narrative" in profile.signals
