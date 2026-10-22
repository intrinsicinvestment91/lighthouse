"""Backup-boundary model and read-only backup-readiness inspection.

Lighthouse's authoritative writable state must live beneath the Start9-protected persistent
volume `/data`, which is what StartOS backs up. Before any future profile switch (which is a
controlled, backup-gated state transition) the system must be able to *prove*, read-only, that
every authoritative state item resolves under that boundary and can be inventoried.

This module is purely inspective: it canonicalizes paths, classifies state, and reports a
fail-closed readiness verdict. It performs NO mutation, NO DB write, and NEVER triggers a
backup. Crucially, a `READY` verdict asserts only *boundary eligibility* — it does NOT assert
that a backup has actually been performed.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

# The Start9 persistent-volume boundary. Authoritative writable state must resolve under this.
DATA_BOUNDARY = "/data"

# State classifications ------------------------------------------------------------------
CATEGORY_AUTHORITATIVE = "authoritative_persistent"
CATEGORY_RECONSTRUCTABLE = "reconstructable_persistent"
CATEGORY_RUNTIME_EPHEMERAL = "runtime_ephemeral"
CATEGORY_BUNDLED_IMMUTABLE = "bundled_immutable"
CATEGORY_EXTERNAL_UNMANAGED = "external_unmanaged"

# Canonical classification of every Lighthouse state item. `authoritative_persistent` items
# gate readiness; `reconstructable_persistent` items are re-fetchable and do not block; the
# rest are informational.
STATE_CATEGORIES: dict[str, str] = {
    # Authoritative — losing these loses irreplaceable operator/derived state.
    "state_db": CATEGORY_AUTHORITATIVE,
    "entities_json": CATEGORY_AUTHORITATIVE,
    "targets_json": CATEGORY_AUTHORITATIVE,
    "archives_dir": CATEGORY_AUTHORITATIVE,
    "profiles_dir": CATEGORY_AUTHORITATIVE,  # future: operator overrides + activation records
    # Reconstructable — module JSON dumps, re-derivable by re-running collectors.
    "patents_dir": CATEGORY_RECONSTRUCTABLE,
    "arxiv_dir": CATEGORY_RECONSTRUCTABLE,
    "flights_dir": CATEGORY_RECONSTRUCTABLE,
    "narrative_dir": CATEGORY_RECONSTRUCTABLE,
    "sec_dir": CATEGORY_RECONSTRUCTABLE,
    "adsb_dir": CATEGORY_RECONSTRUCTABLE,
    "velocity_dir": CATEGORY_RECONSTRUCTABLE,
    "correlations_dir": CATEGORY_RECONSTRUCTABLE,
    "nostr_dir": CATEGORY_RECONSTRUCTABLE,
    # Ephemeral — safe to lose; regenerated at runtime.
    "logs_dir": CATEGORY_RUNTIME_EPHEMERAL,
    "pycache": CATEGORY_RUNTIME_EPHEMERAL,
    # Bundled — shipped in the image, re-supplied by reinstall; not operator state.
    "seed_dir": CATEGORY_BUNDLED_IMMUTABLE,
    "app_code": CATEGORY_BUNDLED_IMMUTABLE,
    "playwright_chromium": CATEGORY_BUNDLED_IMMUTABLE,
    # External — an authoritative path deliberately configured outside the backup boundary.
    "db_path_override": CATEGORY_EXTERNAL_UNMANAGED,
}

# Required authoritative items (relative to the data dir) that must exist + sit under the
# boundary for a fully READY verdict. `profiles_dir` is intentionally omitted here: it is a
# future capability and its absence is informational, not blocking.
_REQUIRED_AUTHORITATIVE = (
    ("state_db", "state.db"),
    ("entities_json", "entities.json"),
    ("targets_json", "targets.json"),
    ("archives_dir", "archives"),
)

# Reconstructable module output dirs (informational boundary checks only).
_RECONSTRUCTABLE_DIRS = (
    ("patents_dir", "patents"),
    ("arxiv_dir", "arxiv"),
    ("flights_dir", "flights"),
    ("narrative_dir", "narrative"),
    ("sec_dir", "sec"),
    ("adsb_dir", "adsb"),
    ("velocity_dir", "velocity"),
    ("correlations_dir", "correlations"),
    ("nostr_dir", "nostr"),
)

# Known contract schema versions — used to answer `schema_versions_known`.
_KNOWN_SCHEMA_CONTRACTS = (
    "alerts",
    "correlations",
    "graph",
    "callsign_observations",
    "targets",
    "intelligence_profile",
)


def resolve_path(p: str | Path) -> Path:
    """Canonicalize a path: normalize `..`, follow symlinks. Works on non-existent paths."""
    return Path(p).resolve()


def is_within_boundary(path: str | Path, boundary: str | Path = DATA_BOUNDARY) -> bool:
    """True iff `path` canonically resolves to `boundary` itself or a descendant of it.

    Uses resolved paths (not string prefixes), so `..` traversal and symlink escape are
    rejected because both are collapsed by `resolve()` first.
    """
    try:
        resolved = resolve_path(path)
        canonical_boundary = resolve_path(boundary)
    except (OSError, RuntimeError, ValueError):
        return False
    return resolved == canonical_boundary or canonical_boundary in resolved.parents


def _severity_check(check_id: str, category: str, configured: Path, boundary: Path) -> dict:
    """Build one boundary check record for an authoritative/reconstructable path."""
    ambiguous = not configured.is_absolute()
    try:
        resolved = resolve_path(configured)
        canonicalized = True
    except (OSError, RuntimeError, ValueError):
        resolved = configured
        canonicalized = False

    if not canonicalized:
        result, reason = "uncanonicalizable", "path could not be safely canonicalized"
    elif ambiguous:
        result, reason = "ambiguous", "configured path is relative; boundary is indeterminate"
    elif is_within_boundary(resolved, boundary):
        result, reason = "within_boundary", "resolves under the backup boundary"
    else:
        result, reason = "outside_boundary", "resolves OUTSIDE the backup boundary"

    authoritative = category == CATEGORY_AUTHORITATIVE
    within = result == "within_boundary"
    blocking = authoritative and not within
    if blocking:
        severity = "blocking"
    elif not within:
        severity = "warning"
    else:
        severity = "info"
    return {
        "check_id": check_id,
        "state_category": category,
        "configured_path": str(configured),
        "resolved_path": str(resolved),
        "boundary": str(boundary),
        "result": result,
        "reason": reason,
        "severity": severity,
        "exists": resolved.exists() if canonicalized else False,
    }


def inspect_backup_readiness(
    boundary: str | Path = DATA_BOUNDARY,
    data_dir: str | Path | None = None,
    db_path: str | Path | None = None,
) -> dict[str, Any]:
    """Read-only backup-readiness verdict for the current configuration.

    Resolves authoritative/reconstructable state paths (from `config` and `modules.db` unless
    overridden) and classifies each against `boundary`. Performs no mutation and triggers no
    backup. `READY` means boundary-eligible for backup — NOT that a backup exists.

    Legacy ``data/state.db`` divergence compares the repo-relative default path resolved against
    the process current working directory (via ``resolve_path()``) to the derived database path.
    """
    from modules import db as _db
    from modules.data_paths import legacy_default_db_path, resolve_data_dir

    db_path_explicitly_supplied = db_path is not None
    if data_dir is None:
        data_dir = resolve_data_dir()
    data_dir = Path(data_dir)
    if db_path is None:
        db_path = Path(_db.DB_PATH)
    db_path = Path(db_path)
    boundary_path = Path(boundary)

    checks: list[dict] = []
    blocking_reasons: list[str] = []
    warnings: list[str] = []

    boundary_valid = boundary_path.is_absolute()
    if not boundary_valid:
        blocking_reasons.append(f"backup boundary is not an absolute path: {boundary_path}")

    # Authoritative required items --------------------------------------------------------
    authoritative_complete = True
    for check_id, rel in _REQUIRED_AUTHORITATIVE:
        configured = db_path if check_id == "state_db" else data_dir / rel
        check = _severity_check(check_id, CATEGORY_AUTHORITATIVE, configured, boundary_path)
        checks.append(check)
        if check["severity"] == "blocking":
            authoritative_complete = False
            blocking_reasons.append(f"{check_id}: {check['reason']} ({check['resolved_path']})")
        elif not check["exists"]:
            # Within boundary but not yet created: pre-runtime, inventory incomplete.
            authoritative_complete = False
            warnings.append(
                f"{check_id}: authoritative state not yet created at {check['resolved_path']}"
            )

    # If the DB path is configured outside the boundary, record the external-unmanaged escape.
    if not is_within_boundary(db_path, boundary_path) and db_path.is_absolute():
        checks.append(
            {
                "check_id": "db_path_override",
                "state_category": CATEGORY_EXTERNAL_UNMANAGED,
                "configured_path": str(db_path),
                "resolved_path": str(resolve_path(db_path)),
                "boundary": str(boundary_path),
                "result": "outside_boundary",
                "reason": "DB_PATH escapes the backup boundary; state would not be backed up",
                "severity": "blocking",
                "exists": resolve_path(db_path).exists(),
            }
        )

    # Orphaned legacy repo-relative database when LIGHTHOUSE_DATA_DIR relocated state.
    if not db_path_explicitly_supplied:
        try:
            legacy_db = resolve_path(legacy_default_db_path())
            configured_db = resolve_path(db_path)
        except (OSError, RuntimeError, ValueError):
            legacy_db = None
            configured_db = None
        if (
            legacy_db is not None
            and configured_db is not None
            and legacy_db != configured_db
            and legacy_db.is_file()
            and legacy_db.stat().st_size > 0
        ):
            checks.append(
                {
                    "check_id": "data_path_divergence",
                    "state_category": CATEGORY_EXTERNAL_UNMANAGED,
                    "configured_path": str(legacy_db),
                    "resolved_path": str(legacy_db),
                    "boundary": str(boundary_path),
                    "result": "outside_boundary",
                    "reason": (
                        "legacy repo-relative state.db exists and differs from the configured "
                        "database path "
                        f"({configured_db}); alert/graph history may be split across two files"
                    ),
                    "severity": "blocking",
                    "exists": True,
                }
            )
            blocking_reasons.append(
                "data_path_divergence: legacy repo-relative state.db differs from configured "
                f"DB_PATH (legacy={legacy_db}, configured={configured_db})"
            )

    # Reconstructable items (informational) ----------------------------------------------
    for check_id, rel in _RECONSTRUCTABLE_DIRS:
        check = _severity_check(check_id, CATEGORY_RECONSTRUCTABLE, data_dir / rel, boundary_path)
        checks.append(check)
        if check["result"] == "outside_boundary":
            warnings.append(f"{check_id}: reconstructable state outside boundary (non-blocking)")

    # Future profiles/ override root (informational until the capability exists) -----------
    profiles_check = _severity_check(
        "profiles_dir", CATEGORY_AUTHORITATIVE, data_dir / "profiles", boundary_path
    )
    # Absence is expected in Phase 1 — downgrade to informational rather than blocking.
    if not profiles_check["exists"] and profiles_check["result"] == "within_boundary":
        profiles_check["severity"] = "info"
        profiles_check["reason"] = "future operator profile root (not yet created)"
    checks.append(profiles_check)

    schema_versions_known = True  # all contract schema versions are enumerable in-process

    restore_procedure_documented = (
        Path(__file__).resolve().parent.parent
        / "docs"
        / "BACKUP_READINESS_AND_PROFILE_SWITCHING.md"
    ).exists()
    if not restore_procedure_documented:
        warnings.append("restore procedure documentation not found")

    boundary_valid = boundary_valid and not any(
        c["severity"] == "blocking" and c["result"] == "outside_boundary" for c in checks
    )

    # Verdict ----------------------------------------------------------------------------
    has_blocking = any(c["severity"] == "blocking" for c in checks) or bool(blocking_reasons)
    if has_blocking or not boundary_path.is_absolute() or not schema_versions_known:
        status = "NOT_READY"
    elif not authoritative_complete:
        # Boundary-valid but authoritative state not yet created: pre-runtime, explicit.
        status = "UNKNOWN"
    else:
        status = "READY"

    return {
        "status": status,
        "boundary": str(boundary_path),
        "checks": checks,
        "authoritative_state_complete": authoritative_complete,
        "backup_boundary_valid": boundary_valid,
        "restore_procedure_documented": restore_procedure_documented,
        "schema_versions_known": schema_versions_known,
        "known_schema_contracts": list(_KNOWN_SCHEMA_CONTRACTS),
        "blocking_reasons": blocking_reasons,
        "warnings": warnings,
        "note": (
            "READY means authoritative state is boundary-eligible for backup; it does NOT "
            "assert that a backup has been performed. Backup execution is a separate step."
        ),
        "backup_performed": False,
    }
