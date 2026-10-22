"""Activation-intent artifact — ``activation_intent.v1`` (LHP-PROFILE-14B).

A deterministic, digest-bearing record of "I intend to move from profile A to profile B."
It is the bridge object between the transition preview (LHP-PROFILE-03/14A) and a future
activation capability — the natural payload for the activation endpoint's
``transition_reference`` input. It is NOT an approval (approval authority is
Gateway-owned), NOT an authorization, and NOT execution: ``authorizes_activation`` is a
digest-covered constant ``False`` and ``validate()`` rejects anything else, so there is
no path where an approved-looking intent becomes an activated profile.

The intent is an HONEST artifact: ``build_activation_intent()`` runs the
activation-preconditions evaluator and embeds its true result (the packet's "validation
result"), so a blocked or invalid transition is representable — recorded, never
executable. ``validate()`` strictens ``ready``: a ready intent must carry every
reference (target content digest, preview binding, backup-manifest id + content
fingerprint), mirroring the preview's READY-strictening.

References are bindings, never copies: ``preview_id`` + ``preview_digest`` bind the
reviewed preview and, transitively, every digest-covered producer fact inside it;
``backup_manifest_id`` + ``backup_manifest_sha256`` bind the evidence;
``target_profile_sha256`` binds the target's full canonical content. The declared
current state comes from an explicit, valid ``ActiveProfileState`` — an intent cannot be
built from an undeclarable current state (the evaluator merely *answers questions* about
garbage inputs; the builder *declares intent* and therefore demands a valid declaration).

Idempotency semantics (ADR-0016, documented only — no ledger, no lock here): two
activation attempts are the same intended transition iff their ``artifact_digest``
values are equal. ``intent_id`` and ``created_at`` are handles, digest-EXCLUDED; every
other field is digest-covered. Duplicate prevention belongs to the future execution
ledger, transition lock, and the endpoint's single-request semantics — none of which
exist in this packet.
"""

import hashlib
import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from modules.activation_preconditions import (
    ALLOWED_PRECONDITION_DECISIONS,
    DECISION_READY,
    evaluate_activation_preconditions,
)
from modules.active_profile_state import ActiveProfileState, ActiveProfileStateError
from modules.profile_registry import get_profile
from modules.profiles import PROFILE_ID_RE, IntelligenceProfile

ACTIVATION_INTENT_SCHEMA_VERSION = 1

_SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")

_EXPECTED_KEYS = frozenset(
    {
        "schema_version",
        "intent_id",
        "created_at",
        "current_profile_id",
        "current_profile_version",
        "target_profile_id",
        "target_profile_version",
        "target_profile_sha256",
        "preview_id",
        "preview_digest",
        "backup_manifest_id",
        "backup_manifest_sha256",
        "preconditions_decision",
        "preconditions_reasons",
        "authorizes_activation",
        "artifact_digest",
    }
)


class ActivationIntentError(Exception):
    """Raised when an activation intent is malformed, inconsistent, or tampered."""


def _default_intent_id() -> str:
    return f"lhintent-{uuid.uuid4().hex[:16]}"


def _now_iso() -> str:
    return datetime.now(tz=UTC).isoformat()


