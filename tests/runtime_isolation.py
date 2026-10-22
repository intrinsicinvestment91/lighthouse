"""Pytest runtime-data isolation boundary (LHP-TEST-ISOLATION-01A).

Establishes a session-owned runtime root outside the repository before application
modules bind default ``data/`` paths. Stdlib only — must not import ``config`` or
``modules.*`` path/database modules.
"""

from __future__ import annotations

import ast
import atexit
import hashlib
import os
import shutil
import sys
import tempfile
from collections.abc import Mapping, MutableMapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT_DATA_DIR = "data"
STATE_DB_NAME = "state.db"

GUARDED_MODULES: tuple[str, ...] = (
    "config",
    "modules.data_paths",
    "modules.db",
    "modules.targets",
)

PROTECTED_REPO_RELATIVE_PATHS: tuple[str, ...] = (
    "data/state.db",
    "data/archives/test.html",
)

_WRITE_DEST_METHODS: frozenset[str] = frozenset(
    {
        "write_text",
        "write_bytes",
        "mkdir",
        "touch",
    }
)

_WRITE_CALL_NAMES: frozenset[str] = frozenset(
    {
        "open",
        "connect",
    }
)


class RuntimeIsolationError(RuntimeError):
    """Fail-closed refusal before pytest collection."""


@dataclass(frozen=True)
class ProtectedFileSnapshot:
    path: Path
    existed: bool
    sha256: str | None


@dataclass(frozen=True)
class IsolationResult:
    session_root: Path | None
    data_dir: Path
    db_path: Path
    operator_owned: bool
    protected_snapshots: tuple[ProtectedFileSnapshot, ...]


_SESSION_RESULT: IsolationResult | None = None
_SESSION_ROOT_TO_CLEAN: Path | None = None
_CLEANUP_REGISTERED = False


def repo_root() -> Path:
    """Repository root containing ``tests/runtime_isolation.py``."""
    return Path(__file__).resolve().parents[1]


def get_session_result() -> IsolationResult | None:
    return _SESSION_RESULT


def _normalize_configured_path(raw: str) -> str:
    stripped = raw.strip()
    if stripped and all(ch == "/" for ch in stripped):
        return "/"
    return stripped.rstrip("/")


def _resolve_data_dir_from_environ(environ: Mapping[str, str]) -> Path:
    raw = environ.get("LIGHTHOUSE_DATA_DIR")
    if raw is None:
        return Path(DEFAULT_DATA_DIR)
    if not raw.strip():
        raise RuntimeIsolationError("LIGHTHOUSE_DATA_DIR must not be empty or whitespace-only")
    return Path(_normalize_configured_path(raw))


def _resolve_db_path_from_environ(environ: Mapping[str, str]) -> Path:
    raw = environ.get("DB_PATH")
    if raw is not None:
        if not raw.strip():
            raise RuntimeIsolationError("DB_PATH must not be empty or whitespace-only")
        return Path(_normalize_configured_path(raw))
    return _resolve_data_dir_from_environ(environ) / STATE_DB_NAME


def path_inside_repo(path: Path, root: Path) -> bool:
    """Return True when ``path`` resolves to ``root`` or a descendant."""
    try:
        resolved = path.expanduser().resolve()
        repo_resolved = root.resolve()
    except OSError:
        return False
    if resolved == repo_resolved:
        return True
    try:
        resolved.relative_to(repo_resolved)
        return True
    except ValueError:
        return False


def _validate_non_blank_env(environ: Mapping[str, str]) -> None:
    for key in ("LIGHTHOUSE_DATA_DIR", "DB_PATH"):
        if key in environ and not str(environ[key]).strip():
            raise RuntimeIsolationError(f"{key} must not be empty or whitespace-only")


def _bound_paths_outside_repo(
    module_name: str,
    repo: Path,
    environ: Mapping[str, str],
) -> bool:
    mod = sys.modules.get(module_name)
    if mod is None:
        return True
    if module_name == "modules.db":
        db_path = Path(getattr(mod, "DB_PATH", DEFAULT_DATA_DIR + "/" + STATE_DB_NAME))
        return not path_inside_repo(db_path, repo)
    if module_name == "modules.targets":
        targets_path = getattr(mod, "DEFAULT_TARGETS_PATH", None)
        if targets_path is None:
            return True
        return not path_inside_repo(Path(targets_path), repo)
    if module_name == "config":
        sample_paths: list[Path] = []
        for attr in ("patent_cfg", "app_cfg", "archive_cfg", "flight_cfg", "adsb_cfg"):
            cfg = getattr(mod, attr, None)
            if cfg is None:
                continue
            for field in ("output_dir", "state_db_path", "db_path", "entities_path"):
                value = getattr(cfg, field, None)
                if value:
                    sample_paths.append(Path(value))
        if sample_paths:
            return all(not path_inside_repo(path, repo) for path in sample_paths)
        data_dir = _resolve_data_dir_from_environ(environ)
        db_path = _resolve_db_path_from_environ(environ)
        return not path_inside_repo(data_dir, repo) and not path_inside_repo(db_path, repo)
    if module_name == "modules.data_paths":
        data_dir = _resolve_data_dir_from_environ(environ)
        db_path = _resolve_db_path_from_environ(environ)
        return not path_inside_repo(data_dir, repo) and not path_inside_repo(db_path, repo)
    return True


