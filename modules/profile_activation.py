"""Backup-gated initial RCD activation (LHP-V1-PROFILE-01C/01E).

One safe local transition only: ``no_active_profile → repository_capability_discovery`` v2.

Requires exact typed confirmation, a ready transition preview, the verified backup root,
and the restore-drill evidence ladder bound to the target profile digest and the configured
data snapshot. Dry-run writes nothing. Authority pins stay false.

Does not switch, deactivate, reset, contact the Gateway, or activate any other profile.
"""

from __future__ import annotations

import hmac
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from modules import active_profile_store
from modules.activation_preconditions import (
    DECISION_READY,
    evaluate_activation_preconditions,
)
from modules.active_profile_state import (
    ACTIVATION_SOURCE_LOCAL_CLI,
    STATE_ACTIVE,
    STATE_NO_ACTIVE_PROFILE,
    ActivationEvidenceReferences,
    build_active_profile_state,
)
from modules.backup_create import MANIFEST_FILENAME, PAYLOAD_DIRNAME
from modules.backup_manifest import BackupManifest, ManifestError, sha256_file
from modules.backup_readiness import resolve_path
from modules.backup_verify import (
    ReportValidationError,
    validate_supplied_restore_drill_report,
    validate_supplied_verification_report,
    verify_backup,
)
from modules.data_paths import resolve_data_dir
from modules.profile_registry import get_profile
from modules.profile_transition import (
    RESTORE_DRILL_PASS_STATUS,
    STATUS_READY,
    VERIFICATION_PASS_STATUSES,
    ProfileTransitionError,
    ProfileTransitionPreview,
    TransitionEvidenceError,
    load_evidence,
    profile_content_sha256,
)

RCD_PROFILE_ID = "repository_capability_discovery"
RCD_PROFILE_VERSION = 2
REQUIRED_CONFIRMATION = "ACTIVATE repository_capability_discovery v2"

AUTHORITY_PINS_FALSE = (
    "authorizes_execution",
    "authorizes_publication",
    "authorizes_release",
    "authorizes_retention",
    "is_finding",
    "is_hypothesis",
    "contacts_gateway",
    "activates_remotely",
    "widens_exposure",
)


class ActivationError(Exception):
    """Base class for activation ceremony failures."""


class ActivationRefused(ActivationError):
    """Preconditions, confirmation, evidence, or target identity failed closed."""


