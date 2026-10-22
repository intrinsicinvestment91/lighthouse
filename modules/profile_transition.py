"""Deterministic profile-transition preview contract (LHP-PROFILE-03).

A *transition preview* is a deterministic, reviewable, serializable artifact that answers:
"Given a proposed profile transition and verified backup evidence, what would change, what
evidence supports the change, and what would require operator approval?" It is **evidence**,
not a command: it never activates a profile, never switches, never calls the Gateway, never
writes DB state, and never executes a collector. Activation and operator approval remain
future, out-of-scope capabilities (see ADR-0001, ADR-0003).

Fail-closed is the rule. A preview may reach `ready_for_review` ONLY when both profiles are
valid and present, the schema is supported, required policy information is present, and backup
evidence (manifest id + passing verification + completed restore drill) is present and passing.
Missing evidence -> `incomplete`; failed/unknown evidence -> `blocked`; a malformed/unavailable
profile or unsupported schema -> `invalid`. Unknown never becomes `ready_for_review`.

Placement rationale (architecture question 1): this lives in Lighthouse because Lighthouse owns
profile policy, backup-evidence contracts, and the `/data` boundary. The Gateway owns the future
approval *record* (operator identity, timestamp, decision, execution authorization) — none of
which is created here. This module only *names* the approvals a future Gateway must collect.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from modules.backup_manifest import (
    detect_lighthouse_version,
    detect_repository_commit,
    sha256_file,
)
from modules.profiles import IntelligenceProfile

PROFILE_TRANSITION_PREVIEW_SCHEMA_VERSION = 1
GENERATOR_VERSION = "1.1.0"
# Serialization v2 (LHP-PROFILE-14A): `_content()` additionally covers the typed
# target-policy snapshot, the local_only/dms_eligibility change categories, the
# backup-manifest and target-profile content digests, and the producer provenance stamps.
DETERMINISTIC_SERIALIZATION_VERSION = 2
DIGEST_ALGORITHM = "sha256"

# Exactly the four producer facts the typed snapshot carries (Gateway P-11 decision record
# §16 ask #1) — no more, no fewer; `validate()` rejects any other shape.
TARGET_POLICY_SNAPSHOT_KEYS = (
    "local_only",
    "dms_eligibility",
    "data_root",
    "schema_migration_required",
)

_SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")

# Fail-closed transition assessment states.
STATUS_READY = "ready_for_review"
STATUS_BLOCKED = "blocked"
STATUS_INVALID = "invalid"
STATUS_INCOMPLETE = "incomplete"
ALLOWED_TRANSITION_STATUSES = frozenset(
    {STATUS_READY, STATUS_BLOCKED, STATUS_INVALID, STATUS_INCOMPLETE}
)

# The success token emitted by lighthouse backup-verify. Create-time backup-create embeds
# use status "passed" and are a different, untyped contract — they never make a preview
# ready_for_review (LHP-LIFE-EVID-01).
VERIFICATION_PASS_STATUS = "verified"  # canonical modules.backup_verify.verify_backup -> status
VERIFICATION_PASS_STATUSES = frozenset({VERIFICATION_PASS_STATUS})
RESTORE_DRILL_PASS_STATUS = "completed"  # modules.backup_verify.restore_drill -> status

# The digest is computed over `_content()`, which deliberately excludes the volatile fields
# (preview_id, created_at, evidence_timestamp) and the digest itself, so it is stable for
# identical transitions + evidence regardless of when the preview was generated. Stability
# means time-invariance, not install-invariance: the producer provenance stamps
# (lighthouse_version, repository_commit) ARE digest-covered on purpose — uncovered
# provenance would be tamper-editable and useless to the execution ledger and ceremony.


class ProfileTransitionError(Exception):
    """Base class for every fail-closed transition-preview error (single CLI catch point)."""


class TransitionEvidenceError(ProfileTransitionError):
    """Raised when a referenced backup-evidence report cannot be located or parsed."""


# -- evidence ----------------------------------------------------------------------------


@dataclass
class TransitionEvidence:
    """References to existing LHP-PROFILE-02 backup evidence — status only, never contents.

    This dataclass records *that* evidence exists and *whether it passed*; it deliberately holds
    no file contents, database rows, or secrets. `backup_manifest_sha256` is a content
    FINGERPRINT of the referenced manifest file (tamper-evident binding, not disclosure).
    `None` means that piece of evidence was not supplied (missing), which fails closed to
    `incomplete`.
    """

    backup_manifest_id: str | None = None
    backup_manifest_sha256: str | None = None
    backup_verification_status: str | None = None
    restore_drill_status: str | None = None
    evidence_timestamp: str | None = None

    def is_complete(self) -> bool:
        """True only when every evidence reference (incl. the manifest digest) is present."""
        return bool(
            self.backup_manifest_id
            and self.backup_manifest_sha256
            and self.backup_verification_status
            and self.restore_drill_status
        )

    def is_passing(self) -> bool:
        """True only when evidence is complete AND both reports report their pass token."""
        return (
            self.is_complete()
            and self.backup_verification_status in VERIFICATION_PASS_STATUSES
            and self.restore_drill_status == RESTORE_DRILL_PASS_STATUS
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "backup_manifest_id": self.backup_manifest_id,
            "backup_manifest_sha256": self.backup_manifest_sha256,
            "backup_verification_status": self.backup_verification_status,
            "restore_drill_status": self.restore_drill_status,
            "evidence_timestamp": self.evidence_timestamp,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TransitionEvidence:
        return cls(
            backup_manifest_id=data.get("backup_manifest_id"),
            backup_manifest_sha256=data.get("backup_manifest_sha256"),
            backup_verification_status=data.get("backup_verification_status"),
            restore_drill_status=data.get("restore_drill_status"),
            evidence_timestamp=data.get("evidence_timestamp"),
        )


def _read_report(path: str | Path, label: str) -> dict[str, Any]:
    try:
        raw = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise TransitionEvidenceError(f"{label} report unreadable: {path} ({exc})") from exc
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise TransitionEvidenceError(f"{label} report unparseable: {path} ({exc})") from exc
    if not isinstance(data, dict):
        raise TransitionEvidenceError(f"{label} report is not a JSON object: {path}")
    return data


def _parse_typed_verification(data: dict[str, Any], *, path: str | Path):
    from modules.backup_verify import (
        CREATE_TIME_VERIFICATION_KEYS,
        CREATE_TIME_VERIFICATION_REFUSAL,
        BackupVerificationReport,
        ReportValidationError,
    )

    if data.get("status") == "passed" or set(data) == CREATE_TIME_VERIFICATION_KEYS:
        raise TransitionEvidenceError(CREATE_TIME_VERIFICATION_REFUSAL)
    try:
        parsed = BackupVerificationReport.from_dict(data)
        parsed.validate()
    except ReportValidationError as exc:
        raise TransitionEvidenceError(f"verification report invalid: {path} ({exc})") from exc
    return parsed


def _parse_typed_restore(data: dict[str, Any], *, path: str | Path):
    from modules.backup_verify import ReportValidationError, RestoreDrillReport

    try:
        parsed = RestoreDrillReport.from_dict(data)
        parsed.validate()
    except ReportValidationError as exc:
        raise TransitionEvidenceError(f"restore-drill report invalid: {path} ({exc})") from exc
    return parsed


def load_evidence(
    manifest_path: str | Path | None = None,
    verification_report_path: str | Path | None = None,
    restore_report_path: str | Path | None = None,
) -> TransitionEvidence:
    """Read existing LHP-PROFILE-02 evidence reports into a `TransitionEvidence`.

    Consumes — never recreates — backup evidence: it extracts only the manifest id, a content
    fingerprint of the manifest file (sha256 over its bytes), and the two pass/fail status
    tokens (plus a timestamp) — never file contents or rows. A supplied report path must be a
    typed, digest-valid artifact bound to that manifest's id and file-byte digest. A supplied
    path that is missing/unreadable/unparseable/untyped/mismatched fails closed with
    `TransitionEvidenceError`; an omitted path leaves that evidence reference `None` (missing).
    """
    manifest_id: str | None = None
    manifest_sha256: str | None = None
    evidence_timestamp: str | None = None
    verification_status: str | None = None
    restore_status: str | None = None

    if manifest_path is not None:
        data = _read_report(manifest_path, "manifest")
        manifest_id = data.get("manifest_id") or None
        evidence_timestamp = data.get("created_at") or None
        try:
            manifest_sha256 = sha256_file(manifest_path)
        except OSError as exc:
            raise TransitionEvidenceError(
                f"manifest unreadable for content digest: {manifest_path} ({exc})"
            ) from exc
    if verification_report_path is not None:
        data = _read_report(verification_report_path, "verification")
        parsed = _parse_typed_verification(data, path=verification_report_path)
        if manifest_id is not None and parsed.manifest_id != manifest_id:
            raise TransitionEvidenceError(
                "verification report manifest_id does not match the supplied manifest"
            )
        if manifest_sha256 is not None and parsed.manifest_sha256 != manifest_sha256:
            raise TransitionEvidenceError(
                "verification report manifest_sha256 does not match the supplied "
                "manifest.json file bytes"
            )
        if manifest_path is not None:
            from modules.backup_verify import (
                ReportValidationError,
                require_verified_sqlite_coverage,
            )

            try:
                require_verified_sqlite_coverage(parsed, Path(manifest_path))
            except ReportValidationError as exc:
                raise TransitionEvidenceError(
                    f"verification report invalid: {verification_report_path} ({exc})"
                ) from exc
        verification_status = parsed.status
    if restore_report_path is not None:
        data = _read_report(restore_report_path, "restore-drill")
        parsed = _parse_typed_restore(data, path=restore_report_path)
        if manifest_id is not None and parsed.manifest_id != manifest_id:
            raise TransitionEvidenceError(
                "restore-drill report manifest_id does not match the supplied manifest"
            )
        if manifest_sha256 is not None and parsed.manifest_sha256 != manifest_sha256:
            raise TransitionEvidenceError(
                "restore-drill report manifest_sha256 does not match the supplied "
                "manifest.json file bytes"
            )
        restore_status = parsed.status

    return TransitionEvidence(
        backup_manifest_id=manifest_id,
        backup_manifest_sha256=manifest_sha256,
        backup_verification_status=verification_status,
        restore_drill_status=restore_status,
        evidence_timestamp=evidence_timestamp,
    )


# -- deterministic profile comparison ----------------------------------------------------


def _list_diff(current: Any, target: Any) -> dict[str, Any]:
    cur = [str(x) for x in current] if isinstance(current, list) else []
    tgt = [str(x) for x in target] if isinstance(target, list) else []
    cur_set, tgt_set = set(cur), set(tgt)
    added = sorted(tgt_set - cur_set)
    removed = sorted(cur_set - tgt_set)
    return {
        "changed": bool(added or removed),
        "from": sorted(cur_set),
        "to": sorted(tgt_set),
        "added": added,
        "removed": removed,
    }


def _scalar_diff(current: Any, target: Any) -> dict[str, Any]:
    return {"changed": current != target, "from": current, "to": target}


def _dict_diff(current: Any, target: Any) -> dict[str, Any]:
    cur = current if isinstance(current, dict) else {}
    tgt = target if isinstance(target, dict) else {}
    changed_keys = sorted(k for k in set(cur) | set(tgt) if cur.get(k) != tgt.get(k))
    return {"changed": bool(changed_keys), "changed_keys": changed_keys, "from": cur, "to": tgt}


_EMPTY_PROFILE_POLICIES: dict[str, Any] = {}


def compare_profiles(
    current: IntelligenceProfile | None, target: IntelligenceProfile
) -> dict[str, dict[str, Any]]:
    """Deterministic, declarative diff between the current and target profile policies.

    `current` may be `None` (no active profile exists yet — activation is not implemented), in
    which case every populated target policy reads as an introduction (`changed=True`, nothing
    removed). This function executes nothing; it only describes differences.
    """

    def cur(attr: str) -> Any:
        return getattr(current, attr) if current is not None else _EMPTY_PROFILE_POLICIES

    c_source = cur("source_policy")
    t_source = target.source_policy
    c_collector = cur("collector_policy")
    t_collector = target.collector_policy
    c_privacy = cur("privacy_policy")
    t_privacy = target.privacy_policy
    c_publishing = cur("publishing_policy")
    t_publishing = target.publishing_policy
    c_dms = cur("dead_mans_switch_policy")
    t_dms = target.dead_mans_switch_policy

    return {
        "sources": _list_diff(
            c_source.get("allowed_source_types"), t_source.get("allowed_source_types")
        ),
        "collectors": _list_diff(
            c_collector.get("enabled_collectors"), t_collector.get("enabled_collectors")
        ),
        "graph_entities": _dict_diff(cur("graph_policy"), target.graph_policy),
        "correlation": _dict_diff(cur("correlation_policy"), target.correlation_policy),
        "privacy": _dict_diff(c_privacy, t_privacy),
        "publishing": _dict_diff(c_publishing, t_publishing),
        "local_only": _scalar_diff(
            bool(c_privacy.get("local_only")), bool(t_privacy.get("local_only"))
        ),
        "dms_eligibility": _scalar_diff(bool(c_dms.get("eligible")), bool(t_dms.get("eligible"))),
    }


# -- producer-owned execution facts (LHP-PROFILE-14A) -------------------------------------


def _target_policy_snapshot(target: IntelligenceProfile) -> dict[str, Any]:
    """Typed target-policy snapshot — the four first-class facts the Gateway execution
    classifier needs (P-11 decision record §16 ask #1), derived verbatim from a VALIDATED
    target profile. `schema_migration_required` is False by construction:
    `intelligence_profile.v1` defines no migration vocabulary and its validator rejects
    executable intent, so a valid v1→v1 transition can never require a schema migration.
    """
    return {
        "local_only": bool(target.privacy_policy.get("local_only")),
        "dms_eligibility": bool(target.dead_mans_switch_policy.get("eligible")),
        "data_root": target.backup_policy.get("data_root"),
        "schema_migration_required": False,
    }


def profile_content_sha256(profile: IntelligenceProfile) -> str:
    """SHA-256 over the profile's full canonical serialized content (same canonical-JSON
    convention as the preview digest: sort_keys, compact separators, UTF-8).

    Binds the WHOLE profile — including the policy blocks `compare_profiles()` does not
    diff (observation/temporal/interface/backup/compatibility) — so a later reader can
    prove the profile is byte-for-byte the one that was reviewed, by recomputing this
    from the live registry object. Public: the activation-preconditions evaluator
    (LHP-PROFILE-14B) recomputes this against the live registry profile; the two
    computations must never drift.
    """
    canonical = json.dumps(profile.to_dict(), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _validate_target_policy_snapshot(snapshot: Any) -> None:
    """Fail-closed shape check: exactly the four keys, real booleans, str-or-None data_root."""
    if not isinstance(snapshot, dict):
        raise ProfileTransitionError("target_policy_snapshot must be an object")
    if set(snapshot) != set(TARGET_POLICY_SNAPSHOT_KEYS):
        raise ProfileTransitionError(
            "target_policy_snapshot must contain exactly the keys "
            f"{sorted(TARGET_POLICY_SNAPSHOT_KEYS)}"
        )
    for key in ("local_only", "dms_eligibility", "schema_migration_required"):
        if not isinstance(snapshot[key], bool):
            raise ProfileTransitionError(f"target_policy_snapshot.{key} must be a boolean")
    data_root = snapshot["data_root"]
    if data_root is not None and (not isinstance(data_root, str) or not data_root):
        raise ProfileTransitionError(
            "target_policy_snapshot.data_root must be a non-empty string or None"
        )


# -- preview artifact --------------------------------------------------------------------


@dataclass
class ProfileTransitionPreview:
    # Identity
    preview_id: str
    created_at: str
    # Current (future placeholder — no active profile exists yet)
    current_profile_id: str | None
    current_profile_version: int | None
    # Requested
    target_profile_id: str | None
    target_profile_version: int | None
    # Assessment
    transition_status: str
    # Changes (named per the artifact contract; each is a deterministic diff sub-dict)
    collectors_changed: dict[str, Any] = field(default_factory=dict)
    sources_changed: dict[str, Any] = field(default_factory=dict)
    graph_entities_changed: dict[str, Any] = field(default_factory=dict)
    correlation_policy_changed: dict[str, Any] = field(default_factory=dict)
    publishing_policy_changed: dict[str, Any] = field(default_factory=dict)
    privacy_policy_changed: dict[str, Any] = field(default_factory=dict)
    # Scalar change categories (LHP-PROFILE-14A) — siblings of the six above, NEVER nested
    # inside them (the Gateway retention scan inspects key names inside the dict-diff blocks)
    local_only_changed: dict[str, Any] = field(default_factory=dict)
    dms_eligibility_changed: dict[str, Any] = field(default_factory=dict)
    # Evidence (references + content fingerprint only, never contents)
    backup_manifest_id: str | None = None
    backup_manifest_sha256: str | None = None
    backup_verification_status: str | None = None
    restore_drill_status: str | None = None
    evidence_timestamp: str | None = None
    # Producer-owned execution facts (LHP-PROFILE-14A) — structured, digest-covered
    target_policy_snapshot: dict[str, Any] | None = None
    target_profile_sha256: str | None = None
    lighthouse_version: str | None = None
    repository_commit: str | None = None
    # Requirements a future Gateway approval must satisfy
    required_operator_approvals: list[str] = field(default_factory=list)
    required_backup_evidence: list[str] = field(default_factory=list)
    required_security_conditions: list[str] = field(default_factory=list)
    # Diagnostics
    warnings: list[str] = field(default_factory=list)
    blocking_reasons: list[str] = field(default_factory=list)
    # Integrity
    artifact_digest: str = ""
    digest_algorithm: str = DIGEST_ALGORITHM
    deterministic_serialization_version: int = DETERMINISTIC_SERIALIZATION_VERSION
    generator_version: str = GENERATOR_VERSION
    schema_version: int = PROFILE_TRANSITION_PREVIEW_SCHEMA_VERSION

    def validate(self) -> None:
        if not isinstance(self.schema_version, int) or isinstance(self.schema_version, bool):
            raise ProfileTransitionError("schema_version must be an integer")
        if self.schema_version < 1:
            raise ProfileTransitionError("schema_version must be >= 1")
        if self.schema_version > PROFILE_TRANSITION_PREVIEW_SCHEMA_VERSION:
            raise ProfileTransitionError(
                f"unsupported schema_version: {self.schema_version} "
                f"(max {PROFILE_TRANSITION_PREVIEW_SCHEMA_VERSION})"
            )
        dsv = self.deterministic_serialization_version
        if not isinstance(dsv, int) or isinstance(dsv, bool):
            raise ProfileTransitionError("deterministic_serialization_version must be an integer")
        if dsv < 1:
            raise ProfileTransitionError("deterministic_serialization_version must be >= 1")
        if dsv > DETERMINISTIC_SERIALIZATION_VERSION:
            raise ProfileTransitionError(
                f"unsupported deterministic_serialization_version: {dsv} "
                f"(max {DETERMINISTIC_SERIALIZATION_VERSION})"
            )
        if not self.preview_id:
            raise ProfileTransitionError("preview_id must be non-empty")
        if self.transition_status not in ALLOWED_TRANSITION_STATUSES:
            raise ProfileTransitionError(f"invalid transition_status: {self.transition_status}")
        if self.digest_algorithm != DIGEST_ALGORITHM:
            raise ProfileTransitionError(f"unsupported digest algorithm: {self.digest_algorithm}")
        if not self.target_profile_id:
            raise ProfileTransitionError("target_profile_id must be present")
        if self.target_policy_snapshot is not None:
            _validate_target_policy_snapshot(self.target_policy_snapshot)
        for label, value in (
            ("backup_manifest_sha256", self.backup_manifest_sha256),
            ("target_profile_sha256", self.target_profile_sha256),
        ):
            if value is not None and (not isinstance(value, str) or not _SHA256_HEX.match(value)):
                raise ProfileTransitionError(f"{label} must be 64 lowercase hex characters")
        for label, value in (
            ("lighthouse_version", self.lighthouse_version),
            ("repository_commit", self.repository_commit),
        ):
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise ProfileTransitionError(f"{label} must be a non-empty string or None")
        # A ready preview must never lack evidence — defense in depth against a hand-built dict.
        if self.transition_status == STATUS_READY and not (
            self.backup_manifest_id
            and self.backup_verification_status in VERIFICATION_PASS_STATUSES
            and self.restore_drill_status == RESTORE_DRILL_PASS_STATUS
        ):
            raise ProfileTransitionError(
                "ready_for_review requires present, passing backup evidence"
            )
        # LHP-PROFILE-14A strictening (unconditional — NOT gated on the self-attested
        # serialization version, which would be a permanent forgeable downgrade knob):
        # a ready preview must carry the producer-owned execution facts.
        if self.transition_status == STATUS_READY:
            if self.target_policy_snapshot is None:
                raise ProfileTransitionError(
                    "ready_for_review requires the typed target_policy_snapshot"
                )
            if not (self.backup_manifest_sha256 and self.target_profile_sha256):
                raise ProfileTransitionError(
                    "ready_for_review requires backup_manifest_sha256 and target_profile_sha256"
                )

    def _content(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "generator_version": self.generator_version,
            "deterministic_serialization_version": self.deterministic_serialization_version,
            "digest_algorithm": self.digest_algorithm,
            "current_profile_id": self.current_profile_id,
            "current_profile_version": self.current_profile_version,
            "target_profile_id": self.target_profile_id,
            "target_profile_version": self.target_profile_version,
            "transition_status": self.transition_status,
            "collectors_changed": self.collectors_changed,
            "sources_changed": self.sources_changed,
            "graph_entities_changed": self.graph_entities_changed,
            "correlation_policy_changed": self.correlation_policy_changed,
            "publishing_policy_changed": self.publishing_policy_changed,
            "privacy_policy_changed": self.privacy_policy_changed,
            "local_only_changed": self.local_only_changed,
            "dms_eligibility_changed": self.dms_eligibility_changed,
            "backup_manifest_id": self.backup_manifest_id,
            "backup_manifest_sha256": self.backup_manifest_sha256,
            "backup_verification_status": self.backup_verification_status,
            "restore_drill_status": self.restore_drill_status,
            "target_policy_snapshot": self.target_policy_snapshot,
            "target_profile_sha256": self.target_profile_sha256,
            "lighthouse_version": self.lighthouse_version,
            "repository_commit": self.repository_commit,
            "required_operator_approvals": self.required_operator_approvals,
            "required_backup_evidence": self.required_backup_evidence,
            "required_security_conditions": self.required_security_conditions,
            "warnings": self.warnings,
            "blocking_reasons": self.blocking_reasons,
        }

    def compute_digest(self) -> str:
        """Deterministic SHA-256 over canonical content, excluding the volatile fields
        (preview_id, created_at, evidence_timestamp) and the digest itself."""
        canonical = json.dumps(self._content(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "schema_version": self.schema_version,
            "preview_id": self.preview_id,
            "created_at": self.created_at,
            "generator_version": self.generator_version,
            "deterministic_serialization_version": self.deterministic_serialization_version,
            "current_profile_id": self.current_profile_id,
            "current_profile_version": self.current_profile_version,
            "target_profile_id": self.target_profile_id,
            "target_profile_version": self.target_profile_version,
            "transition_status": self.transition_status,
            "target_policy_snapshot": self.target_policy_snapshot,
            "target_profile_sha256": self.target_profile_sha256,
            "lighthouse_version": self.lighthouse_version,
            "repository_commit": self.repository_commit,
            "changes": {
                "collectors_changed": self.collectors_changed,
                "sources_changed": self.sources_changed,
                "graph_entities_changed": self.graph_entities_changed,
                "correlation_policy_changed": self.correlation_policy_changed,
                "publishing_policy_changed": self.publishing_policy_changed,
                "privacy_policy_changed": self.privacy_policy_changed,
                "local_only_changed": self.local_only_changed,
                "dms_eligibility_changed": self.dms_eligibility_changed,
            },
            "evidence": {
                "backup_manifest_id": self.backup_manifest_id,
                "backup_manifest_sha256": self.backup_manifest_sha256,
                "backup_verification_status": self.backup_verification_status,
                "restore_drill_status": self.restore_drill_status,
                "evidence_timestamp": self.evidence_timestamp,
            },
            "requirements": {
                "required_operator_approvals": self.required_operator_approvals,
                "required_backup_evidence": self.required_backup_evidence,
                "required_security_conditions": self.required_security_conditions,
            },
            "warnings": self.warnings,
            "blocking_reasons": self.blocking_reasons,
            "artifact_digest": self.artifact_digest,
            "digest_algorithm": self.digest_algorithm,
        }
        return payload

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ProfileTransitionPreview:
        changes = data.get("changes", {})
        evidence = data.get("evidence", {})
        requirements = data.get("requirements", {})
        return cls(
            preview_id=data.get("preview_id", ""),
            created_at=data.get("created_at", ""),
            current_profile_id=data.get("current_profile_id"),
            current_profile_version=data.get("current_profile_version"),
            target_profile_id=data.get("target_profile_id"),
            target_profile_version=data.get("target_profile_version"),
            transition_status=data.get("transition_status", STATUS_INVALID),
            collectors_changed=changes.get("collectors_changed") or {},
            sources_changed=changes.get("sources_changed") or {},
            graph_entities_changed=changes.get("graph_entities_changed") or {},
            correlation_policy_changed=changes.get("correlation_policy_changed") or {},
            publishing_policy_changed=changes.get("publishing_policy_changed") or {},
            privacy_policy_changed=changes.get("privacy_policy_changed") or {},
            local_only_changed=changes.get("local_only_changed") or {},
            dms_eligibility_changed=changes.get("dms_eligibility_changed") or {},
            backup_manifest_id=evidence.get("backup_manifest_id"),
            backup_manifest_sha256=evidence.get("backup_manifest_sha256"),
            backup_verification_status=evidence.get("backup_verification_status"),
            restore_drill_status=evidence.get("restore_drill_status"),
            evidence_timestamp=evidence.get("evidence_timestamp"),
            target_policy_snapshot=data.get("target_policy_snapshot"),
            target_profile_sha256=data.get("target_profile_sha256"),
            lighthouse_version=data.get("lighthouse_version"),
            repository_commit=data.get("repository_commit"),
            required_operator_approvals=list(requirements.get("required_operator_approvals", [])),
            required_backup_evidence=list(requirements.get("required_backup_evidence", [])),
            required_security_conditions=list(requirements.get("required_security_conditions", [])),
            warnings=list(data.get("warnings", [])),
            blocking_reasons=list(data.get("blocking_reasons", [])),
            artifact_digest=data.get("artifact_digest", ""),
            digest_algorithm=data.get("digest_algorithm", DIGEST_ALGORITHM),
            deterministic_serialization_version=data.get(
                "deterministic_serialization_version", DETERMINISTIC_SERIALIZATION_VERSION
            ),
            generator_version=data.get("generator_version", GENERATOR_VERSION),
            schema_version=data.get("schema_version", PROFILE_TRANSITION_PREVIEW_SCHEMA_VERSION),
        )


# -- requirement derivation --------------------------------------------------------------


def _derive_required_approvals(
    diff: dict[str, dict[str, Any]], target: IntelligenceProfile
) -> list[str]:
    approvals = ["operator approval: activate the target profile (exactly-one-active invariant)"]
    if diff["local_only"]["changed"] and diff["local_only"]["to"] is False:
        approvals.append("operator approval: privacy downgrade (local_only True -> False)")
    if diff["privacy"]["changed"]:
        approvals.append("operator approval: privacy_policy change")
    if diff["publishing"]["changed"]:
        approvals.append("operator approval: publishing_policy change")
    if bool(target.publishing_policy.get("public_publishing")):
        approvals.append("operator approval: enable public publishing")
    if diff["dms_eligibility"]["changed"]:
        approvals.append("operator approval: dead-man's-switch eligibility change")
    if diff["sources"]["changed"]:
        approvals.append("operator approval: approved-source set change")
    if diff["collectors"]["changed"]:
        approvals.append("operator approval: enabled-collector set change")
    return approvals


def _required_backup_evidence() -> list[str]:
    return [
        "a verified backup manifest of the target profile data_root",
        "a passing independent backup verification",
        "a completed isolated restore drill",
    ]


def _required_security_conditions(target: IntelligenceProfile) -> list[str]:
    conditions = [
        "exactly one active profile invariant preserved (ADR-0003)",
        "backup evidence present and passing before any activation",
        "operator explicitly approves at the Gateway (approval authority is external)",
    ]
    data_root = target.backup_policy.get("data_root")
    if data_root:
        conditions.append(f"target data_root resolves under /data ({data_root})")
    return conditions


# -- build -------------------------------------------------------------------------------


def _default_preview_id() -> str:
    import uuid

    return f"lhtransition-{uuid.uuid4().hex[:16]}"


def _now_iso() -> str:
    from datetime import UTC, datetime

    return datetime.now(tz=UTC).isoformat()


def _assess_status(
    profiles_valid: bool,
    evidence: TransitionEvidence,
    blocking_reasons: list[str],
) -> str:
    if not profiles_valid:
        return STATUS_INVALID
    if not evidence.is_complete():
        blocking_reasons.append(
            "backup evidence incomplete: manifest id, manifest content digest, backup "
            "verification, and restore drill must all be present"
        )
        return STATUS_INCOMPLETE
    if not evidence.is_passing():
        blocking_reasons.append(
            "backup evidence present but not passing: verification must be one of "
            f"{sorted(VERIFICATION_PASS_STATUSES)} and restore drill "
            f"'{RESTORE_DRILL_PASS_STATUS}'"
        )
        return STATUS_BLOCKED
    return STATUS_READY


def build_transition_preview(
    target_profile: IntelligenceProfile,
    current_profile: IntelligenceProfile | None = None,
    evidence: TransitionEvidence | None = None,
    preview_id: str | None = None,
    created_at: str | None = None,
) -> ProfileTransitionPreview:
    """Build a deterministic, fail-closed transition preview. Performs no activation.

    The returned artifact is `ready_for_review` only when both profiles validate, required
    policy information is present, and backup evidence is present and passing. It never
    activates, switches, writes state, or contacts the Gateway.
    """
    evidence = evidence or TransitionEvidence()
    warnings: list[str] = []
    blocking_reasons: list[str] = []

    # 1. Profile validity (fail closed to `invalid`) ----------------------------------------
    profiles_valid = True
    try:
        target_profile.validate()
    except Exception as exc:  # noqa: BLE001 - any validation failure => invalid, no disclosure
        profiles_valid = False
        blocking_reasons.append(f"target profile invalid: {exc}")
    if current_profile is not None:
        try:
            current_profile.validate()
        except Exception as exc:  # noqa: BLE001
            profiles_valid = False
            blocking_reasons.append(f"current profile invalid: {exc}")

    # 2. Deterministic diff (safe even when current is None) --------------------------------
    diff = compare_profiles(current_profile if profiles_valid else None, target_profile)

    if (
        current_profile is not None
        and current_profile.profile_id == target_profile.profile_id
        and current_profile.profile_version == target_profile.profile_version
    ):
        warnings.append("current and target profiles are identical; no transition is needed")

    if evidence.backup_verification_status == "passed":
        warnings.append(
            "verification report status is 'passed' (backup-create embed); "
            "profile-activate requires lighthouse backup-verify output with status 'verified'"
        )

    # 3. Required policy information present? ------------------------------------------------
    if profiles_valid:
        backup_required = target_profile.backup_policy.get("backup_required_on_activation")
        if not isinstance(backup_required, bool):
            warnings.append("target backup_policy.backup_required_on_activation is not declared")
        if backup_required and not target_profile.backup_policy.get("data_root"):
            blocking_reasons.append(
                "required policy information missing: target requires backup but declares no "
                "backup_policy.data_root"
            )

    # 4. Fail-closed assessment -------------------------------------------------------------
    if any(r.startswith("required policy information missing") for r in blocking_reasons):
        status = STATUS_INVALID if not profiles_valid else STATUS_INCOMPLETE
    else:
        status = _assess_status(profiles_valid, evidence, blocking_reasons)

    # 5. Producer-owned execution facts — derived ONLY from validated profiles (else None,
    # and the READY validator rule makes an artifact without them unable to be ready)
    target_policy_snapshot: dict[str, Any] | None = None
    target_profile_sha256: str | None = None
    if profiles_valid:
        target_policy_snapshot = _target_policy_snapshot(target_profile)
        target_profile_sha256 = profile_content_sha256(target_profile)

    preview = ProfileTransitionPreview(
        preview_id=preview_id or _default_preview_id(),
        created_at=created_at or _now_iso(),
        current_profile_id=current_profile.profile_id if current_profile is not None else None,
        current_profile_version=(
            current_profile.profile_version if current_profile is not None else None
        ),
        target_profile_id=target_profile.profile_id or None,
        target_profile_version=target_profile.profile_version,
        transition_status=status,
        collectors_changed=diff["collectors"],
        sources_changed=diff["sources"],
        graph_entities_changed=diff["graph_entities"],
        correlation_policy_changed=diff["correlation"],
        publishing_policy_changed=diff["publishing"],
        privacy_policy_changed=diff["privacy"],
        local_only_changed=diff["local_only"],
        dms_eligibility_changed=diff["dms_eligibility"],
        backup_manifest_id=evidence.backup_manifest_id,
        backup_manifest_sha256=evidence.backup_manifest_sha256,
        backup_verification_status=evidence.backup_verification_status,
        restore_drill_status=evidence.restore_drill_status,
        evidence_timestamp=evidence.evidence_timestamp,
        target_policy_snapshot=target_policy_snapshot,
        target_profile_sha256=target_profile_sha256,
        lighthouse_version=detect_lighthouse_version(),
        repository_commit=detect_repository_commit(),
        required_operator_approvals=_derive_required_approvals(diff, target_profile),
        required_backup_evidence=_required_backup_evidence(),
        required_security_conditions=_required_security_conditions(target_profile),
        warnings=warnings,
        blocking_reasons=blocking_reasons,
    )
    preview.artifact_digest = preview.compute_digest()
    return preview


def generate_transition_preview_from_registry(
    target_profile_id: str,
    current_profile_id: str | None = None,
    manifest_path: str | Path | None = None,
    verification_report_path: str | Path | None = None,
    restore_report_path: str | Path | None = None,
    directory: str | Path | None = None,
    preview_id: str | None = None,
    created_at: str | None = None,
) -> ProfileTransitionPreview:
    """CLI-facing: resolve profiles from the bundled registry, load evidence, build a preview.

    Raises `ProfileTransitionError` if the target (or a named current) profile is not found —
    an unavailable profile can never be previewed as ready. Reads only; activates nothing.
    """
    from modules.profile_registry import get_profile

    try:
        target = get_profile(target_profile_id, directory=directory)
    except KeyError as exc:
        raise ProfileTransitionError(f"target profile not available: {target_profile_id}") from exc

    current = None
    if current_profile_id:
        try:
            current = get_profile(current_profile_id, directory=directory)
        except KeyError as exc:
            raise ProfileTransitionError(
                f"current profile not available: {current_profile_id}"
            ) from exc

    evidence = load_evidence(
        manifest_path=manifest_path,
        verification_report_path=verification_report_path,
        restore_report_path=restore_report_path,
    )
    return build_transition_preview(
        target_profile=target,
        current_profile=current,
        evidence=evidence,
        preview_id=preview_id,
        created_at=created_at,
    )
