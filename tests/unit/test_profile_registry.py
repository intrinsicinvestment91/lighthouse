"""Unit tests for the read-only bundled-profile registry (modules/profile_registry.py)."""

import json

import pytest

from modules import profile_registry
from modules.profile_registry import (
    get_profile,
    list_bundled_profiles,
    validate_all_bundled,
)


def _write_profile(directory, filename, **overrides):
    data = {
        "schema_version": 1,
        "profile_id": overrides.get("profile_id", "reg_profile"),
        "profile_version": overrides.get("profile_version", 1),
        "display_name": overrides.get("display_name", "Reg Profile"),
        "mission": "m",
        "status": "draft",
        "backup_policy": {"backup_required_on_activation": True},
    }
    (directory / filename).write_text(json.dumps(data), encoding="utf-8")


def test_list_is_deterministically_ordered(tmp_path):
    _write_profile(tmp_path, "z.json", profile_id="zzz_profile")
    _write_profile(tmp_path, "a.json", profile_id="aaa_profile")
    _write_profile(tmp_path, "m.json", profile_id="mmm_profile")

    ids = [p.profile_id for p in list_bundled_profiles(tmp_path)]
    assert ids == ["aaa_profile", "mmm_profile", "zzz_profile"]
    # Stable across repeated calls.
    assert ids == [p.profile_id for p in list_bundled_profiles(tmp_path)]


def test_get_profile_exact_match(tmp_path):
    _write_profile(tmp_path, "one.json", profile_id="one_profile", profile_version=1)
    profile = get_profile("one_profile", directory=tmp_path)
    assert profile.profile_id == "one_profile"
    assert profile.profile_version == 1


def test_get_profile_specific_version(tmp_path):
    _write_profile(tmp_path, "v1.json", profile_id="ver_profile", profile_version=1)
    _write_profile(tmp_path, "v2.json", profile_id="ver_profile", profile_version=2)
    assert get_profile("ver_profile", version=1, directory=tmp_path).profile_version == 1
    # No version -> highest.
    assert get_profile("ver_profile", directory=tmp_path).profile_version == 2


def test_get_profile_missing_raises(tmp_path):
    _write_profile(tmp_path, "one.json", profile_id="one_profile")
    with pytest.raises(KeyError, match="profile not found"):
        get_profile("does_not_exist", directory=tmp_path)


def test_duplicate_registration_rejected(tmp_path):
    _write_profile(tmp_path, "a.json", profile_id="dup_profile", profile_version=1)
    _write_profile(tmp_path, "b.json", profile_id="dup_profile", profile_version=1)
    with pytest.raises(ValueError, match="duplicate profile registration"):
        list_bundled_profiles(tmp_path)


def test_validate_all_reports_malformed(tmp_path):
    _write_profile(tmp_path, "good.json", profile_id="good_profile")
    (tmp_path / "bad.json").write_text("{ not valid json", encoding="utf-8")

    result = validate_all_bundled(tmp_path)
    assert result["valid"] is False
    assert any("bad.json" in err for err in result["errors"])
    assert result["profile_count"] == 2


def test_validate_all_reports_duplicate(tmp_path):
    _write_profile(tmp_path, "a.json", profile_id="dup_profile", profile_version=1)
    _write_profile(tmp_path, "b.json", profile_id="dup_profile", profile_version=1)
    result = validate_all_bundled(tmp_path)
    assert result["valid"] is False
    assert any("duplicate registration" in err for err in result["errors"])


def test_empty_directory_is_empty(tmp_path):
    assert list_bundled_profiles(tmp_path) == []
    result = validate_all_bundled(tmp_path)
    assert result["valid"] is True
    assert result["profile_count"] == 0


def test_registry_has_no_activation_surface():
    # The registry must never expose activation/selection. Fail-closed by absence.
    for forbidden in ("activate_profile", "set_active_profile", "activate", "write_active_profile"):
        assert not hasattr(profile_registry, forbidden)
