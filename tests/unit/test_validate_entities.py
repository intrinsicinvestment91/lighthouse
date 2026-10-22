import json

from scripts.validate_entities import validate_entities


def _write_entities(tmp_path, payload):
    path = tmp_path / "entities.json"
    path.write_text(json.dumps(payload))
    return path


def test_validate_entities_valid_file_passes(tmp_path):
    path = _write_entities(
        tmp_path,
        [
            {
                "name": "Tesla",
                "sec_cik": "0001318605",
                "assignee_patterns": ["Tesla", "Tesla, Inc."],
                "facilities": ["Gigafactory Texas"],
                "ipc_classes": ["B64G"],
                "region": "Austin, TX",
            }
        ],
    )

    result = validate_entities(str(path))
    assert result["valid"] is True
    assert result["errors"] == []
    assert result["warnings"] == []
    assert result["entity_count"] == 1


def test_validate_entities_missing_required_field_fails(tmp_path):
    path = _write_entities(
        tmp_path,
        [
            {
                "name": "Tesla",
                "sec_cik": "0001318605",
                "assignee_patterns": ["Tesla"],
                "facilities": ["Gigafactory Texas"],
                "ipc_classes": ["B64G"],
            }
        ],
    )

    result = validate_entities(str(path))
    assert result["valid"] is False
    assert any("missing required non-empty field: region" in err for err in result["errors"])


def test_validate_entities_invalid_cik_fails(tmp_path):
    path = _write_entities(
        tmp_path,
        [
            {
                "name": "Tesla",
                "sec_cik": "CIK-123",
                "assignee_patterns": ["Tesla"],
                "facilities": ["Gigafactory Texas"],
                "ipc_classes": ["B64G"],
                "region": "Austin, TX",
            }
        ],
    )

    result = validate_entities(str(path))
    assert result["valid"] is False
    assert any("sec_cik must be a numeric string" in err for err in result["errors"])


def test_validate_entities_duplicate_names_fail(tmp_path):
    path = _write_entities(
        tmp_path,
        [
            {
                "name": "Tesla",
                "sec_cik": "0001318605",
                "assignee_patterns": ["Tesla"],
                "facilities": ["Gigafactory Texas"],
                "ipc_classes": ["B64G"],
                "region": "Austin, TX",
            },
            {
                "name": "tesla",
                "sec_cik": "0001318605",
                "assignee_patterns": ["Tesla Motors"],
                "facilities": ["Fremont"],
                "ipc_classes": ["B64G"],
                "region": "Fremont, CA",
            },
        ],
    )

    result = validate_entities(str(path))
    assert result["valid"] is False
    assert any(err == "Duplicate entity name found: tesla" for err in result["errors"])


def test_validate_entities_empty_lists_are_warnings_only(tmp_path):
    path = _write_entities(
        tmp_path,
        [
            {
                "name": "Tesla",
                "sec_cik": "0001318605",
                "assignee_patterns": ["Tesla"],
                "facilities": [],
                "ipc_classes": [],
                "region": "Austin, TX",
            }
        ],
    )

    result = validate_entities(str(path))
    assert result["valid"] is True
    assert result["errors"] == []
    assert any("has no facilities" in warn for warn in result["warnings"])
    assert any("has no ipc_classes" in warn for warn in result["warnings"])
