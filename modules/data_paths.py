"""Canonical data-directory path resolution (LHP-V1-HARDEN-03B/03D).

``LIGHTHOUSE_DATA_DIR`` is the data directory itself — never a parent under which ``data/`` is
appended. ``DB_PATH`` is an explicit full-database-file override; when unset it derives from
``LIGHTHOUSE_DATA_DIR`` as ``<data dir>/state.db``.

Startup contract: environment values are read when resolver functions run; module-level constants
(such as ``modules.db.DB_PATH``) may resolve at import time. Changing environment variables after
imports is unsupported and does not promise hot reconfiguration.

Pure: stdlib only, no config singleton import, no DB, no filesystem writes.
"""

from __future__ import annotations

import os
from pathlib import Path

_DEFAULT_DATA_DIR = "data"
_STATE_DB_NAME = "state.db"
_LEGACY_DEFAULT_DB = Path("data") / _STATE_DB_NAME


class DataPathError(ValueError):
    """Raised when a configured ``LIGHTHOUSE_DATA_DIR`` or ``DB_PATH`` value is invalid.

    Missing variables use defaults; only empty or whitespace-only configured values fail.
    """


def _normalize_configured_path(raw: str) -> str:
    stripped = raw.strip()
    if stripped and all(ch == "/" for ch in stripped):
        return "/"
    return stripped.rstrip("/")


def _validate_relative_child(rel: str) -> None:
    rel_path = Path(rel)
    if rel_path.is_absolute():
        raise DataPathError(f"data path child must be relative, not absolute: {rel!r}")
    if ".." in rel_path.parts:
        raise DataPathError(f"data path child must not escape the data directory: {rel!r}")


def resolve_data_dir() -> Path:
    """Return the configured data directory (default: relative ``data``)."""
    raw = os.environ.get("LIGHTHOUSE_DATA_DIR")
    if raw is None:
        return Path(_DEFAULT_DATA_DIR)
    if not raw.strip():
        raise DataPathError("LIGHTHOUSE_DATA_DIR must not be empty or whitespace-only")
    return Path(_normalize_configured_path(raw))


def data_path(rel: str) -> str:
    """Resolve ``rel`` under ``LIGHTHOUSE_DATA_DIR``."""
    _validate_relative_child(rel)
    return str(resolve_data_dir() / rel)


def resolve_db_path() -> Path:
    """Return the SQLite database path — explicit DB_PATH or derived from LIGHTHOUSE_DATA_DIR."""
    raw = os.environ.get("DB_PATH")
    if raw is not None:
        if not raw.strip():
            raise DataPathError("DB_PATH must not be empty or whitespace-only")
        return Path(_normalize_configured_path(raw))
    return resolve_data_dir() / _STATE_DB_NAME


def legacy_default_db_path() -> Path:
    """Repo-relative default database location before LIGHTHOUSE_DATA_DIR relocation."""
    return _LEGACY_DEFAULT_DB
