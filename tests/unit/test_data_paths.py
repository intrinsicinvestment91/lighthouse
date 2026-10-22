"""Unit tests for modules.data_paths — canonical data-directory contract (LHP-V1-HARDEN-03B)."""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

from modules.data_paths import (
    DataPathError,
    data_path,
    legacy_default_db_path,
    resolve_data_dir,
    resolve_db_path,
)


def test_default_data_dir_is_relative_data(monkeypatch):
    monkeypatch.delenv("LIGHTHOUSE_DATA_DIR", raising=False)
    monkeypatch.delenv("DB_PATH", raising=False)
    assert resolve_data_dir() == Path("data")
    assert data_path("state.db") == "data/state.db"
    assert resolve_db_path() == Path("data/state.db")


def test_lighthouse_data_dir_is_the_data_root(monkeypatch):
    monkeypatch.setenv("LIGHTHOUSE_DATA_DIR", "/tmp/lhp-contract")
    monkeypatch.delenv("DB_PATH", raising=False)
    assert data_path("state.db") == "/tmp/lhp-contract/state.db"
    assert resolve_db_path() == Path("/tmp/lhp-contract/state.db")


def test_trailing_slash_stripped(monkeypatch):
    monkeypatch.setenv("LIGHTHOUSE_DATA_DIR", "/data/")
    monkeypatch.delenv("DB_PATH", raising=False)
    assert data_path("state.db") == "/data/state.db"


@pytest.mark.parametrize(
    "configured",
    [
        "/",
        "//",
        "///",
        "  //  ",
        " /// ",
    ],
)
def test_slash_only_roots_preserve_filesystem_root(monkeypatch, configured):
    monkeypatch.setenv("LIGHTHOUSE_DATA_DIR", configured)
    monkeypatch.delenv("DB_PATH", raising=False)
    assert resolve_data_dir() == Path("/")
    assert data_path("state.db") == "/state.db"


def test_padded_lighthouse_data_dir_trimmed(monkeypatch):
    monkeypatch.setenv("LIGHTHOUSE_DATA_DIR", "  /data/  ")
    monkeypatch.delenv("DB_PATH", raising=False)
    assert resolve_data_dir() == Path("/data")
    assert data_path("state.db") == "/data/state.db"


def test_padded_db_path_trimmed(monkeypatch):
    monkeypatch.setenv("LIGHTHOUSE_DATA_DIR", "/data")
    monkeypatch.setenv("DB_PATH", "  /other/state.db  ")
    assert resolve_db_path() == Path("/other/state.db")


@pytest.mark.parametrize(
    "rel",
    [
        "/abs/child",
        "../escape",
        "a/../b",
        "..",
    ],
)
def test_data_path_rejects_escape(monkeypatch, rel):
    monkeypatch.setenv("LIGHTHOUSE_DATA_DIR", "/data")
    monkeypatch.delenv("DB_PATH", raising=False)
    with pytest.raises(DataPathError):
        data_path(rel)


def test_data_path_accepts_valid_relative_child(monkeypatch):
    monkeypatch.setenv("LIGHTHOUSE_DATA_DIR", "/data")
    monkeypatch.delenv("DB_PATH", raising=False)
    assert data_path("patents/out.json") == "/data/patents/out.json"


def test_db_path_explicit_override(monkeypatch):
    monkeypatch.setenv("LIGHTHOUSE_DATA_DIR", "/data")
    monkeypatch.setenv("DB_PATH", "/other/state.db")
    assert resolve_db_path() == Path("/other/state.db")
    assert data_path("state.db") == "/data/state.db"


@pytest.mark.parametrize(
    ("env_name", "env_value"),
    [
        ("LIGHTHOUSE_DATA_DIR", ""),
        ("LIGHTHOUSE_DATA_DIR", "   "),
        ("DB_PATH", ""),
        ("DB_PATH", "   "),
    ],
)
def test_empty_or_whitespace_env_refused(monkeypatch, env_name, env_value):
    monkeypatch.delenv("LIGHTHOUSE_DATA_DIR", raising=False)
    monkeypatch.delenv("DB_PATH", raising=False)
    monkeypatch.setenv(env_name, env_value)
    with pytest.raises(DataPathError):
        if env_name == "LIGHTHOUSE_DATA_DIR":
            resolve_data_dir()
        else:
            resolve_db_path()