def _check_guarded_modules_already_loaded(
    imported_modules: set[str],
    repo: Path,
    environ: Mapping[str, str],
) -> None:
    for name in GUARDED_MODULES:
        if name not in imported_modules:
            continue
        if _bound_paths_outside_repo(name, repo, environ):
            continue
        raise RuntimeIsolationError(
            f"{name} was imported before runtime isolation with repo-relative runtime paths"
        )


def snapshot_protected_files(repo: Path) -> tuple[ProtectedFileSnapshot, ...]:
    snapshots: list[ProtectedFileSnapshot] = []
    for rel in PROTECTED_REPO_RELATIVE_PATHS:
        path = repo / rel
        existed = path.is_file()
        digest = hashlib.sha256(path.read_bytes()).hexdigest() if existed else None
        snapshots.append(ProtectedFileSnapshot(path=path, existed=existed, sha256=digest))
    return tuple(snapshots)


def protected_files_unchanged(
    repo: Path,
    baseline: tuple[ProtectedFileSnapshot, ...] | None = None,
) -> bool:
    if baseline is None:
        session = get_session_result()
        baseline = session.protected_snapshots if session else snapshot_protected_files(repo)
    current = snapshot_protected_files(repo)
    return baseline == current


def _cleanup_session_root() -> None:
    global _SESSION_ROOT_TO_CLEAN
    if _SESSION_ROOT_TO_CLEAN is None:
        return
    root = _SESSION_ROOT_TO_CLEAN
    _SESSION_ROOT_TO_CLEAN = None
    if root.exists():
        shutil.rmtree(root, ignore_errors=True)


def _register_cleanup_once() -> None:
    global _CLEANUP_REGISTERED
    if _CLEANUP_REGISTERED:
        return
    atexit.register(_cleanup_session_root)
    _CLEANUP_REGISTERED = True


def apply_runtime_isolation(
    environ: MutableMapping[str, str] | None = None,
    *,
    repo_root_path: Path | None = None,
    imported_modules: set[str] | None = None,
) -> IsolationResult:
    """Establish or validate the pytest runtime path boundary."""
    global _SESSION_RESULT, _SESSION_ROOT_TO_CLEAN

    if environ is None:
        environ = os.environ
    live_session = environ is os.environ
    repo = repo_root_path if repo_root_path is not None else repo_root()
    modules = imported_modules if imported_modules is not None else set(sys.modules.keys())

    _validate_non_blank_env(environ)

    data_raw = environ.get("LIGHTHOUSE_DATA_DIR")
    db_raw = environ.get("DB_PATH")
    both_absent = data_raw is None and db_raw is None

    if live_session and _SESSION_RESULT is not None:
        current_data = _resolve_data_dir_from_environ(environ)
        current_db = _resolve_db_path_from_environ(environ)
        if not path_inside_repo(current_data, repo) and not path_inside_repo(current_db, repo):
            return _SESSION_RESULT

    _check_guarded_modules_already_loaded(modules, repo, environ)

    session_root: Path | None = None
    operator_owned = not both_absent

    if both_absent:
        if live_session and _SESSION_ROOT_TO_CLEAN is not None and _SESSION_ROOT_TO_CLEAN.exists():
            shutil.rmtree(_SESSION_ROOT_TO_CLEAN, ignore_errors=True)
        session_root = Path(tempfile.mkdtemp(prefix="lhp-pytest-runtime-"))
        data_dir = session_root / "data"
        db_path = data_dir / STATE_DB_NAME
        environ["LIGHTHOUSE_DATA_DIR"] = str(data_dir)
        environ["DB_PATH"] = str(db_path)
        if live_session:
            _SESSION_ROOT_TO_CLEAN = session_root
            _register_cleanup_once()
        operator_owned = False
    else:
        data_dir = _resolve_data_dir_from_environ(environ)
        db_path = _resolve_db_path_from_environ(environ)

    if path_inside_repo(data_dir, repo):
        raise RuntimeIsolationError(
            f"LIGHTHOUSE_DATA_DIR resolves inside the repository: {data_dir!s}"
        )
    if path_inside_repo(db_path, repo):
        raise RuntimeIsolationError(f"DB_PATH resolves inside the repository: {db_path!s}")

    # Bytecode contract (precise; no claim over arbitrary replacement envs):
    # - Plugin-import bytecode may already have been written before this runs
    #   (tests/__pycache__/{__init__,runtime_isolation}*.pyc). Those are
    #   plugin-import artifacts, not application runtime data.
    # - sys.dont_write_bytecode = True suppresses later imports in *this*
    #   pytest process only.
    # - Children that inherit os.environ inherit PYTHONDONTWRITEBYTECODE=1.
    # - A child launched with a replacement env= mapping must set the flag
    #   itself; this plugin never monkeypatches subprocess.Popen.
    sys.dont_write_bytecode = True
    environ["PYTHONDONTWRITEBYTECODE"] = "1"

    snapshots = snapshot_protected_files(repo)
    result = IsolationResult(
        session_root=session_root,
        data_dir=data_dir,
        db_path=db_path,
        operator_owned=operator_owned,
        protected_snapshots=snapshots,
    )
    if live_session:
        _SESSION_RESULT = result
    return result


