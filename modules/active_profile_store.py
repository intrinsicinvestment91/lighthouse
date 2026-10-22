"""Singleton active-profile store (LHP-V1-PROFILE-01C/01E).

Persists at most one validated ``active_profile_state.v1`` record. Missing database or missing
table means ``no_active_profile`` — and a read never creates either. Corrupt or multiple rows
fail closed. Initial activation is atomic (``BEGIN IMMEDIATE``); identical reactivation is
idempotent; a different active identity is unsupported switching and is refused.

This module does not evaluate evidence, does not contact the Gateway, and does not switch,
deactivate, or reset. The activation *ceremony* lives in ``modules/profile_activation.py`` —
``_persist_initial_activation`` is intentionally private to that service.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from modules import db
from modules.active_profile_state import (
    STATE_ACTIVE,
    STATE_NO_ACTIVE_PROFILE,
    ActiveProfileState,
    ActiveProfileStateError,
    no_active_profile_state,
)

__all__ = [
    "ActiveProfileStoreError",
    "CorruptActiveProfileStateError",
    "UnsupportedProfileSwitchError",
    "read_active_profile_state",
]


class ActiveProfileStoreError(Exception):
    """Base class for active-profile store failures."""


class CorruptActiveProfileStateError(ActiveProfileStoreError):
    """Stored bytes are unreadable, invalid, or ambiguous (more than one row)."""


class UnsupportedProfileSwitchError(ActiveProfileStoreError):
    """An active profile already exists and differs from the requested initial activation."""


def read_active_profile_state() -> ActiveProfileState:
    """Return the active state, or the explicit ``no_active_profile`` record.

    Never creates a database file or table. Zero rows → ``no_active_profile``. One valid
    row → that state. Anything else (corrupt JSON, contract failure, multiple rows) raises
    ``CorruptActiveProfileStateError``.
    """
    try:
        rows = db.fetch_active_profile_rows()
    except (OSError, sqlite3.Error) as exc:
        raise CorruptActiveProfileStateError(f"active profile store unreadable: {exc}") from exc

    if not rows:
        return no_active_profile_state()
    if len(rows) != 1:
        raise CorruptActiveProfileStateError(
            f"ambiguous active profile state: expected 0 or 1 row, found {len(rows)}"
        )
    return _row_to_state(rows[0])


def _persist_initial_activation(state: ActiveProfileState) -> ActiveProfileState:
    """Atomically persist an ``active`` record as the sole initial activation.

    Idempotent when the stored identity (artifact digest) matches. Refuses when a different
    active profile is already present. Returns the validated post-write state (read back).
    """
    if not isinstance(state, ActiveProfileState):
        raise ActiveProfileStoreError("state must be an ActiveProfileState")
    try:
        state.validate()
    except ActiveProfileStateError as exc:
        raise ActiveProfileStoreError(f"refusing to persist invalid state: {exc}") from exc
    if state.activation_state != STATE_ACTIVE:
        raise ActiveProfileStoreError(
            f"initial activation requires activation_state={STATE_ACTIVE!r}, "
            f"got {state.activation_state!r}"
        )

    payload = state.to_dict()
    state_json = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    try:
        outcome = db._activate_active_profile_atomic(
            state_json,
            state.artifact_digest,
            profile_id=state.active_profile_id,
            profile_version=state.active_profile_version,
            profile_sha256=state.active_profile_sha256,
        )
    except RuntimeError as exc:
        message = str(exc)
        if message.startswith("unsupported_profile_switch"):
            raise UnsupportedProfileSwitchError(message) from exc
        if message.startswith("corrupt_active_profile_state"):
            raise CorruptActiveProfileStateError(message) from exc
        raise ActiveProfileStoreError(message) from exc
    except sqlite3.Error as exc:
        raise ActiveProfileStoreError(f"activation transaction failed: {exc}") from exc

    # Idempotent path: return the *stored* record (preserves original activated_at).
    if outcome == "already_present":
        return read_active_profile_state()

    read_back = read_active_profile_state()
    if read_back.activation_state != STATE_ACTIVE:
        raise CorruptActiveProfileStateError(
            "activation write succeeded but read-back is not active"
        )
    if read_back.artifact_digest != state.artifact_digest:
        raise CorruptActiveProfileStateError(
            "activation write succeeded but read-back digest differs"
        )
    return read_back


def _row_to_state(row: dict[str, Any]) -> ActiveProfileState:
    try:
        data = json.loads(row["state_json"])
    except (TypeError, json.JSONDecodeError) as exc:
        raise CorruptActiveProfileStateError(
            f"active profile state_json is not valid JSON: {exc}"
        ) from exc
    try:
        state = ActiveProfileState.from_dict(data)
    except ActiveProfileStateError as exc:
        raise CorruptActiveProfileStateError(
            f"active profile state fails its contract: {exc}"
        ) from exc
    if state.artifact_digest != row["artifact_digest"]:
        raise CorruptActiveProfileStateError(
            "stored artifact_digest column does not match state_json digest"
        )
    if state.activation_state == STATE_NO_ACTIVE_PROFILE:
        # Absence is represented by *no row*, never by storing the absent record.
        raise CorruptActiveProfileStateError(
            "stored row declares no_active_profile — ambiguous; refuse"
        )
    return state
