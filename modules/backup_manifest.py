"""Read-only backup-manifest contract for Lighthouse authoritative state (LHP-PROFILE-02).

A *manifest* is a deterministic, versioned inventory of the authoritative persistent state a
backup would cover. It records identity, classification, path, type, size, and integrity for
every included item — but embeds NO file contents, database rows, or secrets. Generating a
manifest is read-only toward the source: it hashes files, snapshots SQLite databases into a
throwaway temp copy for a consistent digest + integrity check, and never mutates source state.

Fail-closed is the rule. Any authoritative path that escapes the `/data` boundary, any symlink
resolving outside the source, any broken authoritative symlink, any special file (socket/FIFO/
device), any un-snapshottable database, or any incomplete authoritative inventory raises
`ManifestError` rather than producing an ambiguous manifest.

The authoritative representation of a manifest is this module's `BackupManifest` dataclass +
`validate()`. `docs/schemas/lighthouse_backup_manifest.v1.schema.json` is a human/Gateway-facing
reference mirror, not the enforcer.
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
import stat
import tempfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from modules.backup_readiness import (
    CATEGORY_AUTHORITATIVE,
    CATEGORY_EXTERNAL_UNMANAGED,
    CATEGORY_RECONSTRUCTABLE,
    DATA_BOUNDARY,
    STATE_CATEGORIES,
    is_within_boundary,
)

BACKUP_MANIFEST_SCHEMA_VERSION = 1
GENERATOR_VERSION = "1.0.0"
DIGEST_ALGORITHM = "sha256"

ENTRY_TYPE_FILE = "file"
ENTRY_TYPE_DIRECTORY = "directory"
ENTRY_TYPE_SQLITE = "sqlite_database"
SUPPORTED_ENTRY_TYPES = frozenset({ENTRY_TYPE_FILE, ENTRY_TYPE_DIRECTORY, ENTRY_TYPE_SQLITE})

# Required authoritative items (logical_name, relative_path-under-data-dir, entry_type). These
# must all exist, be within boundary, and be snapshottable for a manifest to be produced.
_REQUIRED_AUTHORITATIVE: tuple[tuple[str, str, str], ...] = (
    ("state_db", "state.db", ENTRY_TYPE_SQLITE),
    ("entities_json", "entities.json", ENTRY_TYPE_FILE),
    ("targets_json", "targets.json", ENTRY_TYPE_FILE),
    ("archives_dir", "archives", ENTRY_TYPE_DIRECTORY),
)

# Optional reconstructable module output dirs (only included when explicitly requested).
_RECONSTRUCTABLE_DIRS: tuple[tuple[str, str], ...] = (
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

_CHUNK = 1 << 20  # 1 MiB streaming read for digests


class BackupError(Exception):
    """Base class for every fail-closed backup/verify/restore error (single catch point)."""


class ManifestError(BackupError):
    """Raised on any fail-closed condition during manifest generation or validation."""


# -- digest helpers ----------------------------------------------------------------------


def sha256_file(path: str | Path) -> str:
    """Stream a file's bytes through SHA-256. Raises ManifestError if unreadable."""
    h = hashlib.sha256()
    try:
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(_CHUNK), b""):
                h.update(chunk)
    except OSError as exc:
        raise ManifestError(f"could not read file for digest: {path} ({exc})") from exc
    return h.hexdigest()


def _is_special(path: Path) -> bool:
    """True for sockets, FIFOs, and block/char devices (never ordinary backup files)."""
    try:
        mode = os.lstat(path).st_mode
    except OSError:
        return False
    return stat.S_ISSOCK(mode) or stat.S_ISFIFO(mode) or stat.S_ISBLK(mode) or stat.S_ISCHR(mode)