def ensure_runtime_isolation() -> IsolationResult:
    """Apply isolation or terminate pytest before collection."""
    try:
        return apply_runtime_isolation()
    except RuntimeIsolationError as exc:
        raise SystemExit(f"runtime isolation refused: {exc}") from exc


def _static_string_parts(node: ast.AST | None) -> tuple[str, ...] | None:
    if node is None:
        return None
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return (node.value,)
    if isinstance(node, ast.JoinedStr):
        parts: list[str] = []
        for value in node.values:
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                parts.append(value.value)
            else:
                return None
        return tuple(parts)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        left = _static_string_parts(node.left)
        right = _static_string_parts(node.right)
        if left is None or right is None:
            return None
        return left + right
    return None


def _canonical_data_path(parts: tuple[str, ...]) -> str | None:
    if not parts:
        return None
    if len(parts) == 1:
        path = Path(parts[0])
    else:
        path = Path(parts[0]).joinpath(*parts[1:])
    normalized = [part for part in path.parts if part not in (".", "")]
    if not normalized:
        return None
    return "/".join(normalized)


def _is_repo_data_path(parts: tuple[str, ...]) -> bool:
    canonical = _canonical_data_path(parts)
    if canonical is None:
        return False
    return canonical == "data" or canonical.startswith("data/")


def _find_static_data_write_violations(source: str, label: str) -> list[str]:
    tree = ast.parse(source)
    violations: list[str] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Attribute) and func.attr in _WRITE_DEST_METHODS:
                parts = _static_string_parts(func.value)
                if parts and _is_repo_data_path(parts):
                    violations.append(f"{label}:{node.lineno}: static Path write target {parts!r}")
                arg_parts = _static_string_parts(node.args[0]) if node.args else None
                if arg_parts and _is_repo_data_path(arg_parts):
                    violations.append(
                        f"{label}:{node.lineno}: static write via .{func.attr}() {arg_parts!r}"
                    )
            if isinstance(func, ast.Name) and func.id in _WRITE_CALL_NAMES:
                arg_parts = _static_string_parts(node.args[0]) if node.args else None
                if arg_parts and _is_repo_data_path(arg_parts):
                    violations.append(
                        f"{label}:{node.lineno}: static {func.id}() target {arg_parts!r}"
                    )
            if isinstance(func, ast.Attribute) and func.attr == "connect":
                arg_parts = _static_string_parts(node.args[0]) if node.args else None
                if arg_parts and _is_repo_data_path(arg_parts):
                    violations.append(
                        f"{label}:{node.lineno}: static connect() target {arg_parts!r}"
                    )
        if isinstance(node, ast.Call) and _is_init_db_call(node):
            path_arg = _init_db_path_arg(node)
            parts = _static_string_parts(path_arg)
            if parts and _is_repo_data_path(parts):
                violations.append(f"{label}:{node.lineno}: init_db() legacy target {parts!r}")

    return violations


def _is_init_db_call(node: ast.Call) -> bool:
    func = node.func
    if isinstance(func, ast.Name) and func.id == "init_db":
        return True
    return (
        isinstance(func, ast.Attribute)
        and func.attr == "init_db"
        and isinstance(func.value, ast.Name)
    )


def _init_db_path_arg(call: ast.Call) -> ast.AST | None:
    if call.args:
        return call.args[0]
    for keyword in call.keywords:
        if keyword.arg in {"path", "db_path"}:
            return keyword.value
    return None