def _ref_str(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _ref_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        return None
    return value


def _ref_sha(value: Any) -> str | None:
    return value if isinstance(value, str) and _SHA256_HEX.match(value) else None


@dataclass
class ActivationIntent:
    """One ``activation_intent.v1`` artifact. See the module docstring for the rules."""

    intent_id: str
    created_at: str
    target_profile_id: str
    target_profile_version: int
    current_profile_id: str | None = None
    current_profile_version: int | None = None
    target_profile_sha256: str | None = None
    preview_id: str | None = None
    preview_digest: str | None = None
    backup_manifest_id: str | None = None
    backup_manifest_sha256: str | None = None
    preconditions_decision: str = "invalid"
    preconditions_reasons: tuple[str, ...] = field(default_factory=tuple)
    authorizes_activation: bool = False
    schema_version: int = ACTIVATION_INTENT_SCHEMA_VERSION
    artifact_digest: str = ""

    def _content(self) -> dict[str, Any]:
        """Digest-covered content — everything except the volatile handles.

        Excluded, exactly: ``intent_id``, ``created_at``, ``artifact_digest`` (itself).
        The digest over this content is the producer-side idempotency identity.
        """
        return {
            "schema_version": self.schema_version,
            "current_profile_id": self.current_profile_id,
            "current_profile_version": self.current_profile_version,
            "target_profile_id": self.target_profile_id,
            "target_profile_version": self.target_profile_version,
            "target_profile_sha256": self.target_profile_sha256,
            "preview_id": self.preview_id,
            "preview_digest": self.preview_digest,
            "backup_manifest_id": self.backup_manifest_id,
            "backup_manifest_sha256": self.backup_manifest_sha256,
            "preconditions_decision": self.preconditions_decision,
            "preconditions_reasons": list(self.preconditions_reasons),
            "authorizes_activation": self.authorizes_activation,
        }

    def compute_digest(self) -> str:
        canonical = json.dumps(self._content(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def validate(self) -> None:  # noqa: PLR0912 — one flat fail-closed rule list
        if isinstance(self.schema_version, bool) or not isinstance(self.schema_version, int):
            raise ActivationIntentError("schema_version must be an integer")
        if self.schema_version != ACTIVATION_INTENT_SCHEMA_VERSION:
            raise ActivationIntentError(
                f"unsupported schema_version: {self.schema_version!r} "
                f"(this build understands {ACTIVATION_INTENT_SCHEMA_VERSION})"
            )
        if not isinstance(self.intent_id, str) or not self.intent_id:
            raise ActivationIntentError("intent_id must be a non-empty string")
        if not isinstance(self.created_at, str):
            raise ActivationIntentError("created_at must be an ISO-8601 string")
        try:
            parsed = datetime.fromisoformat(self.created_at)
        except ValueError as exc:
            raise ActivationIntentError(f"created_at is not ISO-8601: {self.created_at!r}") from exc
        if parsed.tzinfo is None:
            raise ActivationIntentError("created_at must be timezone-aware")

        # The anti-confusion pin: an intent NEVER authorizes anything. Identity check —
        # truthy stand-ins (1, "false", None) are all rejected, not coerced.
        if self.authorizes_activation is not False:
            raise ActivationIntentError(
                "authorizes_activation must be exactly False — an intent is never an authorization"
            )

        if not isinstance(self.target_profile_id, str) or not PROFILE_ID_RE.match(
            self.target_profile_id
        ):
            raise ActivationIntentError(
                f"target_profile_id must be a valid profile id, got {self.target_profile_id!r}"
            )
        if (
            isinstance(self.target_profile_version, bool)
            or not isinstance(self.target_profile_version, int)
            or self.target_profile_version < 1
        ):
            raise ActivationIntentError("target_profile_version must be an integer >= 1")

        current_pair = (self.current_profile_id, self.current_profile_version)
        if current_pair != (None, None):
            if not isinstance(self.current_profile_id, str) or not PROFILE_ID_RE.match(
                self.current_profile_id
            ):
                raise ActivationIntentError(
                    "current profile reference must be a complete valid pair or both None "
                    "(explicit foundation) — half declarations are rejected"
                )
            if (
                isinstance(self.current_profile_version, bool)
                or not isinstance(self.current_profile_version, int)
                or self.current_profile_version < 1
            ):
                raise ActivationIntentError(
                    "current profile reference must be a complete valid pair or both None "
                    "(explicit foundation) — half declarations are rejected"
                )

        for name in ("target_profile_sha256", "preview_digest", "backup_manifest_sha256"):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, str) or not _SHA256_HEX.match(value)):
                raise ActivationIntentError(
                    f"{name} must be a 64-char lowercase hex SHA-256 when present"
                )
        for name in ("preview_id", "backup_manifest_id"):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, str) or not value):
                raise ActivationIntentError(f"{name} must be a non-empty string when present")

        if self.preconditions_decision not in ALLOWED_PRECONDITION_DECISIONS:
            raise ActivationIntentError(
                f"preconditions_decision must be one of "
                f"{sorted(ALLOWED_PRECONDITION_DECISIONS)}, got "
                f"{self.preconditions_decision!r}"
            )
        if not isinstance(self.preconditions_reasons, tuple) or any(
            not isinstance(reason, str) or not reason for reason in self.preconditions_reasons
        ):
            raise ActivationIntentError(
                "preconditions_reasons must be a tuple of non-empty strings"
            )
        if self.preconditions_decision == DECISION_READY:
            if self.preconditions_reasons:
                raise ActivationIntentError("a ready intent must carry no blocking reasons")
            missing = [
                name
                for name in (
                    "target_profile_sha256",
                    "preview_id",
                    "preview_digest",
                    "backup_manifest_id",
                    "backup_manifest_sha256",
                )
                if getattr(self, name) is None
            ]
            if missing:
                raise ActivationIntentError(
                    f"a ready intent requires every reference; missing: {missing}"
                )
        elif not self.preconditions_reasons:
            raise ActivationIntentError(
                "a non-ready intent must state at least one reason (fail-closed honesty)"
            )

        if not isinstance(self.artifact_digest, str) or not _SHA256_HEX.match(self.artifact_digest):
            raise ActivationIntentError("artifact_digest must be a 64-char lowercase hex SHA-256")
        if self.artifact_digest != self.compute_digest():
            raise ActivationIntentError(
                "artifact_digest mismatch — intent content does not match its digest"
            )

    def to_dict(self) -> dict[str, Any]:
        """Deterministic serialization. Validates first — an invalid intent never serializes."""
        self.validate()
        data = self._content()
        data["preconditions_reasons"] = list(self.preconditions_reasons)
        data["intent_id"] = self.intent_id
        data["created_at"] = self.created_at
        data["artifact_digest"] = self.artifact_digest
        return data

    @classmethod
    def from_dict(cls, data: Any) -> "ActivationIntent":
        """Strict, fail-closed deserialization: missing and unexpected keys both raise."""
        if not isinstance(data, dict):
            raise ActivationIntentError("activation intent must be an object")
        keys = set(data)
        missing = _EXPECTED_KEYS - keys
        if missing:
            raise ActivationIntentError(
                f"activation intent is missing required keys: {sorted(missing)}"
            )
        unexpected = keys - _EXPECTED_KEYS
        if unexpected:
            raise ActivationIntentError(
                f"activation intent carries unexpected keys: {sorted(unexpected)}"
            )
        reasons = data["preconditions_reasons"]
        if not isinstance(reasons, list):
            raise ActivationIntentError("preconditions_reasons must be a list")
        intent = cls(
            intent_id=data["intent_id"],
            created_at=data["created_at"],
            target_profile_id=data["target_profile_id"],
            target_profile_version=data["target_profile_version"],
            current_profile_id=data["current_profile_id"],
            current_profile_version=data["current_profile_version"],
            target_profile_sha256=data["target_profile_sha256"],
            preview_id=data["preview_id"],
            preview_digest=data["preview_digest"],
            backup_manifest_id=data["backup_manifest_id"],
            backup_manifest_sha256=data["backup_manifest_sha256"],
            preconditions_decision=data["preconditions_decision"],
            preconditions_reasons=tuple(reasons),
            authorizes_activation=data["authorizes_activation"],
            schema_version=data["schema_version"],
            artifact_digest=data["artifact_digest"],
        )
        intent.validate()
        return intent


