"""Intelligence Profile contract — versioned, declarative, fail-closed.

An Intelligence Profile is a *declarative policy bundle* that describes which mission a
Lighthouse deployment is currently configured for (public scientific monitoring, private
invention discovery, etc.). It is NOT a plugin: a profile may never declare arbitrary
executable modules or shell commands. This module defines the contract and its validation
only — it does not activate, resolve, or apply any profile. Activation is a separate,
future, operator-tasked capability.

Mirrors the `modules/targets.py` idiom: a dataclass + `*_SCHEMA_VERSION` constant +
`__post_init__` normalization + `validate()` raising `ValueError` + `to_dict()`/`from_dict()`
+ a `load_*` (raises) / `validate_*` (non-raising summary) pair.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from modules.backup_readiness import DATA_BOUNDARY, is_within_boundary

INTELLIGENCE_PROFILE_SCHEMA_VERSION = 1

# Maturity states. There is deliberately NO "production" / "active" status: this packet ships
# architectural seeds only, and activation is not implemented.
ALLOWED_STATUSES = {"draft", "experimental", "ready_for_validation"}

# A stable, machine-safe identifier (used for data roots, activation records, audit).
PROFILE_ID_RE = re.compile(r"^[a-z][a-z0-9_]*$")

# The declarative policy blocks. Each must be a dict; none may contain executable-intent keys.
POLICY_FIELDS = (
    "compatibility",
    "source_policy",
    "collector_policy",
    "observation_policy",
    "graph_policy",
    "correlation_policy",
    "temporal_policy",
    "privacy_policy",
    "publishing_policy",
    "backup_policy",
    "interface_policy",
    "dead_mans_switch_policy",
)

# Keys that would turn a declarative bundle into an executable plugin. Rejected at any depth.
_FORBIDDEN_POLICY_KEYS = {
    "module",
    "modules",
    "command",
    "cmd",
    "shell",
    "exec",
    "entrypoint",
    "eval",
    "script",
    "run",
    "callable",
    "import",
    "subprocess",
}

# Recognized keys inside the `compatibility` block. Unknown declarations fail closed.
ALLOWED_COMPATIBILITY_KEYS = {
    "min_schema_version",
    "max_schema_version",
    "requires_data_boundary",
    "start9_min_version",
    "lighthouse_min_version",
    "platforms",
    "notes",
}

# Fields inside dead_mans_switch_policy that would imply *enrollment/activation* rather than
# mere declarative eligibility. Any truthy value here is rejected.
_DMS_ACTIVATION_KEYS = ("enrolled", "activated", "active", "enabled", "armed", "scheduled")


@dataclass
class IntelligenceProfile:
    profile_id: str
    profile_version: int = 1
    display_name: str = ""
    description: str = ""
    mission: str = ""
    status: str = "draft"
    compatibility: dict[str, Any] = field(default_factory=dict)
    source_policy: dict[str, Any] = field(default_factory=dict)
    collector_policy: dict[str, Any] = field(default_factory=dict)
    observation_policy: dict[str, Any] = field(default_factory=dict)
    graph_policy: dict[str, Any] = field(default_factory=dict)
    correlation_policy: dict[str, Any] = field(default_factory=dict)
    temporal_policy: dict[str, Any] = field(default_factory=dict)
    privacy_policy: dict[str, Any] = field(default_factory=dict)
    publishing_policy: dict[str, Any] = field(default_factory=dict)
    backup_policy: dict[str, Any] = field(default_factory=dict)
    interface_policy: dict[str, Any] = field(default_factory=dict)
    dead_mans_switch_policy: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    schema_version: int = INTELLIGENCE_PROFILE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        for name in (*POLICY_FIELDS, "metadata"):
            if getattr(self, name) is None:
                setattr(self, name, {})
        if self.schema_version is None:
            self.schema_version = INTELLIGENCE_PROFILE_SCHEMA_VERSION
        if self.profile_version is None:
            self.profile_version = 1
        if not self.status:
            self.status = "draft"

        self.profile_id = (self.profile_id or "").strip()
        self.display_name = (self.display_name or "").strip()
        self.status = self.status.strip().lower()

    # -- validation ---------------------------------------------------------------------

    def validate(self) -> None:
        # Identity ---------------------------------------------------------------------
        if not self.profile_id:
            raise ValueError("profile_id must be non-empty")
        if not PROFILE_ID_RE.match(self.profile_id):
            raise ValueError(f"invalid profile_id: {self.profile_id}")
        if not self.display_name:
            raise ValueError("display_name must be non-empty")

        # Versions (fail closed on unknown/future schema) ------------------------------
        if not isinstance(self.profile_version, int) or isinstance(self.profile_version, bool):
            raise ValueError("profile_version must be an integer")
        if self.profile_version < 1:
            raise ValueError("profile_version must be >= 1")
        if not isinstance(self.schema_version, int) or isinstance(self.schema_version, bool):
            raise ValueError("schema_version must be an integer")
        if self.schema_version < 1:
            raise ValueError("schema_version must be >= 1")
        if self.schema_version > INTELLIGENCE_PROFILE_SCHEMA_VERSION:
            raise ValueError(
                f"unsupported schema_version: {self.schema_version} "
                f"(max {INTELLIGENCE_PROFILE_SCHEMA_VERSION})"
            )

        # Maturity ---------------------------------------------------------------------
        if self.status not in ALLOWED_STATUSES:
            raise ValueError(f"invalid status: {self.status}")

        # Policy blocks must be dicts and carry no executable-intent keys --------------
        for name in POLICY_FIELDS:
            value = getattr(self, name)
            if not isinstance(value, dict):
                raise ValueError(f"{name} must be a dict")
            _reject_forbidden_keys(name, value)
        if not isinstance(self.metadata, dict):
            raise ValueError("metadata must be a dict")

        # Compatibility must be well-formed; unknown declarations fail closed ----------
        for key in self.compatibility:
            if key not in ALLOWED_COMPATIBILITY_KEYS:
                raise ValueError(f"invalid compatibility key: {key}")

        # Privacy vs. publishing: a private profile may not publish publicly ------------
        if self._is_private() and self._public_publishing_enabled():
            raise ValueError("private profile must not enable public_publishing")

        # Dead-man's-switch: eligibility is declarative only; never enrollment ----------
        dms = self.dead_mans_switch_policy
        eligible = dms.get("eligible", False)
        if not isinstance(eligible, bool):
            raise ValueError("dead_mans_switch_policy.eligible must be a boolean")
        for key in _DMS_ACTIVATION_KEYS:
            if dms.get(key):
                raise ValueError(
                    f"dead_mans_switch_policy.{key} is not permitted "
                    "(eligibility is declarative only; enrollment is never declared here)"
                )

        # Backup policy: activation readiness is impossible without a resolved backup ----
        backup_required = self.backup_policy.get("backup_required_on_activation")
        if not isinstance(backup_required, bool):
            raise ValueError("backup_policy.backup_required_on_activation must be a boolean")
        data_root = self.backup_policy.get("data_root")
        if data_root is not None:
            if not isinstance(data_root, str) or not data_root.strip():
                raise ValueError("backup_policy.data_root must be a non-empty string when present")
            if not is_within_boundary(data_root, DATA_BOUNDARY):
                raise ValueError(
                    f"backup_policy.data_root must resolve under {DATA_BOUNDARY}: {data_root}"
                )

    def _is_private(self) -> bool:
        return bool(self.privacy_policy.get("local_only"))

    def _public_publishing_enabled(self) -> bool:
        return bool(self.publishing_policy.get("public_publishing"))

    # -- serialization ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        payload: dict[str, Any] = {
            "schema_version": self.schema_version,
            "profile_id": self.profile_id,
            "profile_version": self.profile_version,
            "display_name": self.display_name,
            "description": self.description,
            "mission": self.mission,
            "status": self.status,
        }
        for name in POLICY_FIELDS:
            payload[name] = getattr(self, name)
        payload["metadata"] = self.metadata
        return payload

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> IntelligenceProfile:
        return cls(
            profile_id=data.get("profile_id", ""),
            profile_version=data.get("profile_version", 1),
            display_name=data.get("display_name", ""),
            description=data.get("description", ""),
            mission=data.get("mission", ""),
            status=data.get("status", "draft"),
            compatibility=data.get("compatibility") or {},
            source_policy=data.get("source_policy") or {},
            collector_policy=data.get("collector_policy") or {},
            observation_policy=data.get("observation_policy") or {},
            graph_policy=data.get("graph_policy") or {},
            correlation_policy=data.get("correlation_policy") or {},
            temporal_policy=data.get("temporal_policy") or {},
            privacy_policy=data.get("privacy_policy") or {},
            publishing_policy=data.get("publishing_policy") or {},
            backup_policy=data.get("backup_policy") or {},
            interface_policy=data.get("interface_policy") or {},
            dead_mans_switch_policy=data.get("dead_mans_switch_policy") or {},
            metadata=data.get("metadata") or {},
            schema_version=data.get("schema_version", INTELLIGENCE_PROFILE_SCHEMA_VERSION),
        )


def _reject_forbidden_keys(block_name: str, obj: Any) -> None:
    """Recursively reject executable-intent keys anywhere inside a policy block."""
    if isinstance(obj, dict):
        for key, value in obj.items():
            if isinstance(key, str) and key.strip().lower() in _FORBIDDEN_POLICY_KEYS:
                raise ValueError(f"{block_name} may not declare executable key: {key}")
            _reject_forbidden_keys(block_name, value)
    elif isinstance(obj, list):
        for item in obj:
            _reject_forbidden_keys(block_name, item)


def load_profile(path: str | Path) -> IntelligenceProfile:
    """Load and validate a single profile definition. Raises on any error (fail-closed)."""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    profile = IntelligenceProfile.from_dict(raw)
    profile.validate()
    return profile


def validate_profile(path: str | Path) -> dict[str, Any]:
    """Non-raising wrapper returning a structured validation summary."""
    errors: list[str] = []
    warnings: list[str] = []
    try:
        profile = load_profile(path)
    except Exception as exc:  # noqa: BLE001 - summarize any failure for the caller
        errors.append(str(exc))
        return {"valid": False, "errors": errors, "warnings": warnings, "profile": None}

    if profile.status != "ready_for_validation":
        warnings.append(f"profile {profile.profile_id} is not yet ready_for_validation")
    if not profile.mission:
        warnings.append(f"profile {profile.profile_id} has no mission statement")

    return {
        "valid": True,
        "errors": errors,
        "warnings": warnings,
        "profile": {
            "profile_id": profile.profile_id,
            "profile_version": profile.profile_version,
            "display_name": profile.display_name,
            "status": profile.status,
        },
    }