def _guard_member(path: Path, boundary: Path) -> None:
    """Fail closed on hazards for a single filesystem member of authoritative state."""
    if path.is_symlink():
        target = os.path.realpath(path)
        if not os.path.exists(target):
            raise ManifestError(f"broken symlink in authoritative state: {path}")
        if not is_within_boundary(target, boundary):
            raise ManifestError(f"symlink escapes source boundary: {path} -> {target}")
    if _is_special(path):
        raise ManifestError(f"unsupported special file in authoritative state: {path}")


def _iter_directory_files(root: Path, boundary: Path) -> list[Path]:
    """Deterministic (sorted) list of regular files under `root`, fail-closed on hazards."""
    root = Path(root)
    files: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        base = Path(dirpath)
        # Guard directory entries themselves (a symlinked subdir could escape).
        _guard_member(base, boundary)
        for name in sorted(filenames):
            member = base / name
            _guard_member(member, boundary)
            if member.is_file() and not member.is_symlink():
                files.append(member)
    return sorted(files)


def directory_digest(root: str | Path, boundary: str | Path) -> str:
    """Stable aggregate digest over sorted `relative_path\\0file_digest` pairs.

    Directory timestamps are never used; only sorted relative paths and per-file content
    digests contribute, so the result is deterministic for unchanged content and independent
    of enumeration order. An empty directory has a well-defined stable digest.
    """
    root = Path(root)
    boundary = Path(boundary)
    h = hashlib.sha256()
    for member in _iter_directory_files(root, boundary):
        rel = member.relative_to(root).as_posix()
        h.update(rel.encode("utf-8"))
        h.update(b"\0")
        h.update(sha256_file(member).encode("ascii"))
        h.update(b"\n")
    return h.hexdigest()


def _directory_children(root: Path, boundary: Path) -> list[dict[str, Any]]:
    root = Path(root)
    children: list[dict[str, Any]] = []
    for member in _iter_directory_files(root, boundary):
        children.append(
            {
                "relative_path": member.relative_to(root).as_posix(),
                "digest": sha256_file(member),
                "size_bytes": member.stat().st_size,
            }
        )
    return children


# -- sqlite snapshot + integrity ----------------------------------------------------------


def sqlite_integrity(path: str | Path, quick: bool = True) -> str:
    """Run PRAGMA quick_check/integrity_check on a database opened read-only.

    Returns the first row of the result ("ok" on success) or a short failure string. Never
    mutates the database and never raises for a corrupt file — a corrupt/non-SQLite file
    yields a non-"ok" string so callers can fail closed explicitly.
    """
    pragma = "quick_check" if quick else "integrity_check"
    uri = f"file:{Path(path)}?mode=ro&immutable=1"
    try:
        conn = sqlite3.connect(uri, uri=True)
    except sqlite3.Error as exc:
        return f"unopenable: {exc}"
    try:
        row = conn.execute(f"PRAGMA {pragma}").fetchone()
    except sqlite3.DatabaseError as exc:
        return f"error: {exc}"
    finally:
        conn.close()
    return row[0] if row else "no-result"


def snapshot_sqlite(src: str | Path, dst: str | Path) -> str:
    """Create a consistent copy of `src` at `dst` via the SQLite online-backup API.

    Uses `sqlite3.Connection.backup()`, which produces a transactionally-consistent copy even
    for WAL-mode or actively-written databases without a naive byte copy. The source is opened
    read-only and never mutated. Returns the integrity result of the copy ("ok"); raises
    ManifestError if the source cannot be opened or the copy fails its integrity check.
    """
    src = Path(src)
    dst = Path(dst)
    src_uri = f"file:{src}?mode=ro"
    try:
        source = sqlite3.connect(src_uri, uri=True)
    except sqlite3.Error as exc:
        raise ManifestError(f"cannot open source database: {src} ({exc})") from exc
    try:
        # Probe first so a non-SQLite file fails closed before we emit a copy.
        try:
            source.execute("PRAGMA schema_version").fetchone()
        except sqlite3.DatabaseError as exc:
            raise ManifestError(f"source is not a valid SQLite database: {src} ({exc})") from exc
        dest = sqlite3.connect(dst)
        try:
            source.backup(dest)
        except sqlite3.Error as exc:
            raise ManifestError(f"sqlite backup failed for {src}: {exc}") from exc
        finally:
            dest.close()
    finally:
        source.close()
    integrity = sqlite_integrity(dst)
    if integrity != "ok":
        raise ManifestError(f"snapshot failed integrity check for {src}: {integrity}")
    return integrity


