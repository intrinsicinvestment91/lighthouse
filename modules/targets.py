from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from modules.data_paths import data_path

TARGET_SCHEMA_VERSION = 1
DEFAULT_TARGETS_PATH = Path(data_path("targets.json"))

ALLOWED_SIGNALS = {
    "sec",
    "patent",
    "arxiv",
    "flight",
    "adsb",
    "narrative",
    "archive",
}

ALLOWED_PRIORITIES = {"low", "medium", "high"}


@dataclass
class TargetProfile:
    name: str
    entities: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    signals: list[str] = field(default_factory=list)
    regions: list[str] = field(default_factory=list)
    priority: str = "medium"
    enabled: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)
    schema_version: int = TARGET_SCHEMA_VERSION

    def __post_init__(self) -> None:
        self.entities = self.entities or []
        self.keywords = self.keywords or []
        self.signals = self.signals or []
        self.regions = self.regions or []
        self.metadata = self.metadata or {}
        if not self.priority:
            self.priority = "medium"
        if self.schema_version is None:
            self.schema_version = TARGET_SCHEMA_VERSION

        self.name = self.name.strip()
        self.priority = self.priority.strip().lower()
        self.signals = [s.strip().lower() for s in self.signals if isinstance(s, str) and s.strip()]
        self.regions = [r.strip().lower() for r in self.regions if isinstance(r, str) and r.strip()]
        self.entities = [e.strip() for e in self.entities if isinstance(e, str) and e.strip()]
        self.keywords = [k.strip() for k in self.keywords if isinstance(k, str) and k.strip()]

    def validate(self) -> None:
        if not self.name:
            raise ValueError("name must be non-empty")

        if self.schema_version < 1:
            raise ValueError("schema_version must be >= 1")

        if not isinstance(self.entities, list):
            raise ValueError("entities must be a list")
        if not isinstance(self.keywords, list):
            raise ValueError("keywords must be a list")
        if not isinstance(self.signals, list):
            raise ValueError("signals must be a list")
        if not isinstance(self.regions, list):
            raise ValueError("regions must be a list")
        if not isinstance(self.metadata, dict):
            raise ValueError("metadata must be a dict")

        if self.priority not in ALLOWED_PRIORITIES:
            raise ValueError("priority must be one of: low, medium, high")

        for signal in self.signals:
            if signal not in ALLOWED_SIGNALS:
                raise ValueError(f"invalid signal: {signal}")

        if not self.entities and not self.keywords and not self.regions:
            raise ValueError("target must include at least one entity, keyword, or region")

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "entities": self.entities,
            "keywords": self.keywords,
            "signals": self.signals,
            "regions": self.regions,
            "priority": self.priority,
            "enabled": self.enabled,
            "metadata": self.metadata,
            "schema_version": self.schema_version,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TargetProfile:
        return cls(
            name=data.get("name", ""),
            entities=data.get("entities") or [],
            keywords=data.get("keywords") or [],
            signals=data.get("signals") or [],
            regions=data.get("regions") or [],
            priority=data.get("priority", "medium"),
            enabled=data.get("enabled", True),
            metadata=data.get("metadata") or {},
            schema_version=data.get("schema_version", TARGET_SCHEMA_VERSION),
        )


@dataclass
class TargetConfig:
    targets: list[TargetProfile] = field(default_factory=list)
    schema_version: int = TARGET_SCHEMA_VERSION

    def __post_init__(self) -> None:
        self.targets = self.targets or []
        if self.schema_version is None:
            self.schema_version = TARGET_SCHEMA_VERSION
        self.targets = [
            TargetProfile.from_dict(item) if isinstance(item, dict) else item
            for item in self.targets
        ]

    def validate(self) -> None:
        if self.schema_version < 1:
            raise ValueError("schema_version must be >= 1")

        if not isinstance(self.targets, list):
            raise ValueError("targets must be a list")

        seen: set[str] = set()
        for target in self.targets:
            target.validate()
            key = target.name.casefold()
            if key in seen:
                raise ValueError(f"duplicate target name: {target.name}")
            seen.add(key)

    def enabled_targets(self) -> list[TargetProfile]:
        return [t for t in self.targets if t.enabled]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "targets": [t.to_dict() for t in self.targets],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TargetConfig:
        return cls(
            targets=data.get("targets") or [],
            schema_version=data.get("schema_version", TARGET_SCHEMA_VERSION),
        )


def load_targets(path: str | Path = DEFAULT_TARGETS_PATH) -> TargetConfig:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    config = TargetConfig.from_dict(raw)
    config.validate()
    return config


def validate_targets(path: str | Path = DEFAULT_TARGETS_PATH) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []

    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        config = TargetConfig.from_dict(raw)
        config.validate()
    except Exception as exc:
        errors.append(str(exc))
        return {
            "valid": False,
            "errors": errors,
            "warnings": warnings,
            "target_count": 0,
            "enabled_count": 0,
        }

    for target in config.targets:
        if not target.signals:
            warnings.append(f"target {target.name} has no signals")
        if not target.enabled:
            warnings.append(f"target {target.name} is disabled")
        if not target.keywords:
            warnings.append(f"target {target.name} has no keywords")

    return {
        "valid": True,
        "errors": errors,
        "warnings": warnings,
        "target_count": len(config.targets),
        "enabled_count": len(config.enabled_targets()),
    }