def test_db_path_derives_from_data_dir_at_import(monkeypatch, tmp_path):
    """db.DB_PATH follows LIGHTHOUSE_DATA_DIR when DB_PATH unset (subprocess import-time)."""
    data_root = tmp_path / "isolated-root"
    data_root.mkdir()
    probe = (
        "import json, os\n"
        f"os.environ['LIGHTHOUSE_DATA_DIR'] = {str(data_root)!r}\n"
        "if 'DB_PATH' in os.environ:\n"
        "    del os.environ['DB_PATH']\n"
        "from modules import db\n"
        "print(json.dumps({'db_path': str(db.DB_PATH)}))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=Path(__file__).resolve().parents[2],
        capture_output=True,
        text=True,
        check=True,
    )
    import json

    payload = json.loads(result.stdout.strip())
    assert payload["db_path"] == str(data_root / "state.db")


def test_cross_call_site_agreement_subprocess(tmp_path):
    """config, db, targets, and profile_activation must agree under one env (import-time)."""
    root = tmp_path / "agreement"
    root.mkdir()
    probe = (
        "import json, os\n"
        f"os.environ['LIGHTHOUSE_DATA_DIR'] = {str(root)!r}\n"
        "os.environ.pop('DB_PATH', None)\n"
        "import config\n"
        "from modules import db, targets\n"
        "from modules.profile_activation import _resolve_data_dir\n"
        "print(json.dumps({\n"
        "  'config_state_db': config._data_path('state.db'),\n"
        "  'db_path': str(db.DB_PATH),\n"
        "  'targets': str(targets.DEFAULT_TARGETS_PATH),\n"
        "  'activation': str(_resolve_data_dir(None)),\n"
        "}))\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=Path(__file__).resolve().parents[2],
        capture_output=True,
        text=True,
        check=True,
    )
    import json

    payload = json.loads(result.stdout.strip())
    expected_db = str(root / "state.db")
    assert payload["config_state_db"] == expected_db
    assert payload["db_path"] == expected_db
    assert payload["targets"] == str(root / "targets.json")
    assert payload["activation"] == str(root.resolve())


def _is_init_db_call(node: ast.Call) -> bool:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id == "init_db"
    if isinstance(func, ast.Attribute):
        return func.attr == "init_db"
    return False


def _is_path_call(node: ast.AST) -> bool:
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    if isinstance(func, ast.Name):
        return func.id == "Path"
    if isinstance(func, ast.Attribute):
        return func.attr == "Path"
    return False


def _static_string_parts(node: ast.AST) -> tuple[str, ...] | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return (node.value,)
    if isinstance(node, ast.JoinedStr):
        return None
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _static_string_parts(node.left)
        right = _static_string_parts(node.right)
        if left is None or right is None:
            return None
        return left + right
    if _is_path_call(node):
        parts: list[str] = []
        for arg in node.args:
            static = _static_string_parts(arg)
            if static is None or len(static) != 1:
                return None
            parts.append(static[0])
        if parts:
            return tuple(parts)
        return None
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        left = _static_string_parts(node.left)
        right = _static_string_parts(node.right)
        if left is None or right is None:
            return None
        return left + right
    return None


def _canonical_legacy_path(parts: tuple[str, ...]) -> str | None:
    if not parts:
        return None
    if len(parts) == 1:
        path = Path(parts[0])
    else:
        path = Path(parts[0]).joinpath(*parts[1:])
    normalized_parts = [part for part in path.parts if part not in (".", "")]
    return "/".join(normalized_parts)


def _is_legacy_state_db_expr(node: ast.AST | None) -> bool:
    if node is None:
        return False
    parts = _static_string_parts(node)
    if parts is None:
        return False
    return _canonical_legacy_path(parts) == "data/state.db"


