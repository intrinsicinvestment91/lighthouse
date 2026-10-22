"""Activation-preconditions evaluator (LHP-PROFILE-14B).

Answers ONE question, read-only and deterministically: *would this transition be allowed
to proceed if an activation capability existed?* It never activates, never mutates, never
writes, and never contacts the Gateway — it is the Lighthouse-side reasoning layer a
future activation packet gates on, and the "validation result" the activation-intent
artifact embeds.

Decisions (``invalid`` > ``blocked`` > ``ready``, fail closed):

- ``invalid`` — the evaluation inputs are structurally unusable: the preview does not
  parse or fails its own contract validation, or the current-state record fails its
  contract validation. Nothing further can be reasoned about.
- ``blocked`` — evaluable, but not affirmatively proven safe: anything missing, failing,
  or unknown lands here. Unknown NEVER becomes ready.
- ``ready`` — every check affirmatively passed. Ready is a statement about
  preconditions, not an authorization: nothing may act on it (no activation exists,
  and approval authority is Gateway-owned).

Checks, in fixed order (reasons are stable snake-case tokens, deterministic, no free
text): preview parses + validates; ``transition_status == ready_for_review``; the 14A
producer facts are present (``target_policy_snapshot``, ``target_profile_sha256``,
``backup_manifest_sha256`` — the "required capability facts": what a future activation
consumes; Gateway capability *attestations* are Gateway-owned evidence and are never
consumed here); backup evidence present and passing; the target profile is supplied,
valid, matches the preview's target reference, and its recomputed canonical content
digest equals the preview's ``target_profile_sha256``; the declared current state (an
explicit ``ActiveProfileState`` — callers construct absence via
``no_active_profile_state()``, there is no implicit default) validates and agrees with
the preview's ``current_profile_id``/``current_profile_version`` pair.

No clock is read: freshness/staleness of artifacts is Gateway-owned by design
(the execution plan carries expiry; this evaluator is time-independent).
"""

import re
from dataclasses import dataclass
from typing import Any

from modules.active_profile_state import STATE_NO_ACTIVE_PROFILE, ActiveProfileState
from modules.profile_registry import get_profile
from modules.profile_transition import (
    RESTORE_DRILL_PASS_STATUS,
    STATUS_READY,
    VERIFICATION_PASS_STATUSES,
    ProfileTransitionPreview,
    profile_content_sha256,
)
from modules.profiles import IntelligenceProfile

_SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")

DECISION_READY = "ready"
DECISION_BLOCKED = "blocked"
DECISION_INVALID = "invalid"
ALLOWED_PRECONDITION_DECISIONS = frozenset({DECISION_READY, DECISION_BLOCKED, DECISION_INVALID})


class ActivationPreconditionsError(Exception):
    """Raised only for caller-contract abuse; content problems yield results, not raises."""


@dataclass(frozen=True)
class ActivationPreconditionsResult:
    """Deterministic evaluator outcome: a decision plus ordered, stable reason tokens."""

    decision: str
    reasons: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {"decision": self.decision, "reasons": list(self.reasons)}


def _parse_preview(preview: Any) -> tuple[ProfileTransitionPreview | None, list[str]]:
    """Parse + contract-validate the preview dict. Any failure is structural → invalid."""
    if not isinstance(preview, dict):
        return None, ["preview_malformed"]
    try:
        parsed = ProfileTransitionPreview.from_dict(preview)
        parsed.validate()
    except Exception:  # noqa: BLE001 — fail closed: ANY parse/validation failure blocks reasoning
        return None, ["preview_malformed"]
    return parsed, []


