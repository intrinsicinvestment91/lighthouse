"""Active-profile state contract — ``active_profile_state.v1`` (LHP-PROFILE-14B/01E).

Defines WHAT an active profile is: the exact, versioned record shape
``lighthouse.active_profile_store`` persists (Gateway capability definition: outputs
``active_profile_record``, postcondition ``exactly_one_active_profile``, failure semantics
``fail_closed_ambiguous_state_blocks``). Construction and validation live here; persistence
and the local activation ceremony live in ``active_profile_store`` /
``profile_activation`` (LHP-V1-PROFILE-01C/01E). This module never switches profiles and never
contacts the Gateway.

Contract rules (fail closed):

- Absence is EXPLICIT, never inferred: the ``activation_state`` discriminator must say
  ``"no_active_profile"``; ``no_active_profile_state()`` is the only zero-argument
  constructor, and ``from_dict({})`` raises rather than defaulting to absence.
- No half states: an ``"active"`` record carries all five identity facts
  (id, version, activated_at, activation_source, active_profile_sha256) valid and
  present, plus every activation-evidence reference (LHP-V1-PROFILE-01E); a
  ``"no_active_profile"`` record carries all identity and evidence fields as exactly
  ``None``.
- ``activation_source`` is a closed vocabulary. Members: ``operator_approved_transition``
  (future Gateway-authorized path) and ``operator_local_cli_confirmation`` (local initial
  activation via typed CLI confirmation, LHP-V1-PROFILE-01C). Extending the vocabulary is a
  deliberate contract change, never a runtime convenience.
- ``artifact_digest`` (SHA-256 over the canonical record content) is required on every
  record — including the absent state — and ``validate()`` recomputes it, so flipping
  ``no_active_profile`` to ``active`` in stored bytes is detectable. Unlike the
  transition preview (whose digest the Gateway trusts and never recomputes, a boundary
  rule), Lighthouse owns this record end to end, so recompute-in-validate is correct.

Field consumers (packet design rule — no consumer, no field): ``activation_state`` +
``active_profile_id``/``active_profile_version`` → the future activation packet's store
and the execution plan's ``verify_activation_outcome`` step (``active_equals_target``,
``exactly_one_active_profile``); ``activated_at``/``activation_source`` → the audit
system; ``active_profile_sha256`` → future post-switch validation (compare against the
authorized ``target_profile_sha256`` to detect in-place profile drift);
``artifact_digest`` → store read-back integrity.
"""

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from modules.profile_transition import profile_content_sha256
from modules.profiles import PROFILE_ID_RE, IntelligenceProfile

ACTIVE_PROFILE_STATE_SCHEMA_VERSION = 1

STATE_ACTIVE = "active"
STATE_NO_ACTIVE_PROFILE = "no_active_profile"
ALLOWED_ACTIVATION_STATES = frozenset({STATE_ACTIVE, STATE_NO_ACTIVE_PROFILE})

# Closed activation-source vocabulary.
# - operator_approved_transition: future Gateway-authorized remote/fleet path (unused today).
# - operator_local_cli_confirmation: local initial activation via explicit typed CLI confirmation
#   (LHP-V1-PROFILE-01C). Extending this set is a deliberate contract change.
ACTIVATION_SOURCE_GATEWAY_TRANSITION = "operator_approved_transition"
ACTIVATION_SOURCE_LOCAL_CLI = "operator_local_cli_confirmation"
ALLOWED_ACTIVATION_SOURCES = frozenset(
    {ACTIVATION_SOURCE_GATEWAY_TRANSITION, ACTIVATION_SOURCE_LOCAL_CLI}
)

_SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")

# The five identity facts that are all-present ("active") or all-None ("no_active_profile").
_IDENTITY_FIELDS = (
    "active_profile_id",
    "active_profile_version",
    "activated_at",
    "activation_source",
    "active_profile_sha256",
)

# Digest-covered activation evidence (LHP-V1-PROFILE-01E). Required for ``active``; all ``None``
# for ``no_active_profile``.
_EVIDENCE_FIELDS = (
    "transition_preview_id",
    "transition_preview_digest",
    "backup_manifest_id",
    "backup_manifest_sha256",
    "verification_report_digest",
    "restore_report_digest",
    "source_data_root",
    "backup_root",
)

_EXPECTED_KEYS = frozenset(
    ("schema_version", "activation_state", "artifact_digest") + _IDENTITY_FIELDS + _EVIDENCE_FIELDS
)


class ActiveProfileStateError(Exception):
    """Raised when an active-profile state record is malformed, inconsistent, or tampered."""


