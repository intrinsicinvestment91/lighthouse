"""Deterministic callsign normalization and enrichment (no registry/API lookups)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

CALLSIGN_SCHEMA_VERSION = 1

MILITARY_CALLSIGN_PREFIXES = {
    "PAT",
    "RCH",
    "REACH",
    "MUSL",
    "ZEUS",
    "OTIS",
    "ELVIS",
    "NOBLE",
    "DUKE",
    "ROPER",
    "TITUS",
    "CODY",
}

CALLSIGN_PREFIX_CATEGORIES = {
    "PAT": "priority_air_transport",
    "RCH": "air_mobility_command",
    "REACH": "air_mobility_command",
    "MUSL": "military_or_special_mission",
    "ZEUS": "military_or_special_mission",
    "OTIS": "military_or_special_mission",
    "ELVIS": "military_or_special_mission",
    "NOBLE": "air_defense_or_tactical",
    "DUKE": "military_or_special_mission",
    "ROPER": "military_or_special_mission",
    "TITUS": "military_or_special_mission",
    "CODY": "military_or_special_mission",
}

_PREFIX_RE = re.compile(r"^([A-Za-z]+)")


def normalize_callsign(value: str | None) -> str | None:
    """Strip, uppercase, collapse whitespace; return None for empty input."""
    if value is None:
        return None
    collapsed = " ".join(value.strip().split())
    if not collapsed:
        return None
    return collapsed.upper()


def callsign_prefix(callsign: str | None) -> str | None:
    """Extract leading alphabetic prefix from a normalized callsign."""
    normalized = normalize_callsign(callsign)
    if not normalized:
        return None
    match = _PREFIX_RE.match(normalized)
    if not match:
        return None
    return match.group(1)


def is_known_military_callsign(callsign: str | None) -> bool:
    """True when callsign has a known military-style prefix (not proof of military aircraft)."""
    prefix = callsign_prefix(callsign)
    return prefix is not None and prefix in MILITARY_CALLSIGN_PREFIXES


@dataclass
class CallsignEnrichment:
    callsign: str | None
    normalized_callsign: str | None
    prefix: str | None
    category: str | None
    military_style: bool
    confidence: float
    tags: list[str] = field(default_factory=list)
    schema_version: int = CALLSIGN_SCHEMA_VERSION

    def validate(self) -> None:
        if not (0.0 <= self.confidence <= 1.0):
            raise ValueError("confidence must be between 0.0 and 1.0")
        if self.schema_version < 1:
            raise ValueError("schema_version must be >= 1")
        if not isinstance(self.tags, list):
            raise ValueError("tags must be a list")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {
            "callsign": self.callsign,
            "normalized_callsign": self.normalized_callsign,
            "prefix": self.prefix,
            "category": self.category,
            "military_style": self.military_style,
            "confidence": self.confidence,
            "tags": list(self.tags),
            "schema_version": self.schema_version,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CallsignEnrichment:
        record = cls(
            callsign=data.get("callsign"),
            normalized_callsign=data.get("normalized_callsign"),
            prefix=data.get("prefix"),
            category=data.get("category"),
            military_style=bool(data.get("military_style", False)),
            confidence=float(data.get("confidence", 0.0)),
            tags=list(data.get("tags") or []),
            schema_version=int(data.get("schema_version", CALLSIGN_SCHEMA_VERSION)),
        )
        record.validate()
        return record


def enrich_callsign(value: str | None) -> CallsignEnrichment:
    """Build deterministic enrichment metadata for an observed callsign."""
    normalized = normalize_callsign(value)
    prefix = callsign_prefix(normalized)
    category = CALLSIGN_PREFIX_CATEGORIES.get(prefix) if prefix else None
    military_style = is_known_military_callsign(normalized)

    if not normalized:
        confidence = 0.0
    elif military_style:
        confidence = 0.8
    elif prefix:
        confidence = 0.3
    else:
        confidence = 0.1

    tags: list[str] = []
    if normalized:
        tags.append("callsign")
    if military_style:
        tags.append("military_style")
    if prefix and prefix in MILITARY_CALLSIGN_PREFIXES:
        tags.append(f"known_prefix:{prefix}")
    if category:
        tags.append(f"category:{category}")

    return CallsignEnrichment(
        callsign=value,
        normalized_callsign=normalized,
        prefix=prefix,
        category=category,
        military_style=military_style,
        confidence=confidence,
        tags=tags,
    )