def evaluate_activation_preconditions(
    preview: dict[str, Any],
    *,
    current_state: ActiveProfileState,
    target_profile: IntelligenceProfile | None,
) -> ActivationPreconditionsResult:
    """Pure, read-only evaluation. See the module docstring for decision semantics.

    ``current_state`` has no default on purpose: the caller must declare the current
    state explicitly, using ``no_active_profile_state()`` when nothing is active.
    """
    invalid: list[str] = []
    blocked: list[str] = []

    parsed, preview_problems = _parse_preview(preview)
    invalid.extend(preview_problems)

    state_valid = False
    if not isinstance(current_state, ActiveProfileState):
        invalid.append("current_state_invalid")
    else:
        try:
            current_state.validate()
            state_valid = True
        except Exception:  # noqa: BLE001 — fail closed
            invalid.append("current_state_invalid")

    if invalid:
        return ActivationPreconditionsResult(DECISION_INVALID, tuple(invalid))
    assert parsed is not None and state_valid  # narrowed by the returns above

    if parsed.transition_status != STATUS_READY:
        blocked.append(f"preview_not_ready:{parsed.transition_status}")

    if parsed.target_policy_snapshot is None:
        blocked.append("producer_fact_missing:target_policy_snapshot")
    if not parsed.target_profile_sha256:
        blocked.append("producer_fact_missing:target_profile_sha256")
    if not parsed.backup_manifest_sha256:
        blocked.append("producer_fact_missing:backup_manifest_sha256")
    # The preview contract deliberately never inspects its own artifact_digest (the
    # Gateway trusts and format-validates it at intake) — but downstream consumers bind
    # BY that digest, so a malformed one means the preview cannot be referenced at all.
    if not isinstance(parsed.artifact_digest, str) or not _SHA256_HEX.match(parsed.artifact_digest):
        blocked.append("preview_digest_malformed")

    if not parsed.backup_manifest_id:
        blocked.append("evidence_missing:backup_manifest_id")
    if not parsed.backup_verification_status:
        blocked.append("evidence_missing:backup_verification_status")
    elif parsed.backup_verification_status not in VERIFICATION_PASS_STATUSES:
        blocked.append("evidence_not_passing:backup_verification_status")
    if not parsed.restore_drill_status:
        blocked.append("evidence_missing:restore_drill_status")
    elif parsed.restore_drill_status != RESTORE_DRILL_PASS_STATUS:
        blocked.append("evidence_not_passing:restore_drill_status")

    if target_profile is None:
        blocked.append("target_profile_missing")
    else:
        target_ok = False
        try:
            target_profile.validate()
            target_ok = True
        except Exception:  # noqa: BLE001 — fail closed
            blocked.append("target_profile_invalid")
        if target_ok:
            if (
                target_profile.profile_id != parsed.target_profile_id
                or target_profile.profile_version != parsed.target_profile_version
            ):
                blocked.append("target_profile_mismatch")
            elif (
                parsed.target_profile_sha256
                and profile_content_sha256(target_profile) != parsed.target_profile_sha256
            ):
                blocked.append("target_profile_digest_mismatch")

    if current_state.activation_state == STATE_NO_ACTIVE_PROFILE:
        if parsed.current_profile_id is not None or parsed.current_profile_version is not None:
            blocked.append("current_state_mismatch")
    elif (
        parsed.current_profile_id != current_state.active_profile_id
        or parsed.current_profile_version != current_state.active_profile_version
    ):
        blocked.append("current_state_mismatch")

    if blocked:
        return ActivationPreconditionsResult(DECISION_BLOCKED, tuple(blocked))
    return ActivationPreconditionsResult(DECISION_READY, ())


def evaluate_activation_preconditions_from_registry(
    preview: dict[str, Any],
    *,
    current_state: ActiveProfileState,
    directory: str | None = None,
) -> ActivationPreconditionsResult:
    """Convenience wrapper: resolve the target profile from the bundled registry (read-only).

    Any resolution failure — unknown id, unknown version, unreadable registry — fails
    closed to a ``None`` target, which the core evaluator reports as
    ``target_profile_missing`` (blocked). Never raises for content reasons.
    """
    target: IntelligenceProfile | None = None
    if isinstance(preview, dict):
        target_id = preview.get("target_profile_id")
        target_version = preview.get("target_profile_version")
        if isinstance(target_id, str) and target_id:
            version = (
                target_version
                if isinstance(target_version, int) and not isinstance(target_version, bool)
                else None
            )
            try:
                target = get_profile(target_id, version=version, directory=directory)
            except Exception:  # noqa: BLE001 — fail closed: unresolvable target is missing
                target = None
    return evaluate_activation_preconditions(
        preview, current_state=current_state, target_profile=target
    )