def activate_repository_capability_discovery(
    *,
    confirmation: str,
    transition_preview: str | Path,
    backup_root: str | Path,
    backup_manifest: str | Path,
    verification_report: str | Path,
    restore_report: str | Path,
    data_dir: str | Path | None = None,
    dry_run: bool = False,
    now: str | None = None,
) -> dict[str, Any]:
    """Run the closed initial-activation ceremony for RCD v2.

    Returns an audit result. On success ``status`` is ``activated``, ``already_active``,
    or ``previewed`` (dry-run). All authority pins are identity-checked ``False``.
    """
    if confirmation != REQUIRED_CONFIRMATION:
        raise ActivationRefused(f"confirmation mismatch — type exactly: {REQUIRED_CONFIRMATION!r}")

    resolved_backup_root = _resolve_backup_root(backup_root)
    configured_data = _resolve_data_dir(data_dir)
    target = get_profile(RCD_PROFILE_ID, version=RCD_PROFILE_VERSION)
    target.validate()
    target_digest = profile_content_sha256(target)
    if target.profile_version != RCD_PROFILE_VERSION:
        raise ActivationRefused("target profile version is not the allowed RCD v2")
    if not target.backup_policy.get("backup_required_on_activation"):
        raise ActivationRefused("target profile does not require verified backup on activation")

    preview = _load_preview(transition_preview)
    if preview.target_profile_id != RCD_PROFILE_ID:
        raise ActivationRefused(
            f"preview target_profile_id must be {RCD_PROFILE_ID!r}, "
            f"got {preview.target_profile_id!r}"
        )
    if preview.target_profile_version != RCD_PROFILE_VERSION:
        raise ActivationRefused(
            f"preview target_profile_version must be {RCD_PROFILE_VERSION}, "
            f"got {preview.target_profile_version!r}"
        )
    if preview.target_profile_sha256 != target_digest:
        raise ActivationRefused(
            "preview target_profile_sha256 does not match the live RCD v2 digest"
        )
    if preview.transition_status != STATUS_READY:
        raise ActivationRefused(
            f"preview is not ready_for_review (status={preview.transition_status!r})"
        )
    if preview.current_profile_id is not None or preview.current_profile_version is not None:
        raise ActivationRefused(
            "initial activation requires a foundation preview (no current profile)"
        )

    manifest_path = Path(backup_manifest)
    verification_path = Path(verification_report)
    restore_path = Path(restore_report)

    evidence_refs = _validate_activation_evidence(
        preview=preview,
        backup_root=resolved_backup_root,
        manifest_path=manifest_path,
        verification_path=verification_path,
        restore_path=restore_path,
        configured_data=configured_data,
    )

    current = active_profile_store.read_active_profile_state()
    if current.activation_state == STATE_ACTIVE:
        if (
            current.active_profile_id == RCD_PROFILE_ID
            and current.active_profile_version == RCD_PROFILE_VERSION
            and current.active_profile_sha256 == target_digest
        ):
            return _audit_result(
                status="already_active",
                state=current,
                dry_run=dry_run,
                target_digest=target_digest,
            )
        raise ActivationRefused(
            "unsupported profile switch: a different profile is already active "
            f"({current.active_profile_id!r} v{current.active_profile_version})"
        )
    if current.activation_state != STATE_NO_ACTIVE_PROFILE:
        raise ActivationRefused(
            f"unrecognized current activation_state: {current.activation_state!r}"
        )

    preconditions = evaluate_activation_preconditions(
        preview.to_dict(),
        current_state=current,
        target_profile=target,
    )
    if preconditions.decision != DECISION_READY:
        raise ActivationRefused(
            f"activation preconditions not ready: {preconditions.decision} "
            f"reasons={list(preconditions.reasons)}"
        )

    activated_at = now or datetime.now(UTC).isoformat()
    state = build_active_profile_state(
        target,
        activated_at=activated_at,
        activation_source=ACTIVATION_SOURCE_LOCAL_CLI,
        evidence=evidence_refs,
    )

    if dry_run:
        return _audit_result(
            status="previewed",
            state=state,
            dry_run=True,
            target_digest=target_digest,
        )

    persisted = active_profile_store._persist_initial_activation(state)
    status = "activated" if persisted.artifact_digest == state.artifact_digest else "already_active"
    return _audit_result(
        status=status,
        state=persisted,
        dry_run=False,
        target_digest=target_digest,
    )


def _resolve_backup_root(backup_root: str | Path | None) -> Path:
    if backup_root is None or (isinstance(backup_root, str) and not backup_root.strip()):
        raise ActivationRefused("backup_root is required")
    try:
        resolved = resolve_path(backup_root)
    except Exception as exc:  # noqa: BLE001 — fail closed on unresolvable backup root
        raise ActivationRefused(f"backup_root is unusable: {exc}") from exc
    if not resolved.is_dir():
        raise ActivationRefused(f"backup_root is not a directory: {resolved}")
    return resolved


def _resolve_data_dir(data_dir: str | Path | None) -> Path:
    try:
        target = resolve_data_dir() if data_dir is None else data_dir
        return resolve_path(target)
    except Exception as exc:  # noqa: BLE001 — fail closed on unresolvable data root
        raise ActivationRefused(f"configured data directory is unusable: {exc}") from exc


def _load_preview(path: str | Path) -> ProfileTransitionPreview:
    try:
        raw = Path(path).read_text(encoding="utf-8")
        data = json.loads(raw)
    except (OSError, json.JSONDecodeError) as exc:
        raise ActivationRefused(f"transition preview unreadable: {exc}") from exc
    try:
        preview = ProfileTransitionPreview.from_dict(data)
        preview.validate()
    except (ProfileTransitionError, Exception) as exc:  # noqa: BLE001
        raise ActivationRefused(f"transition preview invalid: {exc}") from exc
    # Preview self-integrity: the digest activation records must be the digest of the
    # content actually supplied, not an unverified claim carried inside the file.
    try:
        intact = hmac.compare_digest(
            preview.artifact_digest.encode("utf-8"), preview.compute_digest().encode("utf-8")
        )
    except Exception:  # noqa: BLE001 — fail closed on a non-string or unencodable digest
        intact = False
    if not intact:
        raise ActivationRefused(
            "transition preview artifact_digest does not match its canonical content"
        )
    return preview