def _init_db_path_arg(call: ast.Call) -> ast.AST | None:
    if call.args:
        return call.args[0]
    for keyword in call.keywords:
        if keyword.arg in {"path", "db_path"}:
            return keyword.value
    return None


def _find_legacy_init_db_violations(source: str, label: str) -> list[str]:
    tree = ast.parse(source)
    violations: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not _is_init_db_call(node):
            continue
        path_arg = _init_db_path_arg(node)
        if _is_legacy_state_db_expr(path_arg):
            violations.append(f"{label}:{node.lineno}")
    return violations


def _production_python_surfaces(repo_root: Path) -> list[Path]:
    paths: list[Path] = []
    paths.extend(sorted(repo_root.glob("*.py")))
    for pattern in ("modules/**/*.py", "portal/**/*.py", "scripts/**/*.py"):
        paths.extend(sorted(repo_root.glob(pattern)))
    excluded = {"/tests/", "/__pycache__/", "/.venv/", "/dist/", "/build/"}
    unique: list[Path] = []
    seen: set[Path] = set()
    for path in paths:
        resolved = path.resolve()
        if resolved in seen:
            continue
        text = str(resolved)
        if any(part in text for part in excluded):
            continue
        seen.add(resolved)
        unique.append(path)
    return unique


def test_no_hardcoded_init_db_data_state_db_in_production():
    """Production surfaces must not pass static legacy data/state.db to init_db."""
    repo_root = Path(__file__).resolve().parents[2]
    violations: list[str] = []
    for path in _production_python_surfaces(repo_root):
        rel = path.relative_to(repo_root).as_posix()
        violations.extend(_find_legacy_init_db_violations(path.read_text(encoding="utf-8"), rel))
    assert violations == []


@pytest.mark.parametrize(
    ("snippet", "expect_violation"),
    [
        ('init_db("data/state.db")', True),
        ('init_db("data/" + "state.db")', True),
        ('init_db(Path("data") / "state.db")', True),
        ('init_db(Path("data", "state.db"))', True),
        ('init_db(pathlib.Path("data") / "state.db")', True),
        ('init_db("data//state.db")', True),
        ('init_db("./data/state.db")', True),
        ('db.init_db("data/state.db")', True),
        ('init_db(data_path("state.db"))', False),
        ("init_db(app_cfg.state_db_path)", False),
        ("init_db(tmp_db_path)", False),
        ("init_db(path=db_path)", False),
    ],
)
def test_ast_guard_positive_controls(snippet, expect_violation):
    source = f"import pathlib\nfrom pathlib import Path\n{snippet}\n"
    violations = _find_legacy_init_db_violations(source, "snippet.py")
    if expect_violation:
        assert len(violations) == 1
        assert violations[0].startswith("snippet.py:")
    else:
        assert violations == []


def test_legacy_default_db_path_is_repo_relative():
    assert legacy_default_db_path() == Path("data/state.db")


def test_blank_lighthouse_data_dir_translated_at_activation_boundary(monkeypatch):
    """Resolver failures must surface as ActivationRefused, not leak DataPathError."""
    from modules.profile_activation import ActivationRefused, _resolve_data_dir

    monkeypatch.setenv("LIGHTHOUSE_DATA_DIR", "   ")
    with pytest.raises(
        ActivationRefused, match="configured data directory is unusable"
    ) as exc_info:
        _resolve_data_dir(None)
    from modules.data_paths import DataPathError

    assert not isinstance(exc_info.value, DataPathError)


def test_default_path_resolution_oserror_translated(monkeypatch):
    """Default-path branch must translate OSError/RuntimeError like explicit paths."""
    from modules import profile_activation
    from modules.profile_activation import ActivationRefused

    monkeypatch.setenv("LIGHTHOUSE_DATA_DIR", "/data")

    def _boom(_path):
        raise OSError("resolution failed")

    monkeypatch.setattr(profile_activation, "resolve_path", _boom)
    refused = ActivationRefused
    with pytest.raises(refused, match="configured data directory is unusable") as exc_info:
        profile_activation._resolve_data_dir(None)
    assert isinstance(exc_info.value.__cause__, OSError)
