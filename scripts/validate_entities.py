"""Local validation harness for data/entities.json."""

from __future__ import annotations

import json
import sys
from pathlib import Path


def _is_non_empty_string(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def validate_entities(path: str) -> dict:
    """Validate entities JSON structure and return deterministic results."""
    errors: list[str] = []
    warnings: list[str] = []

    data = json.loads(Path(path).read_text())
    if not isinstance(data, list):
        return {
            "valid": False,
            "errors": ["Root JSON must be a list of entity objects."],
            "warnings": [],
            "entity_count": 0,
        }

    seen_names: set[str] = set()
    for idx, item in enumerate(data):
        label = f"entry[{idx}]"
        if not isinstance(item, dict):
            errors.append(f"{label} must be an object.")
            continue

        name = item.get("name")
        sec_cik = item.get("sec_cik")
        region = item.get("region")
        assignee_patterns = item.get("assignee_patterns")
        facilities = item.get("facilities")
        ipc_classes = item.get("ipc_classes")

        entity_label = name if _is_non_empty_string(name) else label

        # Required fields
        if not _is_non_empty_string(name):
            errors.append(f"{label} missing required non-empty field: name")
        if not _is_non_empty_string(sec_cik):
            errors.append(f"Entity {entity_label} missing required non-empty field: sec_cik")
        if not _is_non_empty_string(region):
            errors.append(f"Entity {entity_label} missing required non-empty field: region")

        # Type checks
        if not isinstance(assignee_patterns, list):
            errors.append(f"Entity {entity_label} field assignee_patterns must be a list.")
        if not isinstance(facilities, list):
            errors.append(f"Entity {entity_label} field facilities must be a list.")
        if not isinstance(ipc_classes, list):
            errors.append(f"Entity {entity_label} field ipc_classes must be a list.")

        # Value checks
        if isinstance(sec_cik, str) and sec_cik and not sec_cik.isdigit():
            errors.append(f"Entity {entity_label} sec_cik must be a numeric string.")

        if _is_non_empty_string(name):
            normalized = name.strip().casefold()
            if normalized in seen_names:
                errors.append(f"Duplicate entity name found: {name.strip()}")
            else:
                seen_names.add(normalized)

        for field_name, values in (
            ("assignee_patterns", assignee_patterns),
            ("facilities", facilities),
            ("ipc_classes", ipc_classes),
        ):
            if not isinstance(values, list):
                continue
            for value in values:
                if not _is_non_empty_string(value):
                    errors.append(
                        f"Entity {entity_label} field {field_name} contains empty "
                        "or non-string value."
                    )

        # Warnings (non-fatal)
        if isinstance(facilities, list) and not facilities:
            warnings.append(f"Entity {entity_label} has no facilities.")
        if isinstance(ipc_classes, list) and not ipc_classes:
            warnings.append(f"Entity {entity_label} has no ipc_classes.")
        if isinstance(assignee_patterns, list):
            for pattern in assignee_patterns:
                if isinstance(pattern, str) and pattern.strip() and len(pattern.strip()) < 3:
                    warnings.append(
                        f"Entity {entity_label} has very short assignee_pattern: {pattern.strip()}"
                    )

    return {
        "valid": len(errors) == 0,
        "errors": errors,
        "warnings": warnings,
        "entity_count": len(data),
    }


def _main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("Usage: python scripts/validate_entities.py <path-to-entities.json>")
        return 1

    result = validate_entities(argv[1])
    if result["valid"]:
        print("VALIDATION PASSED")
    else:
        print("VALIDATION FAILED")

    if result["errors"]:
        print("Errors:")
        for message in result["errors"]:
            print(f"- {message}")

    if result["warnings"]:
        print("Warnings:")
        for message in result["warnings"]:
            print(f"- {message}")

    print(f"Entity count: {result['entity_count']}")
    return 0 if result["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv))
