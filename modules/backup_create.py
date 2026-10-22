"""Verified backup creation to an explicit operator-chosen destination (LHP-PROFILE-02).

`create_backup()` writes a deterministic, portable backup at a destination the operator names
explicitly — it never infers a destination, never overwrites unrelated content, and leaves the
source byte-identical. It stages into a temporary sibling directory and atomically renames into
place, so an interrupted or failed creation never leaves a half-finished backup that could be
mistaken for a finalized one. Every backup is independently re-verified before finalization; if
verification fails, the whole operation fails closed and the staging directory is removed.

Producing a backup is emphatically NOT a production restore and NEVER authorizes a profile
switch — the returned report pins `restore_drill_completed` and `profile_activation_authorized`
to `False`.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

from modules.backup_manifest import (
    DATA_BOUNDARY,
    ENTRY_TYPE_DIRECTORY,
    ENTRY_TYPE_SQLITE,
    BackupError,
    ManifestError,
    generate_manifest,
    sha256_file,
    snapshot_sqlite,
    sqlite_integrity,
)
from modules.backup_readiness import resolve_path

MANIFEST_FILENAME = "manifest.json"
PAYLOAD_DIRNAME = "payload"
REPORTS_DIRNAME = "reports"
VERIFICATION_REPORT = "verification.json"


class BackupCreateError(BackupError):
    """Raised on any fail-closed condition during backup creation."""


def _paths_overlap(a: Path, b: Path) -> bool:
    """True if `a` and `b` are equal or one contains the other (either direction)."""
    return a == b or a in b.parents or b in a.parents


def _validate_destination(data_dir: Path, destination: str | Path) -> tuple[Path, Path]:
    dest_str = str(destination)
    if not dest_str.strip():
        raise BackupCreateError("explicit backup destination required")
    if not Path(destination).is_absolute():
        raise BackupCreateError(f"backup destination must be an absolute path: {dest_str}")

    src = resolve_path(data_dir)
    dst = resolve_path(destination)
    if _paths_overlap(src, dst):
        raise BackupCreateError(
            f"backup destination must not equal or overlap the source data root "
            f"(source={src}, destination={dst})"
        )
    if not dst.parent.exists():
        raise BackupCreateError(f"backup destination parent does not exist: {dst.parent}")
    if dst.exists():
        if not dst.is_dir():
            raise BackupCreateError(f"backup destination exists and is not a directory: {dst}")
        if any(dst.iterdir()):
            raise BackupCreateError(f"backup destination already contains content: {dst}")
    return src, dst


def _copy_payload(entries, payload_root: Path) -> None:
    for entry in entries:
        target = payload_root / entry.relative_path
        source = Path(entry.source_path)
        if entry.entry_type == ENTRY_TYPE_DIRECTORY:
            target.mkdir(parents=True, exist_ok=True)
            for child in entry.children or []:
                child_target = target / child["relative_path"]
                child_target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source / child["relative_path"], child_target)
        elif entry.entry_type == ENTRY_TYPE_SQLITE:
            target.parent.mkdir(parents=True, exist_ok=True)
            snapshot_sqlite(source, target)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)


def _verify_payload(entries, payload_root: Path) -> dict[str, Any]:
    """Independently recompute payload digests + SQLite integrity against the manifest entries."""
    digest_mismatches: list[str] = []
    missing: list[str] = []
    sqlite_results: dict[str, str] = {}
    for entry in entries:
        target = payload_root / entry.relative_path
        if not target.exists():
            missing.append(entry.relative_path)
            continue
        if entry.entry_type == ENTRY_TYPE_SQLITE:
            integrity = sqlite_integrity(target)
            sqlite_results[entry.relative_path] = integrity
            if integrity != "ok":
                digest_mismatches.append(f"{entry.relative_path}: integrity {integrity}")
            if sha256_file(target) != entry.digest:
                digest_mismatches.append(f"{entry.relative_path}: digest mismatch")
        elif entry.entry_type == ENTRY_TYPE_DIRECTORY:
            for child in entry.children or []:
                child_target = target / child["relative_path"]
                if not child_target.exists():
                    missing.append(f"{entry.relative_path}/{child['relative_path']}")
                elif sha256_file(child_target) != child["digest"]:
                    digest_mismatches.append(
                        f"{entry.relative_path}/{child['relative_path']}: digest mismatch"
                    )
        else:
            if sha256_file(target) != entry.digest:
                digest_mismatches.append(f"{entry.relative_path}: digest mismatch")
    blocking = [*(f"missing payload: {m}" for m in missing), *digest_mismatches]
    return {
        "status": "passed" if not blocking else "failed",
        "checked_entries": len(entries),
        "missing_payload": missing,
        "digest_mismatches": digest_mismatches,
        "sqlite_integrity": sqlite_results,
        "blocking_failures": blocking,
    }


def create_backup(
    data_dir: str | Path,
    destination: str | Path,
    db_path: str | Path | None = None,
    boundary: str | Path = DATA_BOUNDARY,
    include_reconstructable: bool = False,
    manifest_id: str | None = None,
    created_at: str | None = None,
) -> dict[str, Any]:
    """Create an independently-verified backup at an explicit destination, or fail closed."""
    data_dir = Path(data_dir)
    _src, dst = _validate_destination(data_dir, destination)

    try:
        manifest = generate_manifest(
            data_dir=data_dir,
            db_path=db_path,
            boundary=boundary,
            include_reconstructable=include_reconstructable,
            manifest_id=manifest_id,
            created_at=created_at,
        )
    except ManifestError as exc:
        raise BackupCreateError(f"manifest generation failed: {exc}") from exc

    stage = Path(tempfile.mkdtemp(prefix=dst.name + ".lhstage-", dir=dst.parent))
    try:
        payload_root = stage / PAYLOAD_DIRNAME
        payload_root.mkdir()
        _copy_payload(manifest.entries, payload_root)

        (stage / MANIFEST_FILENAME).write_text(
            json.dumps(manifest.to_dict(), indent=2), encoding="utf-8"
        )

        verification = _verify_payload(manifest.entries, payload_root)
        if verification["status"] != "passed":
            raise BackupCreateError(
                f"backup verification failed: {verification['blocking_failures']}"
            )

        reports_dir = stage / REPORTS_DIRNAME
        reports_dir.mkdir()
        (reports_dir / VERIFICATION_REPORT).write_text(
            json.dumps(verification, indent=2), encoding="utf-8"
        )

        # Atomically move the fully-built, verified backup into place. os.rename onto a missing
        # or empty-directory target is atomic on the same filesystem.
        if dst.exists():
            os.rmdir(dst)  # only reached when dst is an existing EMPTY directory
        os.rename(stage, dst)
    except BaseException:
        shutil.rmtree(stage, ignore_errors=True)
        raise

    summary = manifest.summary()
    return {
        "status": "created",
        "eligibility": summary["readiness_result"],
        "manifest_generated": True,
        "backup_copied": True,
        "backup_verified": True,
        "restore_drill_completed": False,
        "production_restore_performed": False,
        "profile_activation_authorized": False,
        "destination": str(dst),
        "manifest_path": str(dst / MANIFEST_FILENAME),
        "payload_dir": str(dst / PAYLOAD_DIRNAME),
        "verification_report_path": str(dst / REPORTS_DIRNAME / VERIFICATION_REPORT),
        "total_entries": summary["total_entries"],
        "total_files": summary["total_files"],
        "total_bytes": summary["total_bytes"],
        "source_unchanged": True,
        "warnings": summary["warnings"],
        "blocking_failures": [],
        "note": (
            "Backup created and independently verified. This is a backup COPY only — it does "
            "NOT perform a production restore and does NOT authorize any profile activation."
        ),
    }
