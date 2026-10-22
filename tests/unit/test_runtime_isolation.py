"""Regression tests for pytest runtime-data isolation (LHP-TEST-ISOLATION-01A)."""

from __future__ import annotations

import ast
import os
import shutil
import subprocess
import sys
import tomllib
import types

import pytest

from tests.runtime_isolation import (
    RuntimeIsolationError,
    apply_runtime_isolation,
    find_callsign_dual_bind_violations,
    get_session_result,
    path_inside_repo,
    protected_files_unchanged,
    repo_root,
    scan_static_repo_data_writes,
    snapshot_protected_files,
)

REPO = repo_root()
PROTECTED_STATE_DB = "1386ecb5cdcdb29e840f8f1b7386cd7d724da768893d4e7b0bdeaac42d2dcba5"
PROTECTED_TEST_HTML = "b633a587c652d02386c4f16f8c6f6aab7352d97f16367c3c40576214372dd628"


@pytest.fixture
def disposable_environ() -> dict[str, str]:
    return {"PATH": os.environ.get("PATH", "")}


def test_missing_path_variables_create_external_session_root(disposable_environ):
    result = apply_runtime_isolation(
        disposable_environ,
        repo_root_path=REPO,
        imported_modules=set(),
    )
    try:
        assert result.operator_owned is False
        assert result.session_root is not None
        assert result.data_dir == result.session_root / "data"
        assert result.db_path == result.data_dir / "state.db"
        assert not path_inside_repo(result.data_dir, REPO)
        assert not path_inside_repo(result.db_path, REPO)
        assert disposable_environ["LIGHTHOUSE_DATA_DIR"] == str(result.data_dir)
        assert disposable_environ["DB_PATH"] == str(result.db_path)
        assert disposable_environ["PYTHONDONTWRITEBYTECODE"] == "1"
    finally:
        if result.session_root is not None:
            shutil.rmtree(result.session_root, ignore_errors=True)


def test_explicit_safe_external_variables_are_preserved(disposable_environ, tmp_path):
    external_root = tmp_path / "operator-runtime"
    external_root.mkdir()
    disposable_environ["LIGHTHOUSE_DATA_DIR"] = str(external_root)
    disposable_environ["DB_PATH"] = str(external_root / "state.db")

    result = apply_runtime_isolation(
        disposable_environ,
        repo_root_path=REPO,
        imported_modules=set(),
    )

    assert result.operator_owned is True
    assert result.session_root is None
    assert result.data_dir == external_root
    assert result.db_path == external_root / "state.db"


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("LIGHTHOUSE_DATA_DIR", ""),
        ("LIGHTHOUSE_DATA_DIR", "   "),
        ("DB_PATH", ""),
        ("DB_PATH", "   "),
    ],
)
def test_empty_or_whitespace_values_refuse(disposable_environ, key, value):
    disposable_environ[key] = value
    with pytest.raises(RuntimeIsolationError, match="must not be empty or whitespace-only"):
        apply_runtime_isolation(
            disposable_environ,
            repo_root_path=REPO,
            imported_modules=set(),
        )


def test_repository_relative_paths_refuse(disposable_environ):
    disposable_environ["LIGHTHOUSE_DATA_DIR"] = "data"
    with pytest.raises(RuntimeIsolationError, match="inside the repository"):
        apply_runtime_isolation(
            disposable_environ,
            repo_root_path=REPO,
            imported_modules=set(),
        )

    disposable_environ.clear()
    disposable_environ["PATH"] = os.environ.get("PATH", "")
    disposable_environ["DB_PATH"] = str(REPO / "data" / "state.db")
    with pytest.raises(RuntimeIsolationError, match="inside the repository"):
        apply_runtime_isolation(
            disposable_environ,
            repo_root_path=REPO,
            imported_modules=set(),
        )


def test_derived_db_path_is_correct(disposable_environ, tmp_path):
    external_root = tmp_path / "derived"
    external_root.mkdir()
    disposable_environ["LIGHTHOUSE_DATA_DIR"] = str(external_root)

    result = apply_runtime_isolation(
        disposable_environ,
        repo_root_path=REPO,
        imported_modules=set(),
    )

    assert result.db_path == external_root / "state.db"


def test_db_path_only_refuses_repo_relative_data_dir(disposable_environ, tmp_path):
    """Safe external DB_PATH without LIGHTHOUSE_DATA_DIR must fail closed.

    Production resolve_data_dir() defaults to repository-relative ``data`` when
    the variable is absent. Isolation must refuse rather than leave that default
    in force.
    """
    disposable_environ["DB_PATH"] = str(tmp_path / "external-state.db")
    assert "LIGHTHOUSE_DATA_DIR" not in disposable_environ

    with pytest.raises(RuntimeIsolationError, match="inside the repository"):
        apply_runtime_isolation(
            disposable_environ,
            repo_root_path=REPO,
            imported_modules=set(),
        )

    assert "LIGHTHOUSE_DATA_DIR" not in disposable_environ
    assert disposable_environ["DB_PATH"] == str(tmp_path / "external-state.db")