def build_activation_intent(
    preview: dict[str, Any],
    *,
    current_state: ActiveProfileState,
    target_profile: IntelligenceProfile | None,
    intent_id: str | None = None,
    created_at: str | None = None,
) -> ActivationIntent:
    """Build an honest intent artifact from a preview + an explicit, valid current state.

    Runs the activation-preconditions evaluator and embeds its true result; a blocked or
    invalid transition still yields a valid (non-ready) artifact. Raises only when no
    truthful intent can exist at all: the current state does not validate (nothing can be
    declared "from"), or the preview does not even name a target ("to" is unrecoverable).
    Nothing is written anywhere; nothing is activated.
    """
    if not isinstance(current_state, ActiveProfileState):
        raise ActivationIntentError("current_state must be an ActiveProfileState")
    try:
        current_state.validate()
    except ActiveProfileStateError as exc:
        raise ActivationIntentError(
            f"cannot declare an intent from an invalid current state: {exc}"
        ) from exc

    if not isinstance(preview, dict):
        raise ActivationIntentError("preview must be an object")
    target_id = _ref_str(preview.get("target_profile_id"))
    target_version = _ref_int(preview.get("target_profile_version"))
    if target_id is None or target_version is None:
        raise ActivationIntentError("preview does not identify a target profile")

    result = evaluate_activation_preconditions(
        preview, current_state=current_state, target_profile=target_profile
    )

    evidence = preview.get("evidence")
    evidence = evidence if isinstance(evidence, dict) else {}
    intent = ActivationIntent(
        intent_id=intent_id or _default_intent_id(),
        created_at=created_at or _now_iso(),
        target_profile_id=target_id,
        target_profile_version=target_version,
        current_profile_id=current_state.active_profile_id,
        current_profile_version=current_state.active_profile_version,
        target_profile_sha256=_ref_sha(preview.get("target_profile_sha256")),
        preview_id=_ref_str(preview.get("preview_id")),
        preview_digest=_ref_sha(preview.get("artifact_digest")),
        backup_manifest_id=_ref_str(evidence.get("backup_manifest_id")),
        backup_manifest_sha256=_ref_sha(evidence.get("backup_manifest_sha256")),
        preconditions_decision=result.decision,
        preconditions_reasons=result.reasons,
    )
    intent.artifact_digest = intent.compute_digest()
    intent.validate()
    return intent


def generate_activation_intent_from_registry(
    preview: dict[str, Any],
    *,
    current_state: ActiveProfileState,
    directory: str | None = None,
    intent_id: str | None = None,
    created_at: str | None = None,
) -> ActivationIntent:
    """Convenience wrapper: resolve the target from the bundled registry (read-only), then build.

    An unresolvable target fails closed to ``None`` — the built intent honestly records
    ``target_profile_missing`` (blocked) rather than raising.
    """
    target: IntelligenceProfile | None = None
    if isinstance(preview, dict):
        target_id = preview.get("target_profile_id")
        target_version = preview.get("target_profile_version")
        if isinstance(target_id, str) and target_id:
            try:
                target = get_profile(
                    target_id, version=_ref_int(target_version), directory=directory
                )
            except Exception:  # noqa: BLE001 — fail closed: unresolvable target is missing
                target = None
    return build_activation_intent(
        preview,
        current_state=current_state,
        target_profile=target,
        intent_id=intent_id,
        created_at=created_at,
    )