_SCAN_SKIP_DIR_NAMES: frozenset[str] = frozenset(
    {
        ".git",
        "__pycache__",
        ".pytest_cache",
        ".ruff_cache",
        ".mypy_cache",
        ".tox",
        ".nox",
        ".venv",
        "venv",
        "env",
        "build",
        "dist",
        "htmlcov",
        "node_modules",
        ".eggs",
    }
)
_SCAN_RECURSIVE_ROOTS: tuple[str, ...] = ("modules", "portal", "scripts", "tests")


def _python_files_for_static_scan(repo: Path) -> list[Path]:
    """Root ``*.py`` plus recursive ``modules|portal|scripts|tests/**/*.py``."""
    seen: set[Path] = set()
    files: list[Path] = []

    def _consider(path: Path) -> None:
        if path.name == "runtime_isolation.py":
            return
        if any(part in _SCAN_SKIP_DIR_NAMES for part in path.parts):
            return
        resolved = path.resolve()
        if resolved in seen:
            return
        seen.add(resolved)
        files.append(path)

    for path in repo.glob("*.py"):
        _consider(path)
    for rel in _SCAN_RECURSIVE_ROOTS:
        root = repo / rel
        if not root.exists():
            continue
        for path in root.rglob("*.py"):
            _consider(path)
    return files


def scan_static_repo_data_writes(repo: Path) -> list[str]:
    """AST guard for static literal ``data/...`` write destinations.

    Scans repository-root ``*.py`` plus recursive ``modules/**/*.py``,
    ``portal/**/*.py``, ``scripts/**/*.py``, and ``tests/**/*.py``. Skips
    ``.git``, bytecode/tool caches, virtualenvs, and ``build``/``dist``.
    Skips this plugin file (it names protected paths as data, not as writes).

    A call is a violation only when a write-like form (``open``/``connect``/
    ``init_db``/``Path.write_*``/``mkdir``/``touch``) has a compile-time
    string destination whose normalized form is ``data`` or ``data/...``.
    Temp-rooted destinations (``tmp_path``, ``/tmp/...``, attributes,
    runtime f-strings) are invisible to this guard — they are not treated
    as safe because they live under ``tests/``; they are not static
    ``data/...`` literals. Tests are not exempt: a literal checkout-relative
    ``data/...`` write in ``tests/`` is a violation.

    Limitations: cannot see ``Path(cfg.output_dir)``, runtime-built f-strings,
    or ``db.DB_PATH`` after import-time binding. Dataclass metadata such as
    ``local_path="data/archives/test.html"`` is not a write.
    """
    violations: list[str] = []
    for path in _python_files_for_static_scan(repo):
        rel = path.relative_to(repo).as_posix()
        violations.extend(
            _find_static_data_write_violations(
                path.read_text(encoding="utf-8"),
                rel,
            )
        )
    return sorted(violations)


def _function_sets_db_path(function: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    for node in ast.walk(function):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (
            isinstance(func, ast.Attribute)
            and func.attr == "setattr"
            and isinstance(func.value, ast.Name)
            and func.value.id in {"monkeypatch", "m"}
        ):
            continue
        if len(node.args) < 3:
            continue
        target = node.args[1]
        value = node.args[2]
        if (
            isinstance(target, ast.Constant)
            and target.value == "DB_PATH"
            and isinstance(value, ast.Name)
            and value.id == "tmp_db_path"
        ):
            return True
    return False


def _calls_runner_with_dry_run_false(
    function: ast.FunctionDef | ast.AsyncFunctionDef,
    runner_name: str,
) -> bool:
    for node in ast.walk(function):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Name) and func.id == runner_name):
            continue
        for keyword in node.keywords:
            if (
                keyword.arg == "dry_run"
                and isinstance(keyword.value, ast.Constant)
                and keyword.value.value is False
            ):
                return True
    return False


def find_callsign_dual_bind_violations(repo: Path) -> list[str]:
    """Require db.DB_PATH rebinding for non-dry ADS-B/flight runner tests."""
    violations: list[str] = []
    checks = (
        ("tests/unit/test_adsb_sentinel.py", "run_adsb_sentinel"),
        ("tests/unit/test_flight_tracker.py", "run_flight_tracker"),
    )
    for rel, runner in checks:
        path = repo / rel
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if not node.name.startswith("test_"):
                continue
            if not _calls_runner_with_dry_run_false(node, runner):
                continue
            if not _function_sets_db_path(node):
                violations.append(f"{rel}:{node.lineno}: {node.name} missing db.DB_PATH bind")
    return violations


def pytest_load_initial_conftests(early_config: Any, parser: Any, args: Any) -> None:
    ensure_runtime_isolation()


def pytest_configure(config: Any) -> None:
    ensure_runtime_isolation()


def pytest_unconfigure(config: Any) -> None:
    _cleanup_session_root()


ensure_runtime_isolation()
