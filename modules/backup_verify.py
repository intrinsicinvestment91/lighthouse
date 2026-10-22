"""Independent backup verification and the isolated restore drill (LHP-PROFILE-02).

`verify_backup()` re-derives a backup's integrity from scratch — it re-reads the manifest,
re-hashes every payload file, re-runs SQLite integrity checks, and rejects any drift, extra
file, structural error, or corruption. It never mutates the backup.

`restore_drill()` proves a backup can be *restored* by copying it into an isolated, explicitly
non-production destination and re-verifying the restored bytes. It refuses to restore into
`/data` (or anything beneath it), refuses to overlap the backup or source, requires an empty
destination, never starts Lighthouse against the restored data, and states plainly what it did
and did not prove. A successful drill does NOT authorize a profile switch and is NOT a
production disaster-recovery restore.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from modules.backup_create import (
    MANIFEST_FILENAME,
    PAYLOAD_DIRNAME,
)
from modules.backup_manifest import (
    ENTRY_TYPE_DIRECTORY,
    ENTRY_TYPE_SQLITE,
    BackupError,
    BackupManifest,
    ManifestError,
    sha256_file,
    sqlite_integrity,
)
from modules.backup_readiness import DATA_BOUNDARY, is_within_boundary, resolve_path

# What the drill deliberately does NOT prove (surfaced in every restore-drill report).
_UNPROVEN_GUARANTEES = (
    "production disaster recovery is not proven — this drill restores into isolation only",
    "restoring into the live /data volume is explicitly out of scope",
    "Lighthouse was not started against the restored data",
    "profile activation remains unauthorized regardless of this drill's outcome",
)

# Typed report contracts (LHP-LIFE-EVID-01). artifact_digest uses the same canonical-JSON
# SHA-256 recipe as ActiveProfileState.compute_digest (sort_keys, compact separators, UTF-8).
# manifest_sha256 is a different digest: SHA-256 of the exact manifest.json file bytes.
VERIFICATION_REPORT_SCHEMA = "lighthouse_backup_verification.v1"
RESTORE_DRILL_REPORT_SCHEMA = "lighthouse_restore_drill.v1"
REPORT_SCHEMA_VERSION = 1
_SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")

# Closed vocabulary for reports produced by verify_backup() — the only verification report shape
# accepted by the activation ceremony (LHP-V1-PROFILE-01E / LHP-LIFE-EVID-01).
VERIFY_BACKUP_REPORT_KEYS = frozenset(
    {
        "report_schema",
        "schema_version",
        "status",
        "backup_root",
        "manifest_id",
        "manifest_sha256",
        "checked_entries",
        "structural_errors",
        "missing_payload",
        "digest_mismatches",
        "extra_payload",
        "sqlite_integrity",
        "blocking_failures",
        "warnings",
        "artifact_digest",
    }
)

# backup-create embeds reports/verification.json with this narrower key set and status "passed".
# It is not a verify_backup() report and cannot bind profile-activate.
CREATE_TIME_VERIFICATION_KEYS = frozenset(
    {
        "status",
        "checked_entries",
        "missing_payload",
        "digest_mismatches",
        "sqlite_integrity",
        "blocking_failures",
    }
)
CREATE_TIME_VERIFICATION_REFUSAL = (
    "verification report is backup-create's embedded reports/verification.json "
    "(status 'passed'); profile-activate requires the output of lighthouse "
    "backup-verify (status 'verified'). The create-time report is not sufficient"
)

# Closed vocabulary for reports produced by restore_drill().
RESTORE_DRILL_REPORT_KEYS = frozenset(
    {
        "report_schema",
        "schema_version",
        "status",
        "backup_root",
        "restore_destination",
        "manifest_id",
        "manifest_sha256",
        "manifest_valid",
        "payload_verified",
        "files_restored",
        "restored_digests_verified",
        "sqlite_integrity_verified",
        "source_unchanged",
        "production_restore_performed",
        "profile_activation_authorized",
        "blocking_failures",
        "warnings",
        "unproven_guarantees",
        "note",
        "artifact_digest",
    }
)


def canonical_report_digest(content: dict[str, Any]) -> str:
    """SHA-256 over canonical JSON.

    Exact recipe used by ``ActiveProfileState.compute_digest``: ``json.dumps`` with
    ``sort_keys=True`` and compact separators, then UTF-8 SHA-256. This is the report
    *content* digest. It is not ``manifest_sha256``, which hashes raw ``manifest.json`` bytes.
    """
    canonical = json.dumps(content, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _require_sha256(label: str, value: Any) -> None:
    if not isinstance(value, str) or not _SHA256_HEX.fullmatch(value):
        raise ReportValidationError(f"{label} must be a 64-char lowercase hex SHA-256")


def _require_str(field: str, value: Any) -> None:
    if not isinstance(value, str):
        raise ReportValidationError(f"{field} must be a str")


def _require_bool(field: str, value: Any) -> None:
    if not isinstance(value, bool):
        raise ReportValidationError(f"{field} must be a bool")


def _require_int_nonneg(field: str, value: Any) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ReportValidationError(f"{field} must be an int")
    if value < 0:
        raise ReportValidationError(f"{field} must be >= 0")


def _require_str_list(field: str, value: Any) -> None:
    if not isinstance(value, list):
        raise ReportValidationError(f"{field} must be a list")
    if not all(isinstance(item, str) for item in value):
        raise ReportValidationError(f"{field} must be a list of str")


def _require_str_str_dict(field: str, value: Any) -> None:
    if not isinstance(value, dict):
        raise ReportValidationError(f"{field} must be a dict")
    if not all(isinstance(k, str) and isinstance(v, str) for k, v in value.items()):
        raise ReportValidationError(f"{field} must be a dict with str keys and str values")


def _closed_report(data: Any, *, label: str, allowed_keys: frozenset[str]) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise ReportValidationError(f"{label} report must be a JSON object")
    keys = set(data)
    missing = allowed_keys - keys
    if missing:
        raise ReportValidationError(f"{label} report is missing required keys: {sorted(missing)}")
    extra = keys - allowed_keys
    if extra:
        raise ReportValidationError(f"{label} report carries unexpected keys: {sorted(extra)}")
    return data


def _manifest_file_digest(backup_root: Path) -> str:
    manifest_path = Path(backup_root) / MANIFEST_FILENAME
    try:
        return sha256_file(manifest_path)
    except OSError as exc:
        raise ReportValidationError(
            f"cannot read manifest.json under backup root {backup_root}: {exc}"
        ) from exc


@dataclass
class BackupVerificationReport:
    """Typed ``lighthouse_backup_verification.v1`` report (LHP-LIFE-EVID-01)."""

    report_schema: str
    schema_version: int
    status: str
    backup_root: str
    manifest_id: str
    manifest_sha256: str
    checked_entries: int
    structural_errors: list[str]
    missing_payload: list[str]
    digest_mismatches: list[str]
    extra_payload: list[str]
    sqlite_integrity: dict[str, str]
    blocking_failures: list[str]
    warnings: list[str]
    artifact_digest: str = ""

    def _content(self) -> dict[str, Any]:
        return {
            "report_schema": self.report_schema,
            "schema_version": self.schema_version,
            "status": self.status,
            "backup_root": self.backup_root,
            "manifest_id": self.manifest_id,
            "manifest_sha256": self.manifest_sha256,
            "checked_entries": self.checked_entries,
            "structural_errors": list(self.structural_errors),
            "missing_payload": list(self.missing_payload),
            "digest_mismatches": list(self.digest_mismatches),
            "extra_payload": list(self.extra_payload),
            "sqlite_integrity": dict(self.sqlite_integrity),
            "blocking_failures": list(self.blocking_failures),
            "warnings": list(self.warnings),
        }

    def compute_digest(self) -> str:
        return canonical_report_digest(self._content())

    def to_dict(self) -> dict[str, Any]:
        payload = self._content()
        payload["artifact_digest"] = self.artifact_digest
        return payload

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BackupVerificationReport:
        try:
            raw = _closed_report(data, label="verification", allowed_keys=VERIFY_BACKUP_REPORT_KEYS)
            # Pass raw values through — no list()/dict() coercion (validate() type-checks).
            return cls(
                report_schema=raw["report_schema"],
                schema_version=raw["schema_version"],
                status=raw["status"],
                backup_root=raw["backup_root"],
                manifest_id=raw["manifest_id"],
                manifest_sha256=raw["manifest_sha256"],
                checked_entries=raw["checked_entries"],
                structural_errors=raw["structural_errors"],
                missing_payload=raw["missing_payload"],
                digest_mismatches=raw["digest_mismatches"],
                extra_payload=raw["extra_payload"],
                sqlite_integrity=raw["sqlite_integrity"],
                blocking_failures=raw["blocking_failures"],
                warnings=raw["warnings"],
                artifact_digest=raw["artifact_digest"],
            )
        except ReportValidationError:
            raise
        except (TypeError, ValueError) as exc:
            raise ReportValidationError(
                f"verification report has a malformed field value: {exc}"
            ) from exc

    def validate(self) -> None:
        try:
            self._validate()
        except ReportValidationError:
            raise
        except (TypeError, ValueError) as exc:
            raise ReportValidationError(
                f"verification report has a malformed field value: {exc}"
            ) from exc

    def _validate(self) -> None:
        # Type-check every field before set membership or digest (H-1 / D-1).
        _require_str("report_schema", self.report_schema)
        _require_int_nonneg("schema_version", self.schema_version)
        _require_str("status", self.status)
        _require_str("backup_root", self.backup_root)
        _require_str("manifest_id", self.manifest_id)
        _require_str("manifest_sha256", self.manifest_sha256)
        _require_int_nonneg("checked_entries", self.checked_entries)
        _require_str_list("structural_errors", self.structural_errors)
        _require_str_list("missing_payload", self.missing_payload)
        _require_str_list("digest_mismatches", self.digest_mismatches)
        _require_str_list("extra_payload", self.extra_payload)
        _require_str_str_dict("sqlite_integrity", self.sqlite_integrity)
        _require_str_list("blocking_failures", self.blocking_failures)
        _require_str_list("warnings", self.warnings)
        _require_str("artifact_digest", self.artifact_digest)

        if self.report_schema != VERIFICATION_REPORT_SCHEMA:
            raise ReportValidationError(
                f"verification report_schema must be {VERIFICATION_REPORT_SCHEMA!r}"
            )
        if self.schema_version != REPORT_SCHEMA_VERSION:
            raise ReportValidationError(
                f"verification schema_version must be {REPORT_SCHEMA_VERSION}"
            )
        if self.status not in {"verified", "failed"}:
            raise ReportValidationError(
                f"verification report status must be 'verified' or 'failed', got {self.status!r}"
            )
        # D-2: verified requires empty failure details (mirrors validate_supplied_*).
        if self.status == "verified":
            if self.blocking_failures:
                raise ReportValidationError("verification report lists blocking_failures")
            if self.structural_errors or self.missing_payload or self.digest_mismatches:
                raise ReportValidationError(
                    "verification report records payload or structural failures"
                )
            if self.extra_payload:
                raise ReportValidationError("verification report records unexpected payload files")
            for entry_path, integrity in self.sqlite_integrity.items():
                if integrity != "ok":
                    raise ReportValidationError(
                        f"verification report sqlite_integrity for {entry_path!r} "
                        f"must be 'ok', got {integrity!r}"
                    )
        _require_sha256("verification manifest_sha256", self.manifest_sha256)
        _require_sha256("verification artifact_digest", self.artifact_digest)
        if self.artifact_digest != self.compute_digest():
            raise ReportValidationError(
                "verification artifact_digest does not match the report content"
            )


@dataclass
class RestoreDrillReport:
    """Typed ``lighthouse_restore_drill.v1`` report (LHP-LIFE-EVID-01)."""

    report_schema: str
    schema_version: int
    status: str
    backup_root: str
    restore_destination: str
    manifest_id: str
    manifest_sha256: str
    manifest_valid: bool
    payload_verified: bool
    files_restored: int
    restored_digests_verified: bool
    sqlite_integrity_verified: bool
    source_unchanged: bool
    production_restore_performed: bool
    profile_activation_authorized: bool
    blocking_failures: list[str]
    warnings: list[str]
    unproven_guarantees: list[str]
    note: str
    artifact_digest: str = ""

    def _content(self) -> dict[str, Any]:
        return {
            "report_schema": self.report_schema,
            "schema_version": self.schema_version,
            "status": self.status,
            "backup_root": self.backup_root,
            "restore_destination": self.restore_destination,
            "manifest_id": self.manifest_id,
            "manifest_sha256": self.manifest_sha256,
            "manifest_valid": self.manifest_valid,
            "payload_verified": self.payload_verified,
            "files_restored": self.files_restored,
            "restored_digests_verified": self.restored_digests_verified,
            "sqlite_integrity_verified": self.sqlite_integrity_verified,
            "source_unchanged": self.source_unchanged,
            "production_restore_performed": self.production_restore_performed,
            "profile_activation_authorized": self.profile_activation_authorized,
            "blocking_failures": list(self.blocking_failures),
            "warnings": list(self.warnings),
            "unproven_guarantees": list(self.unproven_guarantees),
            "note": self.note,
        }

    def compute_digest(self) -> str:
        return canonical_report_digest(self._content())

    def to_dict(self) -> dict[str, Any]:
        payload = self._content()
        payload["artifact_digest"] = self.artifact_digest
        return payload

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RestoreDrillReport:
        try:
            raw = _closed_report(
                data, label="restore-drill", allowed_keys=RESTORE_DRILL_REPORT_KEYS
            )
            # Pass raw values through — no list() coercion (validate() type-checks).
            return cls(
                report_schema=raw["report_schema"],
                schema_version=raw["schema_version"],
                status=raw["status"],
                backup_root=raw["backup_root"],
                restore_destination=raw["restore_destination"],
                manifest_id=raw["manifest_id"],
                manifest_sha256=raw["manifest_sha256"],
                manifest_valid=raw["manifest_valid"],
                payload_verified=raw["payload_verified"],
                files_restored=raw["files_restored"],
                restored_digests_verified=raw["restored_digests_verified"],
                sqlite_integrity_verified=raw["sqlite_integrity_verified"],
                source_unchanged=raw["source_unchanged"],
                production_restore_performed=raw["production_restore_performed"],
                profile_activation_authorized=raw["profile_activation_authorized"],
                blocking_failures=raw["blocking_failures"],
                warnings=raw["warnings"],
                unproven_guarantees=raw["unproven_guarantees"],
                note=raw["note"],
                artifact_digest=raw["artifact_digest"],
            )
        except ReportValidationError:
            raise
        except (TypeError, ValueError) as exc:
            raise ReportValidationError(
                f"restore-drill report has a malformed field value: {exc}"
            ) from exc

    def validate(self) -> None:
        try:
            self._validate()
        except ReportValidationError:
            raise
        except (TypeError, ValueError) as exc:
            raise ReportValidationError(
                f"restore-drill report has a malformed field value: {exc}"
            ) from exc

    def _validate(self) -> None:
        # Type-check every field before set membership or digest (H-1 / D-1).
        _require_str("report_schema", self.report_schema)
        _require_int_nonneg("schema_version", self.schema_version)
        _require_str("status", self.status)
        _require_str("backup_root", self.backup_root)
        _require_str("restore_destination", self.restore_destination)
        _require_str("manifest_id", self.manifest_id)
        _require_str("manifest_sha256", self.manifest_sha256)
        _require_bool("manifest_valid", self.manifest_valid)
        _require_bool("payload_verified", self.payload_verified)
        _require_int_nonneg("files_restored", self.files_restored)
        _require_bool("restored_digests_verified", self.restored_digests_verified)
        _require_bool("sqlite_integrity_verified", self.sqlite_integrity_verified)
        _require_bool("source_unchanged", self.source_unchanged)
        _require_bool("production_restore_performed", self.production_restore_performed)
        _require_bool("profile_activation_authorized", self.profile_activation_authorized)
        _require_str_list("blocking_failures", self.blocking_failures)
        _require_str_list("warnings", self.warnings)
        _require_str_list("unproven_guarantees", self.unproven_guarantees)
        _require_str("note", self.note)
        _require_str("artifact_digest", self.artifact_digest)

        if self.report_schema != RESTORE_DRILL_REPORT_SCHEMA:
            raise ReportValidationError(
                f"restore-drill report_schema must be {RESTORE_DRILL_REPORT_SCHEMA!r}"
            )
        if self.schema_version != REPORT_SCHEMA_VERSION:
            raise ReportValidationError(
                f"restore-drill schema_version must be {REPORT_SCHEMA_VERSION}"
            )
        if self.status not in {"completed", "failed"}:
            raise ReportValidationError(
                f"restore-drill report status must be 'completed' or 'failed', got {self.status!r}"
            )
        # D-2: completed requires passing details (mirrors validate_supplied_*).
        if self.status == "completed":
            _require_literal_true(self.manifest_valid, "manifest_valid")
            _require_literal_true(self.payload_verified, "payload_verified")
            _require_literal_true(self.restored_digests_verified, "restored_digests_verified")
            _require_literal_true(self.sqlite_integrity_verified, "sqlite_integrity_verified")
            _require_literal_true(self.source_unchanged, "source_unchanged")
            if self.files_restored <= 0:
                raise ReportValidationError(
                    "restore-drill report files_restored must be greater than zero"
                )
            if self.blocking_failures:
                raise ReportValidationError("restore-drill report lists blocking_failures")
        if self.production_restore_performed is not False:
            raise ReportValidationError(
                "restore-drill report production_restore_performed must be false"
            )
        if self.profile_activation_authorized is not False:
            raise ReportValidationError(
                "restore-drill report profile_activation_authorized must be false"
            )
        _require_sha256("restore-drill manifest_sha256", self.manifest_sha256)
        _require_sha256("restore-drill artifact_digest", self.artifact_digest)
        if self.artifact_digest != self.compute_digest():
            raise ReportValidationError(
                "restore-drill artifact_digest does not match the report content"
            )


def _stamp_verification_report(**fields: Any) -> dict[str, Any]:
    report = BackupVerificationReport(
        report_schema=VERIFICATION_REPORT_SCHEMA,
        schema_version=REPORT_SCHEMA_VERSION,
        artifact_digest="",
        **fields,
    )
    report.artifact_digest = report.compute_digest()
    report.validate()
    return report.to_dict()


def _stamp_restore_report(**fields: Any) -> dict[str, Any]:
    report = RestoreDrillReport(
        report_schema=RESTORE_DRILL_REPORT_SCHEMA,
        schema_version=REPORT_SCHEMA_VERSION,
        artifact_digest="",
        **fields,
    )
    report.artifact_digest = report.compute_digest()
    report.validate()
    return report.to_dict()


class VerificationError(BackupError):
    """Raised when a backup's manifest cannot be located or parsed at all."""


