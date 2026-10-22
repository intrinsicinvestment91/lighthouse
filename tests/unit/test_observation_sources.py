"""LHP-INTEL-01E — the operator-declared observation source list.

This file is the *work list*: which repositories and documents the operator has asked Lighthouse to
observe. It is deliberately NOT the trust boundary. The boundary is
`ObservationConfig.allowed_roots`, which lives in `config.py` and comes from the environment — so a
source file can only ever *narrow* what is observed, never widen it.

That separation is enforced structurally rather than by a check: **the schema has no field through
which a root could be declared**, and unknown keys are rejected. A sources file cannot grant itself
a boundary, because there is nowhere to write one down. (An allowlist stored in the same file as the
paths it authorizes would be decorative — it would catch operator typos, not a bad edit to that
file.)

Pure: this module reads one JSON file and validates it. It never reads the environment, never opens
a database, and never imports a collector.
"""

import ast
import json
from pathlib import Path

import pytest

from modules import observation_sources
from modules.observation_sources import (
    ALLOWED_SOURCE_TYPES,
    OBSERVATION_SOURCE_SCHEMA_VERSION,
    ObservationSource,
    ObservationSourceError,
    load_sources,
    validate_sources,
)

# -- fixtures ------------------------------------------------------------------------------


def write_sources(path: Path, payload: dict) -> Path:
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def valid_payload(**overrides) -> dict:
    payload = {
        "schema_version": OBSERVATION_SOURCE_SCHEMA_VERSION,
        "sources": [
            {"type": "repository", "path": "/work/widget"},
            {"type": "document", "path": "/work/spec.pdf"},
        ],
    }
    payload.update(overrides)
    return payload


# -- the vocabulary ------------------------------------------------------------------------


def test_the_source_types_are_exactly_the_two_scope_types_the_database_already_knows():
    # Reused, not reinvented: `scope_type` in `observation_sets` is already closed over exactly
    # these two. A third name for the same concept is how vocabularies drift apart.
    assert frozenset({"repository", "document"}) == ALLOWED_SOURCE_TYPES


def test_an_unknown_source_type_is_rejected():
    with pytest.raises(ObservationSourceError, match="type"):
        ObservationSource(type="database", path="/work/widget").validate()


def test_a_source_must_carry_a_path():
    with pytest.raises(ObservationSourceError, match="path"):
        ObservationSource(type="repository", path="   ").validate()


def test_a_source_is_enabled_unless_it_says_otherwise():
    assert ObservationSource(type="repository", path="/work/widget").enabled is True


# -- the file ------------------------------------------------------------------------------


def test_a_valid_source_file_loads(tmp_path: Path):
    config = load_sources(write_sources(tmp_path / "sources.json", valid_payload()))

    assert config.schema_version == OBSERVATION_SOURCE_SCHEMA_VERSION
    assert [(s.type, s.path) for s in config.sources] == [
        ("repository", "/work/widget"),
        ("document", "/work/spec.pdf"),
    ]


def test_only_enabled_sources_are_offered_for_observation(tmp_path: Path):
    payload = valid_payload(
        sources=[
            {"type": "repository", "path": "/work/on"},
            {"type": "repository", "path": "/work/off", "enabled": False},
        ]
    )
    config = load_sources(write_sources(tmp_path / "sources.json", payload))

    assert [s.path for s in config.enabled_sources()] == ["/work/on"]


def test_an_unknown_schema_version_is_rejected(tmp_path: Path):
    payload = valid_payload(schema_version=OBSERVATION_SOURCE_SCHEMA_VERSION + 1)

    with pytest.raises(ObservationSourceError, match="schema_version"):
        load_sources(write_sources(tmp_path / "sources.json", payload))


def test_a_duplicate_path_is_rejected(tmp_path: Path):
    payload = valid_payload(
        sources=[
            {"type": "repository", "path": "/work/widget"},
            {"type": "repository", "path": "/work/widget"},
        ]
    )

    with pytest.raises(ObservationSourceError, match="duplicate"):
        load_sources(write_sources(tmp_path / "sources.json", payload))


def test_an_empty_source_list_is_rejected(tmp_path: Path):
    with pytest.raises(ObservationSourceError, match="sources"):
        load_sources(write_sources(tmp_path / "sources.json", valid_payload(sources=[])))


