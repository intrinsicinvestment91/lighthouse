from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from modules.targets import ALLOWED_PRIORITIES, ALLOWED_SIGNALS, TargetConfig, load_targets

ROUTER_SCHEMA_VERSION = 1

_PRIORITY_RANK = {"low": 0, "medium": 1, "high": 2}


@dataclass
class SignalRoute:
    signal: str
    targets: list[str] = field(default_factory=list)
    entities: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)
    regions: list[str] = field(default_factory=list)
    priority: str = "medium"
    enabled: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)
    schema_version: int = ROUTER_SCHEMA_VERSION

    def __post_init__(self) -> None:
        self.signal = self.signal.strip().lower()
        self.targets = self.targets or []
        self.entities = self.entities or []
        self.keywords = self.keywords or []
        self.regions = self.regions or []
        self.metadata = self.metadata or {}
        if not self.priority:
            self.priority = "medium"
        if self.schema_version is None:
            self.schema_version = ROUTER_SCHEMA_VERSION

        self.priority = self.priority.strip().lower()
        self.targets = _dedupe_list(self.targets, case_insensitive=False)
        self.entities = _dedupe_list(self.entities, case_insensitive=False)
        self.keywords = _dedupe_list(self.keywords, case_insensitive=True)
        self.regions = _dedupe_list(
            [r.strip().lower() for r in self.regions if isinstance(r, str) and r.strip()],
            case_insensitive=False,
        )

    def validate(self) -> None:
        if not self.signal:
            raise ValueError("signal must be non-empty")

        if self.signal not in ALLOWED_SIGNALS:
            raise ValueError(f"invalid signal: {self.signal}")

        if self.schema_version < 1:
            raise ValueError("schema_version must be >= 1")

        if not isinstance(self.targets, list):
            raise ValueError("targets must be a list")
        if not isinstance(self.entities, list):
            raise ValueError("entities must be a list")
        if not isinstance(self.keywords, list):
            raise ValueError("keywords must be a list")
        if not isinstance(self.regions, list):
            raise ValueError("regions must be a list")
        if not isinstance(self.metadata, dict):
            raise ValueError("metadata must be a dict")

        if self.priority not in ALLOWED_PRIORITIES:
            raise ValueError("priority must be one of: low, medium, high")

        if (
            self.enabled
            and not self.targets
            and not self.entities
            and not self.keywords
            and not self.regions
        ):
            raise ValueError(
                "enabled route must include at least one target, entity, keyword, or region"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "signal": self.signal,
            "targets": self.targets,
            "entities": self.entities,
            "keywords": self.keywords,
            "regions": self.regions,
            "priority": self.priority,
            "enabled": self.enabled,
            "metadata": self.metadata,
            "schema_version": self.schema_version,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SignalRoute:
        return cls(
            signal=data.get("signal", ""),
            targets=data.get("targets") or [],
            entities=data.get("entities") or [],
            keywords=data.get("keywords") or [],
            regions=data.get("regions") or [],
            priority=data.get("priority", "medium"),
            enabled=data.get("enabled", True),
            metadata=data.get("metadata") or {},
            schema_version=data.get("schema_version", ROUTER_SCHEMA_VERSION),
        )


def _dedupe_list(values: list[str], *, case_insensitive: bool) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not isinstance(value, str):
            continue
        cleaned = value.strip()
        if not cleaned:
            continue
        key = cleaned.casefold() if case_insensitive else cleaned
        if key in seen:
            continue
        seen.add(key)
        result.append(cleaned if not case_insensitive else cleaned)
    return result


def _merge_priority(current: str, incoming: str) -> str:
    current_rank = _PRIORITY_RANK.get(current, 0)
    incoming_rank = _PRIORITY_RANK.get(incoming, 0)
    return incoming if incoming_rank > current_rank else current


def _append_unique(
    existing: list[str],
    values: list[str],
    *,
    case_insensitive: bool = False,
) -> None:
    seen: set[str] = set()
    for item in existing:
        key = item.casefold() if case_insensitive else item
        seen.add(key)
    for value in values:
        if not isinstance(value, str):
            continue
        cleaned = value.strip()
        if not cleaned:
            continue
        key = cleaned.casefold() if case_insensitive else cleaned
        if key in seen:
            continue
        seen.add(key)
        existing.append(cleaned)


def build_signal_routes(config: TargetConfig) -> dict[str, SignalRoute]:
    config.validate()
    routes: dict[str, SignalRoute] = {}

    for target in config.enabled_targets():
        for signal in target.signals:
            if signal not in routes:
                routes[signal] = SignalRoute(signal=signal, priority=target.priority)
            route = routes[signal]
            _append_unique(route.targets, [target.name], case_insensitive=False)
            _append_unique(route.entities, target.entities, case_insensitive=False)
            _append_unique(route.keywords, target.keywords, case_insensitive=True)
            _append_unique(route.regions, target.regions, case_insensitive=False)
            route.priority = _merge_priority(route.priority, target.priority)

    for route in routes.values():
        route.validate()

    return routes


def build_signal_routes_from_file(path: str | Path | None = None) -> dict[str, SignalRoute]:
    if path is None:
        config = load_targets()
    else:
        config = load_targets(path)
    return build_signal_routes(config)


def route_summary(routes: dict[str, SignalRoute]) -> dict[str, Any]:
    target_names: set[str] = set()
    entity_names: set[str] = set()
    for route in routes.values():
        target_names.update(route.targets)
        entity_names.update(route.entities)

    return {
        "route_count": len(routes),
        "signals": sorted(routes.keys()),
        "targets": sorted(target_names),
        "entities": sorted(entity_names),
    }