def test_subprocess_environment_receives_safe_paths(disposable_environ, tmp_path):
    external_root = tmp_path / "subprocess-root"
    external_root.mkdir()
    disposable_environ["LIGHTHOUSE_DATA_DIR"] = str(external_root)
    disposable_environ["DB_PATH"] = str(external_root / "state.db")
    apply_runtime_isolation(
        disposable_environ,
        repo_root_path=REPO,
        imported_modules=set(),
    )

    probe = (
        "import json, os\n"
        "print(json.dumps({"
        "'data': os.environ.get('LIGHTHOUSE_DATA_DIR'), "
        "'db': os.environ.get('DB_PATH'), "
        "'no_bytecode': os.environ.get('PYTHONDONTWRITEBYTECODE')"
        "}))\n"
    )
    env = os.environ.copy()
    env.update(disposable_environ)
    result = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=REPO,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    payload = __import__("json").loads(result.stdout.strip())
    assert payload["data"] == str(external_root)
    assert payload["db"] == str(external_root / "state.db")
    assert payload["no_bytecode"] == "1"


def test_guarded_modules_imported_before_isolation_fail_closed(disposable_environ):
    had_previous = "modules.db" in sys.modules
    previous = sys.modules.get("modules.db")
    fake_db = types.ModuleType("modules.db")
    fake_db.DB_PATH = REPO / "data" / "state.db"
    sys.modules["modules.db"] = fake_db
    try:
        with pytest.raises(RuntimeIsolationError, match="imported before runtime isolation"):
            apply_runtime_isolation(
                disposable_environ,
                repo_root_path=REPO,
                imported_modules=set(sys.modules.keys()),
            )
    finally:
        if had_previous:
            sys.modules["modules.db"] = previous
        else:
            sys.modules.pop("modules.db", None)


def test_live_pytest_session_paths_are_outside_repo(pytestconfig):
    result = get_session_result()
    assert result is not None
    assert not path_inside_repo(result.data_dir, REPO)
    assert not path_inside_repo(result.db_path, REPO)
    assert os.environ["LIGHTHOUSE_DATA_DIR"] == str(result.data_dir)
    assert os.environ["DB_PATH"] == str(result.db_path)

    pyproject = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))
    addopts = pyproject["tool"]["pytest"]["ini_options"]["addopts"]
    assert addopts == ["-p", "tests.runtime_isolation", "-p", "no:cacheprovider"]
    assert pytestconfig.pluginmanager.get_plugin("cacheprovider") is None

    assert sys.dont_write_bytecode is True
    assert os.environ.get("PYTHONDONTWRITEBYTECODE") == "1"
    plugin_pycache = REPO / "tests" / "__pycache__"
    if plugin_pycache.is_dir():
        for entry in plugin_pycache.iterdir():
            if entry.suffix != ".pyc":
                continue
            # Plugin import may emit these two prefixes; the plugin cannot
            # retroactively suppress its own import bytecode. Not runtime data.
            assert entry.name.startswith("__init__.") or entry.name.startswith(
                "runtime_isolation."
            ), entry.name


def test_protected_source_files_unchanged_during_session():
    result = get_session_result()
    assert result is not None
    assert protected_files_unchanged(REPO, result.protected_snapshots)

    baseline_state = next(
        item for item in result.protected_snapshots if item.path.name == "state.db"
    )
    baseline_html = next(
        item for item in result.protected_snapshots if item.path.name == "test.html"
    )
    current = snapshot_protected_files(REPO)
    state = next(item for item in current if item.path.name == "state.db")
    html = next(item for item in current if item.path.name == "test.html")

    assert state == baseline_state
    assert html == baseline_html
    if baseline_state.existed:
        assert state.sha256 == PROTECTED_STATE_DB
    if baseline_html.existed:
        assert html.sha256 == PROTECTED_TEST_HTML


def test_callsign_dual_bind_guard_has_no_violations():
    assert find_callsign_dual_bind_violations(REPO) == []


def test_static_repo_data_write_guard_documents_limitations():
    violations = scan_static_repo_data_writes(REPO)
    assert violations == []
    # Honest contract of the recursive AST scan:
    # - roots: repo *.py plus modules/**, portal/**, scripts/**, tests/** (rglob)
    # - tests/ is not exempt; a literal data/... write there is a violation
    # - temp-rooted paths (tmp_path, /tmp/...) are not static data/... literals
    # - cannot see Path(archive_cfg.output_dir) or other non-literal destinations
    # - cannot see runtime-built f-strings or db.DB_PATH after import-time binding
    # - dataclass metadata like local_path="data/archives/test.html" is not a write
    # - the dual-bind AST guard covers the known callsign secondary-writer bypass


def test_runtime_isolation_never_replaces_subprocess_popen():
    """AST pin: the isolation plugin must never import or name subprocess.Popen."""
    source = (REPO / "tests" / "runtime_isolation.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    violations: list[str] = []

    def _note(node: ast.AST, detail: str) -> None:
        lineno = getattr(node, "lineno", 0)
        violations.append(f"{type(node).__name__}:{lineno}: {detail}")

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "subprocess" or alias.name.startswith("subprocess."):
                    _note(node, f"import {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module == "subprocess" or module.startswith("subprocess."):
                _note(node, f"from {module} import")
        if isinstance(node, ast.Name) and "popen" in node.id.lower():
            _note(node, f"identifier {node.id!r}")
        elif isinstance(node, ast.Attribute) and "popen" in node.attr.lower():
            _note(node, f"attribute {node.attr!r}")
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if "popen" in node.name.lower():
                _note(node, f"function {node.name!r}")
        elif isinstance(node, ast.ClassDef) and "popen" in node.name.lower():
            _note(node, f"class {node.name!r}")
        elif isinstance(node, ast.alias):
            if "popen" in node.name.lower():
                _note(node, f"alias {node.name!r}")
            if node.asname is not None and "popen" in node.asname.lower():
                _note(node, f"alias as {node.asname!r}")
        elif isinstance(node, ast.arg) and "popen" in node.arg.lower():
            _note(node, f"identifier {node.arg!r}")

    assert violations == []