def _sqlite_user_version(path: Path) -> int | None:
    uri = f"file:{path}?mode=ro&immutable=1"
    try:
        conn = sqlite3.connect(uri, uri=True)
    except sqlite3.Error:
        return None
    try:
        row = conn.execute("PRAGMA user_version").fetchone()
        return int(row[0]) if row else None
    except sqlite3.DatabaseError:
        return None
    finally:
        conn.close()


# -- path safety -------------------------------------------------------------------------


def validate_manifest_paths(relative_paths: list[str]) -> None:
    """Reject absolute paths, `..` traversal, and duplicate normalized relative paths.

    A hostile or malformed manifest whose entries reference `/absolute/path`,
    `../../etc/passwd`, or `payload/../../../tmp/file` fails closed here before any copy.
    """
    seen: set[str] = set()
    for rel in relative_paths:
        if not isinstance(rel, str) or not rel:
            raise ManifestError(f"invalid manifest path: {rel!r}")
        pure = Path(rel)
        if pure.is_absolute() or rel.startswith("/"):
            raise ManifestError(f"absolute manifest path forbidden: {rel}")
        parts = pure.parts
        if ".." in parts:
            raise ManifestError(f"path traversal forbidden in manifest path: {rel}")
        normalized = os.path.normpath(rel)
        if normalized.startswith("..") or os.path.isabs(normalized):
            raise ManifestError(f"path escapes payload root: {rel}")
        if normalized in seen:
            raise ManifestError(f"duplicate normalized manifest path: {rel} ({normalized})")
        seen.add(normalized)


# -- dataclasses -------------------------------------------------------------------------


@dataclass
class ManifestEntry:
    entry_id: str
    state_category: str
    logical_name: str
    relative_path: str
    entry_type: str
    required: bool
    size_bytes: int
    digest: str
    source_path: str = ""
    digest_algorithm: str = DIGEST_ALGORITHM
    modified_at: str | None = None
    sqlite_integrity: str | None = None
    schema_version_meta: dict[str, Any] | None = None
    inclusion_reason: str = ""
    reconstruction_notes: str | None = None
    children: list[dict[str, Any]] | None = None

    def validate(self) -> None:
        if self.entry_type not in SUPPORTED_ENTRY_TYPES:
            raise ManifestError(f"unsupported entry_type: {self.entry_type}")
        if self.state_category not in set(STATE_CATEGORIES.values()):
            raise ManifestError(f"unknown state_category: {self.state_category}")
        if self.digest_algorithm != DIGEST_ALGORITHM:
            raise ManifestError(f"unsupported digest algorithm: {self.digest_algorithm}")

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "entry_id": self.entry_id,
            "state_category": self.state_category,
            "logical_name": self.logical_name,
            "relative_path": self.relative_path,
            "entry_type": self.entry_type,
            "required": self.required,
            "size_bytes": self.size_bytes,
            "digest": self.digest,
            "source_path": self.source_path,
            "digest_algorithm": self.digest_algorithm,
            "inclusion_reason": self.inclusion_reason,
        }
        if self.modified_at is not None:
            d["modified_at"] = self.modified_at
        if self.sqlite_integrity is not None:
            d["sqlite_integrity"] = self.sqlite_integrity
        if self.schema_version_meta is not None:
            d["schema_version_meta"] = self.schema_version_meta
        if self.reconstruction_notes is not None:
            d["reconstruction_notes"] = self.reconstruction_notes
        if self.children is not None:
            d["children"] = self.children
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ManifestEntry:
        return cls(
            entry_id=data.get("entry_id", ""),
            state_category=data.get("state_category", ""),
            logical_name=data.get("logical_name", ""),
            relative_path=data.get("relative_path", ""),
            entry_type=data.get("entry_type", ""),
            required=bool(data.get("required", False)),
            size_bytes=int(data.get("size_bytes", 0)),
            digest=data.get("digest", ""),
            source_path=data.get("source_path", ""),
            digest_algorithm=data.get("digest_algorithm", DIGEST_ALGORITHM),
            modified_at=data.get("modified_at"),
            sqlite_integrity=data.get("sqlite_integrity"),
            schema_version_meta=data.get("schema_version_meta"),
            inclusion_reason=data.get("inclusion_reason", ""),
            reconstruction_notes=data.get("reconstruction_notes"),
            children=data.get("children"),
        )


