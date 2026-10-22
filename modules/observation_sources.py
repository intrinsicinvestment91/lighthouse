"""The operator-declared observation source list (LHP-INTEL-01E).

*What has the operator asked Lighthouse to look at?* — a work list of local repositories and
documents, and nothing else. This file is data.

**It is deliberately not the trust boundary.** That is `ObservationConfig.allowed_roots` in
`config.py`, sourced from the environment. A source declared here must resolve inside one of those
roots or it is refused, so this file can only ever *narrow* what gets observed, never widen it.

The separation is structural, not a check: there is **no field in this schema through which a root
could be declared**, and an unknown key is rejected rather than ignored. A sources file cannot grant
itself a boundary, because there is nowhere to write one down. An allowlist stored in the same file
as the paths it authorizes would be decorative — it would catch an operator's typo, not a bad edit
to that file.

Pure: reads one JSON file, validates it. Never reads the environment (`load_sources()` takes its
path as a required argument — `modules/data_paths.py` is the canonical reader of
`LIGHTHOUSE_DATA_DIR` and `DB_PATH`; this module deliberately does not copy that pattern), never
opens a database, never imports a collector.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

OBSERVATION_SOURCE_SCHEMA_VERSION = 1

# The two kinds of thing that can be observed — and they are exactly the two `scope_type` values
# `observation_sets` is already closed over. Reused, not reinvented: a third name for the same
# concept is how two vocabularies start meaning slightly different things.
ALLOWED_SOURCE_TYPES = frozenset({"repository", "document"})

# The closed key sets. Note what is absent from both: any word for a root, an allowlist, or a
# permission. That absence IS the trust model.
_CONFIG_KEYS = frozenset({"schema_version", "sources"})
_SOURCE_KEYS = frozenset({"type", "path", "enabled", "profile_id"})


class ObservationSourceError(Exception):
    """The declared source list is malformed, unknown, or tries to say something it may not say."""


@dataclass(frozen=True)
class ObservationSource:
    """One thing the operator has asked to have observed. A path, and what kind of thing it is."""

    type: str
    path: str
    enabled: bool = True
    profile_id: str | None = None

    def validate(self) -> None:
        if self.type not in ALLOWED_SOURCE_TYPES:
            raise ObservationSourceError(
                f"unknown source type: {self.type!r} "
                f"(expected one of {sorted(ALLOWED_SOURCE_TYPES)})"
            )
        if not isinstance(self.path, str) or not self.path.strip():
            raise ObservationSourceError("source path must be a non-empty string")
        if not isinstance(self.enabled, bool):
            raise ObservationSourceError("source enabled must be a boolean")
        if self.profile_id is not None and not isinstance(self.profile_id, str):
            raise ObservationSourceError("source profile_id must be a string or null")

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "path": self.path,
            "enabled": self.enabled,
            "profile_id": self.profile_id,
        }

    @classmethod
    def from_dict(cls, data: Any) -> ObservationSource:
        if not isinstance(data, dict):
            raise ObservationSourceError("every source must be an object")
        _require_exact_keys("source", data, _SOURCE_KEYS, required=frozenset({"type", "path"}))
        return cls(
            type=data["type"],
            path=data["path"],
            enabled=data.get("enabled", True),
            profile_id=data.get("profile_id"),
        )


@dataclass(frozen=True)
class ObservationSourceConfig:
    schema_version: int
    sources: tuple[ObservationSource, ...]

    def validate(self) -> None:
        if self.schema_version != OBSERVATION_SOURCE_SCHEMA_VERSION:
            raise ObservationSourceError(
                f"unsupported schema_version: {self.schema_version!r} "
                f"(this build understands {OBSERVATION_SOURCE_SCHEMA_VERSION})"
            )
        if not self.sources:
            raise ObservationSourceError("sources must be a non-empty list")

        seen: set[str] = set()
        for source in self.sources:
            source.validate()
            if source.path in seen:
                raise ObservationSourceError(f"duplicate source path: {source.path}")
            seen.add(source.path)

    def enabled_sources(self) -> tuple[ObservationSource, ...]:
        return tuple(source for source in self.sources if source.enabled)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "sources": [source.to_dict() for source in self.sources],
        }

    @classmethod
    def from_dict(cls, data: Any) -> ObservationSourceConfig:
        if not isinstance(data, dict):
            raise ObservationSourceError("the source list must be an object")
        _require_exact_keys("source list", data, _CONFIG_KEYS, required=frozenset({"sources"}))
        raw_sources = data.get("sources")
        if not isinstance(raw_sources, list):
            raise ObservationSourceError("sources must be a list")
        return cls(
            schema_version=data.get("schema_version", OBSERVATION_SOURCE_SCHEMA_VERSION),
            sources=tuple(ObservationSource.from_dict(item) for item in raw_sources),
        )


def load_sources(path: str | Path) -> ObservationSourceConfig:
    """Read and validate the declared source list. `path` is required — this module reads no env."""
    target = Path(path)
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ObservationSourceError(f"observation source list not found: {target}") from exc
    except (OSError, ValueError) as exc:
        raise ObservationSourceError(f"observation source list is unreadable: {exc}") from exc

    config = ObservationSourceConfig.from_dict(raw)
    config.validate()
    return config


def validate_sources(path: str | Path) -> dict[str, Any]:
    """Non-raising report over the declared source list — for operator inspection."""
    try:
        config = load_sources(path)
    except ObservationSourceError as exc:
        return {
            "valid": False,
            "errors": [str(exc)],
            "warnings": [],
            "source_count": 0,
            "enabled_count": 0,
        }

    warnings = [f"source {s.path} is disabled" for s in config.sources if not s.enabled]
    return {
        "valid": True,
        "errors": [],
        "warnings": warnings,
        "source_count": len(config.sources),
        "enabled_count": len(config.enabled_sources()),
    }


def _require_exact_keys(
    label: str,
    data: dict[str, Any],
    expected: frozenset[str],
    *,
    required: frozenset[str] | None = None,
) -> None:
    # Unknown keys are rejected, not ignored. Ignoring them is how a file grows an `allowed_roots`
    # that silently does nothing — or, worse, that someone later decides to honour.
    unexpected = set(data) - expected
    if unexpected:
        raise ObservationSourceError(f"{label} carries unexpected keys: {sorted(unexpected)}")
    missing = (required or expected) - set(data)
    if missing:
        raise ObservationSourceError(f"{label} is missing required keys: {sorted(missing)}")