_MANIFEST_JSON_KEYS = frozenset(
    {
        "manifest_schema_version",
        "manifest_id",
        "created_at",
        "lighthouse_version",
        "repository_commit",
        "profile_schema_version",
        "source_boundary",
        "source_data_root",
        "generator_version",
        "inventory_complete",
        "entries",
        "summary",
    }
)


def _strict_json_object(data: Any, *, label: str, allowed_keys: frozenset[str]) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise ActivationRefused(f"{label} must be a JSON object")
    keys = set(data)
    missing = allowed_keys - keys
    if missing:
        raise ActivationRefused(f"{label} is missing required keys: {sorted(missing)}")
    extra = keys - allowed_keys
    if extra:
        raise ActivationRefused(f"{label} carries unexpected keys: {sorted(extra)}")
    return data


def _load_manifest_contract(manifest_path: Path, *, backup_root: Path) -> BackupManifest:
    canonical_manifest = backup_root / MANIFEST_FILENAME
    try:
        resolved_manifest = resolve_path(manifest_path)
        resolved_canonical = resolve_path(canonical_manifest)
    except Exception as exc:  # noqa: BLE001
        raise ActivationRefused(f"manifest path is unusable: {exc}") from exc
    if resolved_manifest != resolved_canonical:
        raise ActivationRefused(
            "backup manifest path does not match the backup root layout "
            f"(expected {resolved_canonical}, got {resolved_manifest})"
        )
    if not resolved_manifest.is_file():
        raise ActivationRefused(f"backup manifest not found: {resolved_manifest}")
    payload_root = backup_root / PAYLOAD_DIRNAME
    if not payload_root.is_dir():
        raise ActivationRefused(f"backup payload directory missing: {payload_root}")
    try:
        raw = json.loads(resolved_manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ActivationRefused(f"backup manifest unreadable: {exc}") from exc
    _strict_json_object(raw, label="backup manifest", allowed_keys=_MANIFEST_JSON_KEYS)
    try:
        manifest = BackupManifest.from_dict(raw)
        manifest.validate()
    except (ManifestError, Exception) as exc:  # noqa: BLE001
        raise ActivationRefused(f"backup manifest invalid: {exc}") from exc
    return manifest


def _validate_activation_evidence(
    *,
    preview: ProfileTransitionPreview,
    backup_root: Path,
    manifest_path: Path,
    verification_path: Path,
    restore_path: Path,
    configured_data: Path,
) -> ActivationEvidenceReferences:
    """Cryptographically bind preview ↔ manifest ↔ reports ↔ backup root ↔ data snapshot."""
    manifest = _load_manifest_contract(manifest_path, backup_root=backup_root)
    manifest_bytes_digest = sha256_file(manifest_path)
    manifest_id = manifest.manifest_id

    try:
        source_root = resolve_path(manifest.source_data_root)
    except Exception as exc:  # noqa: BLE001
        raise ActivationRefused(f"manifest source_data_root is unusable: {exc}") from exc
    if source_root != configured_data:
        raise ActivationRefused(
            "backup manifest source_data_root does not match the configured data snapshot "
            f"(manifest={source_root}, configured={configured_data})"
        )

    try:
        evidence = load_evidence(manifest_path, verification_path, restore_path)
    except TransitionEvidenceError as exc:
        raise ActivationRefused(f"evidence report invalid: {exc}") from exc
    if not evidence.is_passing():
        raise ActivationRefused(
            "backup/restore evidence is missing or not passing "
            f"(verification={evidence.backup_verification_status!r}, "
            f"restore={evidence.restore_drill_status!r})"
        )
    if evidence.backup_manifest_id != manifest_id:
        raise ActivationRefused("loaded evidence manifest_id does not match the manifest file")
    if preview.backup_manifest_id != manifest_id:
        raise ActivationRefused("preview backup_manifest_id does not match the manifest file")
    if preview.backup_manifest_sha256 != manifest_bytes_digest:
        raise ActivationRefused(
            "preview backup_manifest_sha256 does not match the manifest file digest"
        )
    if evidence.backup_manifest_sha256 != manifest_bytes_digest:
        raise ActivationRefused("evidence backup_manifest_sha256 mismatch")

    try:
        verification = json.loads(verification_path.read_text(encoding="utf-8"))
        restore = json.loads(restore_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ActivationRefused(f"evidence artifact unreadable: {exc}") from exc

    try:
        validate_supplied_verification_report(
            verification,
            backup_root=backup_root,
            manifest_id=manifest_id,
        )
        validate_supplied_restore_drill_report(
            restore,
            backup_root=backup_root,
            manifest_id=manifest_id,
            source_data_root=source_root,
        )
    except ReportValidationError as exc:
        raise ActivationRefused(f"evidence report invalid: {exc}") from exc

    if restore.get("status") != RESTORE_DRILL_PASS_STATUS:
        raise ActivationRefused(
            f"restore-drill report status is not completed: {restore.get('status')!r}"
        )

    try:
        fresh_verification = verify_backup(backup_root)
    except Exception as exc:  # noqa: BLE001
        raise ActivationRefused(f"backup re-verification failed: {exc}") from exc
    if fresh_verification.get("status") not in VERIFICATION_PASS_STATUSES:
        raise ActivationRefused(
            f"backup re-verification is not passing: {fresh_verification.get('status')!r}"
        )
    if fresh_verification.get("manifest_id") != manifest_id:
        raise ActivationRefused("re-verified backup manifest_id mismatch")
    if fresh_verification.get("manifest_sha256") != manifest_bytes_digest:
        raise ActivationRefused(
            "re-verified backup manifest_sha256 does not match the manifest file digest"
        )
    try:
        fresh_root = resolve_path(fresh_verification.get("backup_root", ""))
    except Exception as exc:  # noqa: BLE001
        raise ActivationRefused(f"re-verified backup_root is unusable: {exc}") from exc
    if fresh_root != backup_root:
        raise ActivationRefused("re-verified backup_root does not match the supplied backup root")

    verification_digest = sha256_file(verification_path)
    restore_digest = sha256_file(restore_path)

    return ActivationEvidenceReferences(
        transition_preview_id=preview.preview_id,
        transition_preview_digest=preview.artifact_digest,
        backup_manifest_id=manifest_id,
        backup_manifest_sha256=manifest_bytes_digest,
        verification_report_digest=verification_digest,
        restore_report_digest=restore_digest,
        source_data_root=str(source_root),
        backup_root=str(backup_root),
    )


def _audit_result(
    *,
    status: str,
    state,
    dry_run: bool,
    target_digest: str,
) -> dict[str, Any]:
    result = {
        "status": status,
        "dry_run": dry_run,
        "schema_version": state.schema_version,
        "activation_state": state.activation_state,
        "active_profile_id": state.active_profile_id,
        "active_profile_version": state.active_profile_version,
        "activated_at": state.activated_at,
        "activation_source": state.activation_source,
        "active_profile_sha256": state.active_profile_sha256,
        "artifact_digest": state.artifact_digest,
        "target_profile_sha256": target_digest,
        "profile_id": RCD_PROFILE_ID,
        "profile_version": RCD_PROFILE_VERSION,
        "transition_preview_id": state.transition_preview_id,
        "transition_preview_digest": state.transition_preview_digest,
        "backup_manifest_id": state.backup_manifest_id,
        "backup_manifest_sha256": state.backup_manifest_sha256,
        "verification_report_digest": state.verification_report_digest,
        "restore_report_digest": state.restore_report_digest,
        "source_data_root": state.source_data_root,
        "backup_root": state.backup_root,
    }
    for pin in AUTHORITY_PINS_FALSE:
        result[pin] = False
    return result