@dataclass
class BackupManifest:
    manifest_id: str
    created_at: str
    source_boundary: str
    source_data_root: str
    entries: list[ManifestEntry] = field(default_factory=list)
    manifest_schema_version: int = BACKUP_MANIFEST_SCHEMA_VERSION
    generator_version: str = GENERATOR_VERSION
    lighthouse_version: str | None = None
    repository_commit: str | None = None
    profile_schema_version: int | None = None
    inventory_complete: bool = False
    warnings: list[str] = field(default_factory=list)

    def validate(self) -> None:
        if not isinstance(self.manifest_schema_version, int) or isinstance(
            self.manifest_schema_version, bool
        ):
            raise ManifestError("manifest_schema_version must be an integer")
        if self.manifest_schema_version < 1:
            raise ManifestError("manifest_schema_version must be >= 1")
        if self.manifest_schema_version > BACKUP_MANIFEST_SCHEMA_VERSION:
            raise ManifestError(
                f"unsupported manifest_schema_version: {self.manifest_schema_version} "
                f"(max {BACKUP_MANIFEST_SCHEMA_VERSION})"
            )
        if not self.manifest_id:
            raise ManifestError("manifest_id must be non-empty")
        for entry in self.entries:
            entry.validate()
        validate_manifest_paths([e.relative_path for e in self.entries])

    def summary(self) -> dict[str, Any]:
        authoritative = [e for e in self.entries if e.state_category == CATEGORY_AUTHORITATIVE]
        total_files = 0
        for e in self.entries:
            if e.entry_type == ENTRY_TYPE_DIRECTORY:
                total_files += len(e.children or [])
            else:
                total_files += 1
        return {
            "total_entries": len(self.entries),
            "total_files": total_files,
            "total_bytes": sum(e.size_bytes for e in self.entries),
            "authoritative_entries": len(authoritative),
            "excluded_entries": len(self.warnings),
            "warnings": list(self.warnings),
            "blocking_failures": [],
            "readiness_result": "READY" if self.inventory_complete else "UNKNOWN",
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "manifest_schema_version": self.manifest_schema_version,
            "manifest_id": self.manifest_id,
            "created_at": self.created_at,
            "lighthouse_version": self.lighthouse_version,
            "repository_commit": self.repository_commit,
            "profile_schema_version": self.profile_schema_version,
            "source_boundary": self.source_boundary,
            "source_data_root": self.source_data_root,
            "generator_version": self.generator_version,
            "inventory_complete": self.inventory_complete,
            "entries": [e.to_dict() for e in self.entries],
            "summary": self.summary(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BackupManifest:
        return cls(
            manifest_id=data.get("manifest_id", ""),
            created_at=data.get("created_at", ""),
            source_boundary=data.get("source_boundary", ""),
            source_data_root=data.get("source_data_root", ""),
            entries=[ManifestEntry.from_dict(e) for e in data.get("entries", [])],
            manifest_schema_version=data.get(
                "manifest_schema_version", BACKUP_MANIFEST_SCHEMA_VERSION
            ),
            generator_version=data.get("generator_version", GENERATOR_VERSION),
            lighthouse_version=data.get("lighthouse_version"),
            repository_commit=data.get("repository_commit"),
            profile_schema_version=data.get("profile_schema_version"),
            inventory_complete=bool(data.get("inventory_complete", False)),
            warnings=list(data.get("summary", {}).get("warnings", [])),
        )


# -- generation --------------------------------------------------------------------------


def _entry_id(relative_path: str) -> str:
    """Deterministic per-entry identity derived from the relative payload path."""
    return hashlib.sha256(relative_path.encode("utf-8")).hexdigest()[:16]


def _iso_mtime(path: Path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime, tz=UTC).isoformat()


def _build_file_entry(
    logical_name: str, category: str, source: Path, rel: str, required: bool, reason: str
) -> ManifestEntry:
    return ManifestEntry(
        entry_id=_entry_id(rel),
        state_category=category,
        logical_name=logical_name,
        relative_path=rel,
        entry_type=ENTRY_TYPE_FILE,
        required=required,
        size_bytes=source.stat().st_size,
        digest=sha256_file(source),
        source_path=str(source),
        modified_at=_iso_mtime(source),
        inclusion_reason=reason,
    )


def _build_directory_entry(
    logical_name: str,
    category: str,
    source: Path,
    rel: str,
    boundary: Path,
    required: bool,
    reason: str,
    notes: str | None = None,
) -> ManifestEntry:
    children = _directory_children(source, boundary)
    return ManifestEntry(
        entry_id=_entry_id(rel),
        state_category=category,
        logical_name=logical_name,
        relative_path=rel,
        entry_type=ENTRY_TYPE_DIRECTORY,
        required=required,
        size_bytes=sum(c["size_bytes"] for c in children),
        digest=directory_digest(source, boundary),
        source_path=str(source),
        inclusion_reason=reason,
        reconstruction_notes=notes,
        children=children,
    )


def _build_sqlite_entry(
    logical_name: str, source: Path, rel: str, required: bool, reason: str
) -> ManifestEntry:
    # Snapshot into a throwaway temp copy purely to compute a consistent digest + integrity;
    # the source is never mutated and the temp copy is discarded.
    tmp_fd, tmp_name = tempfile.mkstemp(suffix=".db", prefix="lh-manifest-snap-")
    os.close(tmp_fd)
    tmp = Path(tmp_name)
    try:
        integrity = snapshot_sqlite(source, tmp)
        digest = sha256_file(tmp)
        size = tmp.stat().st_size
    finally:
        tmp.unlink(missing_ok=True)
    return ManifestEntry(
        entry_id=_entry_id(rel),
        state_category=CATEGORY_AUTHORITATIVE,
        logical_name=logical_name,
        relative_path=rel,
        entry_type=ENTRY_TYPE_SQLITE,
        required=required,
        size_bytes=size,
        digest=digest,
        source_path=str(source),
        modified_at=_iso_mtime(source),
        sqlite_integrity=integrity,
        schema_version_meta={"user_version": _sqlite_user_version(source)},
        inclusion_reason=reason,
    )


def generate_manifest(
    data_dir: str | Path,
    db_path: str | Path | None = None,
    boundary: str | Path = DATA_BOUNDARY,
    include_reconstructable: bool = False,
    manifest_id: str | None = None,
    created_at: str | None = None,
    lighthouse_version: str | None = None,
    repository_commit: str | None = None,
) -> BackupManifest:
    """Deterministically inventory authoritative (and optionally reconstructable) state.

    Read-only toward the source. Raises `ManifestError` on any fail-closed condition:
    incomplete/absent authoritative state, an authoritative path outside the boundary, a
    symlink escape, a broken authoritative symlink, a special file, or an un-snapshottable DB.
    """
    data_dir = Path(data_dir)
    boundary_path = Path(boundary)
    if db_path is None:
        db_path = data_dir / "state.db"
    db_path = Path(db_path)

    if not boundary_path.is_absolute():
        raise ManifestError(f"backup boundary must be absolute: {boundary_path}")

    # External-unmanaged authoritative state (DB outside the boundary) fails closed.
    if not is_within_boundary(db_path, boundary_path):
        raise ManifestError(
            f"authoritative database escapes boundary ({CATEGORY_EXTERNAL_UNMANAGED}): {db_path}"
        )

    entries: list[ManifestEntry] = []
    warnings: list[str] = []

    for logical_name, rel, entry_type in _REQUIRED_AUTHORITATIVE:
        source = db_path if logical_name == "state_db" else data_dir / rel
        if not source.exists():
            raise ManifestError(f"required authoritative state missing: {logical_name} ({source})")
        if not is_within_boundary(source, boundary_path):
            raise ManifestError(f"authoritative path escapes boundary: {source}")
        # Symlinks within authoritative state may not escape the source data root itself.
        _guard_member(source, data_dir)
        reason = f"required authoritative state ({logical_name})"
        if entry_type == ENTRY_TYPE_SQLITE:
            entries.append(_build_sqlite_entry(logical_name, source, rel, True, reason))
        elif entry_type == ENTRY_TYPE_DIRECTORY:
            entries.append(
                _build_directory_entry(
                    logical_name, CATEGORY_AUTHORITATIVE, source, rel, data_dir, True, reason
                )
            )
        else:
            entries.append(
                _build_file_entry(logical_name, CATEGORY_AUTHORITATIVE, source, rel, True, reason)
            )

    if include_reconstructable:
        for logical_name, rel in _RECONSTRUCTABLE_DIRS:
            source = data_dir / rel
            if not source.exists():
                continue
            _guard_member(source, data_dir)
            entries.append(
                _build_directory_entry(
                    logical_name,
                    CATEGORY_RECONSTRUCTABLE,
                    source,
                    rel,
                    data_dir,
                    False,
                    f"optional reconstructable state ({logical_name})",
                    notes="re-derivable by re-running the corresponding collector",
                )
            )
    else:
        warnings.append("reconstructable state excluded by default (re-derivable)")

    entries.sort(key=lambda e: e.relative_path)
    validate_manifest_paths([e.relative_path for e in entries])

    manifest = BackupManifest(
        manifest_id=manifest_id or _default_manifest_id(),
        created_at=created_at or datetime.now(tz=UTC).isoformat(),
        source_boundary=str(boundary_path),
        source_data_root=str(data_dir),
        entries=entries,
        lighthouse_version=lighthouse_version or detect_lighthouse_version(),
        repository_commit=repository_commit or detect_repository_commit(),
        inventory_complete=True,
        warnings=warnings,
    )
    manifest.validate()
    return manifest


def _default_manifest_id() -> str:
    import uuid

    return f"lhbackup-{uuid.uuid4().hex[:16]}"


def detect_lighthouse_version() -> str | None:
    """Best-effort installed version of the `pattern-match-sentinel` distribution.

    "lighthouse" is only the console-script name, not the distribution name — querying it
    always misses. Returns None (never raises) when the package is not installed.
    """
    try:
        from importlib.metadata import PackageNotFoundError, version

        try:
            return version("pattern-match-sentinel")
        except PackageNotFoundError:
            return None
    except Exception:  # noqa: BLE001 - version metadata is best-effort, never fail closed on it
        return None


def detect_repository_commit() -> str | None:
    """Best-effort git HEAD commit of this checkout; None when git or `.git` is unavailable
    (deployed s9pk/Docker images ship without a repository)."""
    try:
        import subprocess

        repo_root = Path(__file__).resolve().parent.parent
        result = subprocess.run(
            ["git", "-C", str(repo_root), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        commit = result.stdout.strip()
        if result.returncode == 0 and commit:
            return commit
        return None
    except Exception:  # noqa: BLE001 - provenance is best-effort, never fail closed on it
        return None
