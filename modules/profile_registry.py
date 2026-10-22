"""Read-only registry over the bundled Intelligence Profile definitions.

Enumerates, retrieves, and validates the declarative profiles shipped in `seed/profiles/`.
This layer is strictly read-only: it never activates a profile, never selects an active
profile, and never writes any file or DB row. Activation lives in ``profile_activation.py``
and ``lighthouse profile-activate`` (``repository_capability_discovery`` v2 only).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from modules.profiles import IntelligenceProfile, load_profile, validate_profile

# Bundled definitions are tracked in the repo's seed/ tree (bundled_immutable state). Operator
# overrides will later live under /data/profiles/, but this registry only reads the bundled set.
BUNDLED_PROFILES_DIR = Path(__file__).resolve().parent.parent / "seed" / "profiles"


def _iter_profile_files(directory: str | Path | None = None) -> list[Path]:
    base = Path(directory) if directory is not None else BUNDLED_PROFILES_DIR
    if not base.is_dir():
        return []
    return sorted(base.glob("*.json"), key=lambda p: p.name)


def _profile_sort_key(profile: IntelligenceProfile) -> tuple[str, int]:
    return (profile.profile_id, profile.profile_version)


def list_bundled_profiles(directory: str | Path | None = None) -> list[IntelligenceProfile]:
    """Load every bundled profile, enforce unique (profile_id, profile_version), sort stably.

    Raises on any malformed definition or duplicate registration (fail-closed).
    """
    profiles: list[IntelligenceProfile] = []
    seen: set[tuple[str, int]] = set()
    for path in _iter_profile_files(directory):
        profile = load_profile(path)
        key = _profile_sort_key(profile)
        if key in seen:
            raise ValueError(
                f"duplicate profile registration: {profile.profile_id} v{profile.profile_version}"
            )
        seen.add(key)
        profiles.append(profile)
    return sorted(profiles, key=_profile_sort_key)


def get_profile(
    profile_id: str,
    version: int | None = None,
    directory: str | Path | None = None,
) -> IntelligenceProfile:
    """Return the profile matching `profile_id` (and `version` if given).

    With no version, returns the highest available version. Raises `KeyError` on no match.
    """
    matches = [p for p in list_bundled_profiles(directory) if p.profile_id == profile_id]
    if version is not None:
        matches = [p for p in matches if p.profile_version == version]
    if not matches:
        raise KeyError(f"profile not found: {profile_id}" + (f" v{version}" if version else ""))
    return max(matches, key=lambda p: p.profile_version)


def validate_all_bundled(directory: str | Path | None = None) -> dict[str, Any]:
    """Validate every bundled profile without raising; report a structured aggregate."""
    errors: list[str] = []
    warnings: list[str] = []
    results: list[dict[str, Any]] = []
    seen: dict[tuple[str, int], str] = {}

    for path in _iter_profile_files(directory):
        summary = validate_profile(path)
        entry: dict[str, Any] = {"file": path.name, **summary}
        results.append(entry)
        if not summary["valid"]:
            errors.extend(f"{path.name}: {e}" for e in summary["errors"])
            continue
        warnings.extend(f"{path.name}: {w}" for w in summary["warnings"])
        prof = summary["profile"]
        key = (prof["profile_id"], prof["profile_version"])
        if key in seen:
            errors.append(
                f"{path.name}: duplicate registration of {prof['profile_id']} "
                f"v{prof['profile_version']} (also in {seen[key]})"
            )
        else:
            seen[key] = path.name

    return {
        "valid": not errors,
        "profile_count": len(results),
        "profiles": results,
        "errors": errors,
        "warnings": warnings,
    }