class RestoreDrillError(BackupError):
    """Raised on an unsafe restore-drill destination or an untrusted manifest."""


class ReportValidationError(BackupError):
    """Raised when a supplied verification or restore-drill report fails closed validation."""


def _closed_dict(data: Any, *, label: str, allowed_keys: frozenset[str]) -> dict[str, Any]:
    return _closed_report(data, label=label, allowed_keys=allowed_keys)


def _require_literal_true(value: Any, field: str) -> None:
    if value is not True:
        raise ReportValidationError(f"restore-drill report {field} is not true")


def require_verified_sqlite_coverage(report: BackupVerificationReport, manifest_path: Path) -> None:
    """Require a verified report's ``sqlite_integrity`` keys match the manifest SQLITE set.

    Failed reports are not checked. Unreadable or invalid manifests refuse closed.
    """
    if report.status != "verified":
        return
    path = Path(manifest_path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ReportValidationError(f"manifest is not a JSON object: {path}")
        manifest = BackupManifest.from_dict(data)
        manifest.validate()
    except ReportValidationError:
        raise
    except (OSError, json.JSONDecodeError, ManifestError, TypeError, ValueError) as exc:
        raise ReportValidationError(
            f"cannot load manifest for sqlite coverage check: {path} ({exc})"
        ) from exc
    required = {
        entry.relative_path for entry in manifest.entries if entry.entry_type == ENTRY_TYPE_SQLITE
    }
    reported = set(report.sqlite_integrity)
    missing = required - reported
    if missing:
        raise ReportValidationError(
            f"verification report sqlite_integrity missing required entries: {sorted(missing)}"
        )
    unknown = reported - required
    if unknown:
        raise ReportValidationError(
            f"verification report sqlite_integrity has unknown entries: {sorted(unknown)}"
        )


def validate_supplied_verification_report(
    data: dict[str, Any],
    *,
    backup_root: Path,
    manifest_id: str,
) -> None:
    """Fail-closed binding for a verify_backup() report file (not a create-time stub)."""
    if isinstance(data, dict) and (
        data.get("status") == "passed" or set(data) == CREATE_TIME_VERIFICATION_KEYS
    ):
        raise ReportValidationError(CREATE_TIME_VERIFICATION_REFUSAL)
    parsed = BackupVerificationReport.from_dict(data)
    parsed.validate()
    if parsed.status != "verified":
        raise ReportValidationError(
            f"verification report status must be 'verified', got {parsed.status!r}"
        )
    reported_root = resolve_path(parsed.backup_root)
    if reported_root != backup_root:
        raise ReportValidationError(
            f"verification report backup_root {reported_root!r} does not match "
            f"the supplied backup root {backup_root!r}"
        )
    if parsed.manifest_id != manifest_id:
        raise ReportValidationError(
            f"verification report manifest_id {parsed.manifest_id!r} does not match {manifest_id!r}"
        )
    expected_manifest_sha = _manifest_file_digest(backup_root)
    if parsed.manifest_sha256 != expected_manifest_sha:
        raise ReportValidationError(
            "verification report manifest_sha256 does not match the manifest.json "
            "file bytes at the supplied backup root"
        )
    require_verified_sqlite_coverage(parsed, Path(backup_root) / MANIFEST_FILENAME)
    if parsed.blocking_failures:
        raise ReportValidationError("verification report lists blocking_failures")
    if parsed.structural_errors or parsed.missing_payload or parsed.digest_mismatches:
        raise ReportValidationError("verification report records payload or structural failures")
    if parsed.extra_payload:
        raise ReportValidationError("verification report records unexpected payload files")


def validate_supplied_restore_drill_report(
    data: dict[str, Any],
    *,
    backup_root: Path,
    manifest_id: str,
    source_data_root: Path,
) -> None:
    """Fail-closed binding for a restore_drill() report file."""
    parsed = RestoreDrillReport.from_dict(data)
    parsed.validate()
    if parsed.status != "completed":
        raise ReportValidationError(
            f"restore-drill report status must be 'completed', got {parsed.status!r}"
        )
    reported_root = resolve_path(parsed.backup_root)
    if reported_root != backup_root:
        raise ReportValidationError(
            f"restore-drill report backup_root {reported_root!r} does not match "
            f"the supplied backup root {backup_root!r}"
        )
    if parsed.manifest_id != manifest_id:
        raise ReportValidationError(
            f"restore-drill report manifest_id {parsed.manifest_id!r} does not match "
            f"{manifest_id!r}"
        )
    expected_manifest_sha = _manifest_file_digest(backup_root)
    if parsed.manifest_sha256 != expected_manifest_sha:
        raise ReportValidationError(
            "restore-drill report manifest_sha256 does not match the manifest.json "
            "file bytes at the supplied backup root"
        )
    _require_literal_true(parsed.manifest_valid, "manifest_valid")
    _require_literal_true(parsed.payload_verified, "payload_verified")
    _require_literal_true(parsed.restored_digests_verified, "restored_digests_verified")
    _require_literal_true(parsed.sqlite_integrity_verified, "sqlite_integrity_verified")
    _require_literal_true(parsed.source_unchanged, "source_unchanged")
    files_restored = parsed.files_restored
    if isinstance(files_restored, bool) or not isinstance(files_restored, int):
        raise ReportValidationError("restore-drill report files_restored must be an integer")
    if files_restored <= 0:
        raise ReportValidationError("restore-drill report files_restored must be greater than zero")
    if parsed.blocking_failures:
        raise ReportValidationError("restore-drill report lists blocking_failures")
    if list(parsed.unproven_guarantees) != list(_UNPROVEN_GUARANTEES):
        raise ReportValidationError(
            "restore-drill report unproven_guarantees do not match producer"
        )
    if not isinstance(parsed.note, str) or not parsed.note.strip():
        raise ReportValidationError("restore-drill report note is missing")
    if not isinstance(parsed.restore_destination, str) or not parsed.restore_destination:
        raise ReportValidationError("restore-drill report restore_destination is missing")
    restore_dest = resolve_path(parsed.restore_destination)
    if (
        restore_dest == source_data_root
        or restore_dest in source_data_root.parents
        or source_data_root in restore_dest.parents
    ):
        raise ReportValidationError(
            "restore-drill report restore_destination overlaps the source data root"
        )


def _load_manifest(backup_root: Path) -> BackupManifest:
    manifest_path = backup_root / MANIFEST_FILENAME
    if not manifest_path.is_file():
        raise VerificationError(f"manifest not found: {manifest_path}")
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise VerificationError(
            f"manifest unreadable/unparseable: {manifest_path} ({exc})"
        ) from exc
    return BackupManifest.from_dict(data)


def _expected_payload_files(entries) -> set[str]:
    expected: set[str] = set()
    for entry in entries:
        if entry.entry_type == ENTRY_TYPE_DIRECTORY:
            for child in entry.children or []:
                expected.add(f"{entry.relative_path}/{child['relative_path']}")
        else:
            expected.add(entry.relative_path)
    return expected


def _check_payload(entries, payload_root: Path, detect_extra: bool) -> dict[str, Any]:
    """Recompute digests + SQLite integrity for every entry against `payload_root`."""
    missing: list[str] = []
    mismatches: list[str] = []
    sqlite_results: dict[str, str] = {}
    for entry in entries:
        target = payload_root / entry.relative_path
        if entry.entry_type == ENTRY_TYPE_DIRECTORY:
            for child in entry.children or []:
                child_target = target / child["relative_path"]
                rel = f"{entry.relative_path}/{child['relative_path']}"
                if not child_target.is_file():
                    missing.append(rel)
                elif sha256_file(child_target) != child["digest"]:
                    mismatches.append(rel)
            continue
        if not target.is_file():
            missing.append(entry.relative_path)
            continue
        if entry.entry_type == ENTRY_TYPE_SQLITE:
            integrity = sqlite_integrity(target)
            sqlite_results[entry.relative_path] = integrity
            if integrity != "ok":
                mismatches.append(f"{entry.relative_path}: integrity {integrity}")
        if sha256_file(target) != entry.digest:
            mismatches.append(f"{entry.relative_path}: digest mismatch")

    extra: list[str] = []
    if detect_extra and payload_root.is_dir():
        expected = _expected_payload_files(entries)
        for path in payload_root.rglob("*"):
            if path.is_file():
                rel = path.relative_to(payload_root).as_posix()
                if rel not in expected:
                    extra.append(rel)
    return {
        "missing": missing,
        "mismatches": mismatches,
        "sqlite_integrity": sqlite_results,
        "extra": sorted(extra),
    }


def verify_backup(backup_root: str | Path) -> dict[str, Any]:
    """Independently re-verify a backup. Returns a report; raises only if the manifest is
    absent or unparseable (a backup we cannot even read cannot be verified with certainty)."""
    backup_root = Path(backup_root)
    manifest = _load_manifest(backup_root)

    # Structural manifest problems (unsupported schema, bad entry type, unsupported digest algo,
    # absolute/traversal/duplicate paths) mean the backup is invalid but readable -> status fail.
    manifest_sha256 = sha256_file(backup_root / MANIFEST_FILENAME)
    try:
        manifest.validate()
    except ManifestError as exc:
        return _stamp_verification_report(
            status="failed",
            backup_root=str(backup_root),
            manifest_id=manifest.manifest_id,
            manifest_sha256=manifest_sha256,
            checked_entries=0,
            structural_errors=[str(exc)],
            missing_payload=[],
            digest_mismatches=[],
            extra_payload=[],
            sqlite_integrity={},
            blocking_failures=[f"structural: {exc}"],
            warnings=[],
        )

    checks = _check_payload(manifest.entries, backup_root / PAYLOAD_DIRNAME, detect_extra=True)
    blocking = [
        *(f"missing payload: {m}" for m in checks["missing"]),
        *checks["mismatches"],
        *(f"unexpected payload file: {e}" for e in checks["extra"]),
    ]
    return _stamp_verification_report(
        status="verified" if not blocking else "failed",
        backup_root=str(backup_root),
        manifest_id=manifest.manifest_id,
        manifest_sha256=manifest_sha256,
        checked_entries=len(manifest.entries),
        structural_errors=[],
        missing_payload=checks["missing"],
        digest_mismatches=checks["mismatches"],
        extra_payload=checks["extra"],
        sqlite_integrity=checks["sqlite_integrity"],
        blocking_failures=blocking,
        warnings=[],
    )


def _guard_restore_destination(
    backup_root: Path, destination: str | Path, source_data_root: str | Path | None
) -> Path:
    dest_str = str(destination)
    if not dest_str.strip():
        raise RestoreDrillError("explicit restore-drill destination required")
    if not Path(destination).is_absolute():
        raise RestoreDrillError(f"restore-drill destination must be absolute: {dest_str}")

    dst = resolve_path(destination)
    if dst == resolve_path(DATA_BOUNDARY) or is_within_boundary(dst, DATA_BOUNDARY):
        raise RestoreDrillError(
            f"restore-drill destination must never be {DATA_BOUNDARY} or beneath it: {dst}"
        )
    backup = resolve_path(backup_root)
    if dst == backup or dst in backup.parents or backup in dst.parents:
        raise RestoreDrillError(f"restore-drill destination overlaps the backup root: {dst}")
    if source_data_root is not None:
        src = resolve_path(source_data_root)
        if dst == src or dst in src.parents or src in dst.parents:
            raise RestoreDrillError(f"restore-drill destination overlaps the source root: {dst}")
    if dst.exists():
        if not dst.is_dir():
            raise RestoreDrillError(f"restore-drill destination is not a directory: {dst}")
        if any(dst.iterdir()):
            raise RestoreDrillError(f"restore-drill destination is not empty: {dst}")
    return dst


def _restore_payload(entries, payload_root: Path, destination: Path) -> None:
    for entry in entries:
        src = payload_root / entry.relative_path
        target = destination / entry.relative_path
        if entry.entry_type == ENTRY_TYPE_DIRECTORY:
            target.mkdir(parents=True, exist_ok=True)
            for child in entry.children or []:
                child_src = src / child["relative_path"]
                if not child_src.is_file():
                    continue
                child_target = target / child["relative_path"]
                child_target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(child_src, child_target)
        else:
            if not src.is_file():
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, target)