def test_a_malformed_file_fails_closed(tmp_path: Path):
    path = tmp_path / "sources.json"
    path.write_text("{ not json", encoding="utf-8")

    with pytest.raises(ObservationSourceError):
        load_sources(path)


def test_a_missing_file_fails_closed(tmp_path: Path):
    with pytest.raises(ObservationSourceError, match="not found"):
        load_sources(tmp_path / "absent.json")


# -- the boundary cannot be widened from here ----------------------------------------------


def test_the_source_file_cannot_declare_a_root(tmp_path: Path):
    """The whole trust model in one test.

    `allowed_roots` is the authoritative policy boundary and it lives in `config.py`. If a sources
    file could name its own roots it would be self-authorizing: `{"allowed_roots": ["/"],
    "sources": [{"path": "/etc/shadow"}]}` would pass its own check. So there is no such field, and
    an unknown key is rejected rather than ignored — widening is unrepresentable, not merely
    forbidden.
    """
    payload = valid_payload()
    payload["allowed_roots"] = ["/"]

    with pytest.raises(ObservationSourceError, match="unexpected"):
        load_sources(write_sources(tmp_path / "sources.json", payload))


def test_a_source_entry_cannot_declare_a_root_either(tmp_path: Path):
    payload = valid_payload(
        sources=[{"type": "repository", "path": "/etc", "allowed_roots": ["/"]}]
    )

    with pytest.raises(ObservationSourceError, match="unexpected"):
        load_sources(write_sources(tmp_path / "sources.json", payload))


def test_no_key_in_the_schema_could_ever_hold_a_root():
    config_keys = observation_sources._CONFIG_KEYS
    source_keys = observation_sources._SOURCE_KEYS

    assert config_keys == frozenset({"schema_version", "sources"})
    assert source_keys == frozenset({"type", "path", "enabled", "profile_id"})
    assert not [k for k in config_keys | source_keys if "root" in k or "allow" in k]


# -- the report ----------------------------------------------------------------------------


def test_validate_sources_reports_a_valid_file(tmp_path: Path):
    report = validate_sources(write_sources(tmp_path / "sources.json", valid_payload()))

    assert report["valid"] is True
    assert report["errors"] == []
    assert report["source_count"] == 2
    assert report["enabled_count"] == 2


def test_validate_sources_reports_an_invalid_file_without_raising(tmp_path: Path):
    payload = valid_payload(sources=[{"type": "database", "path": "/work/x"}])
    report = validate_sources(write_sources(tmp_path / "sources.json", payload))

    assert report["valid"] is False
    assert report["errors"]
    assert report["source_count"] == 0


# -- purity: this module is not the trust boundary and must not pretend to be ---------------

_ALLOWED_IMPORTS = frozenset(
    {
        "__future__",
        "dataclasses",
        "json",
        "pathlib",
        "typing",
    }
)


def _module_source() -> str:
    return Path(observation_sources.__file__).read_text(encoding="utf-8")


def test_the_module_never_reads_the_environment():
    """This module never reads the environment; `load_sources()` takes its path as an argument.

    `modules/data_paths.py` is the canonical reader of `LIGHTHOUSE_DATA_DIR` and `DB_PATH`.
    Observation intake roots arrive via `ObservationConfig.allowed_roots` in `config.py`.

    Checked against the parse tree, not the file text: prose that *mentions* the environment is
    fine, code that *reads* it is not.
    """
    reads = {"getenv", "environ", "putenv", "environb"}
    touched = {
        node.attr
        for node in ast.walk(ast.parse(_module_source()))
        if isinstance(node, ast.Attribute)
    } | {node.id for node in ast.walk(ast.parse(_module_source())) if isinstance(node, ast.Name)}

    assert not touched & reads


def test_the_module_imports_nothing_impure():
    imported: set[str] = set()
    for node in ast.walk(ast.parse(_module_source())):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            imported.add(node.module or "")

    assert not imported & {
        "os",
        "subprocess",
        "socket",
        "httpx",
        "anthropic",
        "modules.db",
        "modules.repository_intake",
        "modules.document_intake",
    }
    assert imported <= _ALLOWED_IMPORTS