@dataclass(frozen=True)
class ActivationEvidenceReferences:
    """Digest-covered evidence bindings for an ``active`` record (LHP-V1-PROFILE-01E)."""

    transition_preview_id: str
    transition_preview_digest: str
    backup_manifest_id: str
    backup_manifest_sha256: str
    verification_report_digest: str
    restore_report_digest: str
    source_data_root: str
    backup_root: str


@dataclass
class ActiveProfileState:
    """One ``active_profile_state.v1`` record. See the module docstring for the rules."""

    activation_state: str
    active_profile_id: str | None = None
    active_profile_version: int | None = None
    activated_at: str | None = None
    activation_source: str | None = None
    active_profile_sha256: str | None = None
    transition_preview_id: str | None = None
    transition_preview_digest: str | None = None
    backup_manifest_id: str | None = None
    backup_manifest_sha256: str | None = None
    verification_report_digest: str | None = None
    restore_report_digest: str | None = None
    source_data_root: str | None = None
    backup_root: str | None = None
    schema_version: int = ACTIVE_PROFILE_STATE_SCHEMA_VERSION
    artifact_digest: str = ""

    def _content(self) -> dict[str, Any]:
        """Digest-covered content: every fact; excludes only ``artifact_digest`` itself.

        ``activated_at`` and ``activation_source`` are facts of the activation, not
        volatile metadata, so they are covered (contrast the preview's ``created_at``).
        """
        return {
            "schema_version": self.schema_version,
            "activation_state": self.activation_state,
            "active_profile_id": self.active_profile_id,
            "active_profile_version": self.active_profile_version,
            "activated_at": self.activated_at,
            "activation_source": self.activation_source,
            "active_profile_sha256": self.active_profile_sha256,
            "transition_preview_id": self.transition_preview_id,
            "transition_preview_digest": self.transition_preview_digest,
            "backup_manifest_id": self.backup_manifest_id,
            "backup_manifest_sha256": self.backup_manifest_sha256,
            "verification_report_digest": self.verification_report_digest,
            "restore_report_digest": self.restore_report_digest,
            "source_data_root": self.source_data_root,
            "backup_root": self.backup_root,
        }

    def compute_digest(self) -> str:
        canonical = json.dumps(self._content(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def validate(self) -> None:
        if isinstance(self.schema_version, bool) or not isinstance(self.schema_version, int):
            raise ActiveProfileStateError("schema_version must be an integer")
        if self.schema_version != ACTIVE_PROFILE_STATE_SCHEMA_VERSION:
            raise ActiveProfileStateError(
                f"unsupported schema_version: {self.schema_version!r} "
                f"(this build understands {ACTIVE_PROFILE_STATE_SCHEMA_VERSION})"
            )
        if self.activation_state not in ALLOWED_ACTIVATION_STATES:
            raise ActiveProfileStateError(
                f"activation_state must be one of {sorted(ALLOWED_ACTIVATION_STATES)}, "
                f"got {self.activation_state!r}"
            )

        if self.activation_state == STATE_NO_ACTIVE_PROFILE:
            for field_name in _IDENTITY_FIELDS + _EVIDENCE_FIELDS:
                if getattr(self, field_name) is not None:
                    raise ActiveProfileStateError(
                        f"no_active_profile state must carry {field_name}=None, "
                        f"got {getattr(self, field_name)!r}"
                    )
        else:
            self._validate_active_identity()
            self._validate_active_evidence()

        if not isinstance(self.artifact_digest, str) or not _SHA256_HEX.match(self.artifact_digest):
            raise ActiveProfileStateError("artifact_digest must be a 64-char lowercase hex SHA-256")
        if self.artifact_digest != self.compute_digest():
            raise ActiveProfileStateError(
                "artifact_digest mismatch — record content does not match its digest"
            )

    def _validate_active_identity(self) -> None:
        if not isinstance(self.active_profile_id, str) or not PROFILE_ID_RE.match(
            self.active_profile_id
        ):
            raise ActiveProfileStateError(
                f"active state requires a valid active_profile_id, got {self.active_profile_id!r}"
            )
        if (
            isinstance(self.active_profile_version, bool)
            or not isinstance(self.active_profile_version, int)
            or self.active_profile_version < 1
        ):
            raise ActiveProfileStateError(
                "active state requires active_profile_version as an integer >= 1"
            )
        if not isinstance(self.activated_at, str):
            raise ActiveProfileStateError("active state requires an activated_at timestamp")
        try:
            parsed = datetime.fromisoformat(self.activated_at)
        except ValueError as exc:
            raise ActiveProfileStateError(
                f"activated_at is not ISO-8601: {self.activated_at!r}"
            ) from exc
        if parsed.tzinfo is None:
            raise ActiveProfileStateError("activated_at must be timezone-aware")
        if self.activation_source not in ALLOWED_ACTIVATION_SOURCES:
            raise ActiveProfileStateError(
                f"activation_source must be one of {sorted(ALLOWED_ACTIVATION_SOURCES)}, "
                f"got {self.activation_source!r}"
            )
        if not isinstance(self.active_profile_sha256, str) or not _SHA256_HEX.match(
            self.active_profile_sha256
        ):
            raise ActiveProfileStateError(
                "active state requires active_profile_sha256 as a 64-char lowercase hex SHA-256"
            )

    def _validate_active_evidence(self) -> None:
        for field_name in _EVIDENCE_FIELDS:
            value = getattr(self, field_name)
            if not isinstance(value, str) or not value:
                raise ActiveProfileStateError(
                    f"active state requires a non-empty {field_name}, got {value!r}"
                )
        for digest_field in (
            "transition_preview_digest",
            "backup_manifest_sha256",
            "verification_report_digest",
            "restore_report_digest",
        ):
            digest = getattr(self, digest_field)
            if not _SHA256_HEX.match(digest):
                raise ActiveProfileStateError(
                    f"{digest_field} must be a 64-char lowercase hex SHA-256"
                )

    def to_dict(self) -> dict[str, Any]:
        """Deterministic serialization. Validates first — an invalid record never serializes."""
        self.validate()
        content = self._content()
        content["artifact_digest"] = self.artifact_digest
        return content

    @classmethod
    def from_dict(cls, data: Any) -> "ActiveProfileState":
        """Strict, fail-closed deserialization.

        Missing keys and unexpected keys both raise — absence of a record is expressed
        by an explicit ``no_active_profile`` record, never by an empty or partial dict.
        """
        if not isinstance(data, dict):
            raise ActiveProfileStateError("active-profile state must be an object")
        keys = set(data)
        missing = _EXPECTED_KEYS - keys
        if missing:
            raise ActiveProfileStateError(
                f"active-profile state is missing required keys: {sorted(missing)}"
            )
        unexpected = keys - _EXPECTED_KEYS
        if unexpected:
            raise ActiveProfileStateError(
                f"active-profile state carries unexpected keys: {sorted(unexpected)}"
            )
        state = cls(
            activation_state=data["activation_state"],
            active_profile_id=data["active_profile_id"],
            active_profile_version=data["active_profile_version"],
            activated_at=data["activated_at"],
            activation_source=data["activation_source"],
            active_profile_sha256=data["active_profile_sha256"],
            transition_preview_id=data["transition_preview_id"],
            transition_preview_digest=data["transition_preview_digest"],
            backup_manifest_id=data["backup_manifest_id"],
            backup_manifest_sha256=data["backup_manifest_sha256"],
            verification_report_digest=data["verification_report_digest"],
            restore_report_digest=data["restore_report_digest"],
            source_data_root=data["source_data_root"],
            backup_root=data["backup_root"],
            schema_version=data["schema_version"],
            artifact_digest=data["artifact_digest"],
        )
        state.validate()
        return state


def no_active_profile_state() -> ActiveProfileState:
    """The explicit "nothing is active" record — the only truthful state today.

    This is a constructed declaration, not a default: deserialization never infers it.
    """
    state = ActiveProfileState(activation_state=STATE_NO_ACTIVE_PROFILE)
    state.artifact_digest = state.compute_digest()
    state.validate()
    return state


def build_active_profile_state(
    profile: IntelligenceProfile,
    *,
    activated_at: str,
    activation_source: str,
    evidence: ActivationEvidenceReferences,
) -> ActiveProfileState:
    """Pure constructor for an ``active`` record describing ``profile``.

    Derives the identity facts (id, version, content digest) from the validated profile
    itself, so a record can never be fabricated inconsistently through this factory.
    ``evidence`` binds the activation ceremony artifacts for later audit. Nothing is written
    anywhere; nothing is activated — this builds the record the activation service persists.
    """
    try:
        content_sha = profile_content_sha256(profile)
    except ValueError as exc:
        raise ActiveProfileStateError(f"profile does not validate: {exc}") from exc
    state = ActiveProfileState(
        activation_state=STATE_ACTIVE,
        active_profile_id=profile.profile_id,
        active_profile_version=profile.profile_version,
        activated_at=activated_at,
        activation_source=activation_source,
        active_profile_sha256=content_sha,
        transition_preview_id=evidence.transition_preview_id,
        transition_preview_digest=evidence.transition_preview_digest,
        backup_manifest_id=evidence.backup_manifest_id,
        backup_manifest_sha256=evidence.backup_manifest_sha256,
        verification_report_digest=evidence.verification_report_digest,
        restore_report_digest=evidence.restore_report_digest,
        source_data_root=evidence.source_data_root,
        backup_root=evidence.backup_root,
    )
    state.artifact_digest = state.compute_digest()
    state.validate()
    return state