def restore_drill(
    backup_root: str | Path,
    destination: str | Path,
    source_data_root: str | Path | None = None,
) -> dict[str, Any]:
    """Restore a backup into an isolated destination and re-verify it. Fails closed on an unsafe
    destination or an untrusted manifest; otherwise returns a report distinguishing what the
    drill proved from what it did not."""
    backup_root = Path(backup_root)
    dst = _guard_restore_destination(backup_root, destination, source_data_root)

    manifest = _load_manifest(backup_root)
    try:
        manifest.validate()
    except ManifestError as exc:
        # An untrusted manifest (traversal/absolute path, bad schema/type) is never restored.
        raise RestoreDrillError(f"refusing to restore an invalid manifest: {exc}") from exc

    payload_root = backup_root / PAYLOAD_DIRNAME
    pre = _check_payload(manifest.entries, payload_root, detect_extra=True)
    payload_verified = not (pre["missing"] or pre["mismatches"] or pre["extra"])

    dst.mkdir(parents=True, exist_ok=True)
    _restore_payload(manifest.entries, payload_root, dst)

    post = _check_payload(manifest.entries, dst, detect_extra=False)
    restored_ok = not (post["missing"] or post["mismatches"])
    sqlite_entries = [e for e in manifest.entries if e.entry_type == ENTRY_TYPE_SQLITE]
    sqlite_ok = all(post["sqlite_integrity"].get(e.relative_path) == "ok" for e in sqlite_entries)
    files_restored = sum(1 for p in dst.rglob("*") if p.is_file())

    blocking = [
        *(f"restored file missing: {m}" for m in post["missing"]),
        *(f"restored: {m}" for m in post["mismatches"]),
    ]
    status = "completed" if (payload_verified and restored_ok and sqlite_ok) else "failed"
    return _stamp_restore_report(
        status=status,
        backup_root=str(backup_root),
        restore_destination=str(dst),
        manifest_id=manifest.manifest_id,
        manifest_sha256=sha256_file(backup_root / MANIFEST_FILENAME),
        manifest_valid=True,
        payload_verified=payload_verified,
        files_restored=files_restored,
        restored_digests_verified=restored_ok,
        sqlite_integrity_verified=bool(sqlite_ok),
        source_unchanged=True,
        production_restore_performed=False,
        profile_activation_authorized=False,
        blocking_failures=blocking,
        warnings=[],
        unproven_guarantees=list(_UNPROVEN_GUARANTEES),
        note=(
            "Restore drill is an isolated verification exercise. A completed drill proves the "
            "backup can be restored and re-verified outside production; it does NOT perform a "
            "production restore and does NOT authorize profile activation."
        ),
    )
