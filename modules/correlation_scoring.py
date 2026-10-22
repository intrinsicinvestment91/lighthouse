"""Deterministic weighted correlation scoring (no ML, no external lookups)."""

from __future__ import annotations

from typing import Any

MIN_CORRELATION_SCORE = 0.0
MAX_CORRELATION_SCORE = 10.0

SIGNAL_BASE_WEIGHTS = {
    "sec": 2.5,
    "adsb": 2.0,
    "flight": 1.5,
    "arxiv": 1.0,
    "patent": 1.0,
    "narrative": 0.75,
    "archive": 0.5,
}

_QUALITY_BONUS_CAP = 2.0
_MULTI_SIGNAL_BONUS_CAP = 2.5

_SEC_MATERIAL_ITEMS = ("1.01", "2.01", "7.01", "8.01")
_KNOWN_CALLSIGN_PREFIXES = ("PAT", "MUSL", "ZEUS", "OTIS", "ELVIS")
_ICAO_HEX_KEYS = frozenset({"hex", "icao24", "icao_hex"})


def clamp_score(value: float) -> float:
    """Clamp to 0–10 and round to one decimal place."""
    bounded = max(MIN_CORRELATION_SCORE, min(MAX_CORRELATION_SCORE, value))
    return round(bounded, 1)


def normalize_signals(signals: list[str] | None) -> list[str]:
    """Lowercase, strip, deduplicate, and sort signals for determinism."""
    if not signals:
        return []
    seen: set[str] = set()
    normalized: list[str] = []
    for raw in signals:
        token = str(raw).strip().lower()
        if not token or token in seen:
            continue
        seen.add(token)
        normalized.append(token)
    return sorted(normalized)


def _walk_values(value: Any):
    """Yield nested dict/list/string values recursively."""
    if isinstance(value, dict):
        yield value
        for nested in value.values():
            yield from _walk_values(nested)
    elif isinstance(value, list):
        for item in value:
            yield from _walk_values(item)
    elif isinstance(value, str):
        yield value


def evidence_contains_key_value(evidence: dict[str, Any], key: str, expected: Any) -> bool:
    """Return True if any nested dict contains key with the expected value."""
    return any(isinstance(obj, dict) and obj.get(key) == expected for obj in _walk_values(evidence))


def evidence_contains_text(evidence: dict[str, Any], terms: list[str]) -> bool:
    """Case-insensitive search for any term in nested string values."""
    needles = [t.lower() for t in terms if t]
    if not needles:
        return False
    for obj in _walk_values(evidence):
        if isinstance(obj, str):
            haystack = obj.lower()
            if any(term in haystack for term in needles):
                return True
    return False


def _is_ae_range_hex(value: str | None) -> bool:
    """Return True for U.S. military ICAO allocation (AE0000–AEFFFF)."""
    if value is None:
        return False
    normalized = value.strip().lower()
    if normalized.startswith("~"):
        normalized = normalized[1:].lstrip()
    return normalized.startswith("ae")


def _evidence_has_ae_range_hex(evidence: dict[str, Any]) -> bool:
    for obj in _walk_values(evidence):
        if not isinstance(obj, dict):
            continue
        for key, raw in obj.items():
            if key in _ICAO_HEX_KEYS and _is_ae_range_hex(str(raw) if raw is not None else None):
                return True
    return False


def _parse_sec_items(raw: Any) -> set[str]:
    if raw is None:
        return set()
    if isinstance(raw, str):
        return {part.strip() for part in raw.split(",") if part.strip()}
    if isinstance(raw, list):
        return {str(part).strip() for part in raw if str(part).strip()}
    value = str(raw).strip()
    return {value} if value else set()


def _evidence_has_sec_material_items(evidence: dict[str, Any]) -> bool:
    material = set(_SEC_MATERIAL_ITEMS)
    for obj in _walk_values(evidence):
        if not isinstance(obj, dict) or "items" not in obj:
            continue
        if _parse_sec_items(obj.get("items")).intersection(material):
            return True
    return False


def _evidence_has_callsign_enrichment_military_style(evidence: dict[str, Any]) -> bool:
    for obj in _walk_values(evidence):
        if not isinstance(obj, dict):
            continue
        enrichment = obj.get("callsign_enrichment")
        if isinstance(enrichment, dict) and enrichment.get("military_style") is True:
            return True
    return False


def evidence_quality_bonus(evidence: dict[str, Any]) -> float:
    """Deterministic evidence-quality bonus, capped at 2.0."""
    bonus = 0.0

    if evidence_contains_key_value(evidence, "military", True):
        bonus += 1.0
    if bonus >= _QUALITY_BONUS_CAP:
        return _QUALITY_BONUS_CAP

    if evidence_contains_key_value(evidence, "military_style_callsign", True):
        bonus += 0.75
    if bonus >= _QUALITY_BONUS_CAP:
        return _QUALITY_BONUS_CAP

    if _evidence_has_callsign_enrichment_military_style(evidence):
        bonus += 0.5
    if bonus >= _QUALITY_BONUS_CAP:
        return _QUALITY_BONUS_CAP

    if _evidence_has_ae_range_hex(evidence):
        bonus += 0.5
    if bonus >= _QUALITY_BONUS_CAP:
        return _QUALITY_BONUS_CAP

    if _evidence_has_sec_material_items(evidence):
        bonus += 0.5
    if bonus >= _QUALITY_BONUS_CAP:
        return _QUALITY_BONUS_CAP

    if evidence_contains_text(evidence, list(_KNOWN_CALLSIGN_PREFIXES)):
        bonus += 0.25

    return min(bonus, _QUALITY_BONUS_CAP)


def multi_signal_bonus(signals: list[str]) -> float:
    """Bonus for signal count and high-value combinations, capped at 2.5."""
    normalized = normalize_signals(signals)
    unique = set(normalized)
    count = len(unique)
    bonus = 0.0

    if count >= 4:
        bonus += 1.5
    elif count == 3:
        bonus += 1.0
    elif count == 2:
        bonus += 0.5

    if {"sec", "adsb", "flight"}.issubset(unique):
        bonus += 1.0
    if {"sec", "flight"}.issubset(unique):
        bonus += 0.5
    if {"adsb", "flight"}.issubset(unique):
        bonus += 0.5

    return min(bonus, _MULTI_SIGNAL_BONUS_CAP)


def score_correlation(
    *,
    signals: list[str],
    evidence: dict[str, Any] | None = None,
    base_score: float | None = None,
) -> float:
    """Compute a weighted correlation score from signals and evidence."""
    normalized = normalize_signals(signals)

    if base_score is not None:
        total = base_score
    elif not normalized:
        total = 0.0
    else:
        total = sum(SIGNAL_BASE_WEIGHTS.get(s, 0.0) for s in normalized)

    total += multi_signal_bonus(normalized)
    total += evidence_quality_bonus(evidence or {})
    return clamp_score(total)
