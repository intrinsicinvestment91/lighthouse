"""Shared helpers for planting a post-activation RCD active-profile state in tests.

This is test setup of the store's legitimate post-activation record — not a bypass of the
admission gates. Production activation still requires confirmation + evidence + the service.
"""

from __future__ import annotations

from modules.active_profile_state import (
    ACTIVATION_SOURCE_LOCAL_CLI,
    ActivationEvidenceReferences,
    build_active_profile_state,
)
from modules.profile_registry import get_profile

RCD_ID = "repository_capability_discovery"
RCD_VERSION = 2
RCD_DIGEST = "11e357a8ab2597091ba9580aaff1dd0701d0e22047b6114fecbbc5e0bd336917"

SP_ID = "scientific_preservation"
SP_VERSION = 1
SP_DIGEST = "0c8116ef43468a4e58c5e3ffd90e9180e97696a4e55d1e92dc65ba0d4ea6270a"

ACTIVATED_AT = "2026-08-30T12:00:00+00:00"
CONFIRMATION = "ACTIVATE repository_capability_discovery v2"

SYNTHETIC_EVIDENCE = ActivationEvidenceReferences(
    transition_preview_id="preview-test-synthetic",
    transition_preview_digest="a" * 64,
    backup_manifest_id="lhbackup-test-synthetic",
    backup_manifest_sha256="b" * 64,
    verification_report_digest="c" * 64,
    restore_report_digest="d" * 64,
    source_data_root="/tmp/lighthouse-test-data",
    backup_root="/tmp/lighthouse-test-backup",
)


def rcd_profile():
    return get_profile(RCD_ID, version=RCD_VERSION)


def sp_profile():
    return get_profile(SP_ID, version=SP_VERSION)


def build_rcd_active_state(*, activated_at: str = ACTIVATED_AT):
    return build_active_profile_state(
        rcd_profile(),
        activated_at=activated_at,
        activation_source=ACTIVATION_SOURCE_LOCAL_CLI,
        evidence=SYNTHETIC_EVIDENCE,
    )


def plant_active_rcd(*, activated_at: str = ACTIVATED_AT):
    """Persist a validated active RCD v2 row through the test-only store writer."""
    from modules import active_profile_store

    return active_profile_store._persist_initial_activation(
        build_rcd_active_state(activated_at=activated_at)
    )


def build_sp_active_state(*, activated_at: str = ACTIVATED_AT):
    return build_active_profile_state(
        sp_profile(),
        activated_at=activated_at,
        activation_source=ACTIVATION_SOURCE_LOCAL_CLI,
        evidence=SYNTHETIC_EVIDENCE,
    )


def plant_active_sp(*, activated_at: str = ACTIVATED_AT):
    """Persist a validated active scientific_preservation row.

    Test setup only — no CLI ceremony.
    """
    from modules import active_profile_store

    return active_profile_store._persist_initial_activation(
        build_sp_active_state(activated_at=activated_at)
    )
