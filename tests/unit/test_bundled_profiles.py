"""Validation tests for the shipped bundled profile definitions (seed/profiles/)."""

from modules.backup_readiness import DATA_BOUNDARY, is_within_boundary
from modules.profile_registry import get_profile, list_bundled_profiles, validate_all_bundled
from modules.profile_transition import profile_content_sha256
from modules.profiles import ALLOWED_STATUSES
from modules.repository_content_recurrence import (
    CANDIDATE_TYPE,
    DETECTOR_ID,
    OWNER_PROFILE_ID,
    RULE_ID,
)
from modules.scientific_source_disappearance import (
    CANDIDATE_TYPE as SP_CANDIDATE_TYPE,
)
from modules.scientific_source_disappearance import (
    DETECTOR_ID as SP_DETECTOR_ID,
)
from modules.scientific_source_disappearance import (
    OWNER_PROFILE_ID as SP_OWNER_PROFILE_ID,
)
from modules.scientific_source_disappearance import (
    RULE_ID as SP_RULE_ID,
)

EXPECTED_PROFILE_IDS = {
    "public_scientific_lighthouse",
    "aerospace_defense_monitoring",
    "fusion_research_monitoring",
    "repository_capability_discovery",
    "scientific_preservation",
}

# Pre-LHP-V1-PROFILE-01B content digests for every non-RCD bundled profile.
UNCHANGED_PEER_DIGESTS = {
    "public_scientific_lighthouse": (
        "c552733612184ea6e3d0da96111a49caf600faf8dde5fae122147636ca2bf500"
    ),
    "aerospace_defense_monitoring": (
        "12d070bc15a3d2ff8420c370f3021d89dcbbf16676db8eb6c78e5514272893bf"
    ),
    "fusion_research_monitoring": (
        "0abc14907c3cf0d222df154acb4b4b0c8f91bfe0f4d841e49368f7ae9eecbe55"
    ),
}

_PRIOR_SP_DIGEST = "8bbe141de0492beeba23a8af5facca95f115f4c260895041124ea07679969776"

# Pre-truth-up RCD digest — content must change visibly when profile_version bumps.
_PRIOR_RCD_DIGEST = "60155ec146e49dc9a5e0633ecd59c9e93ced3a3e5e6db3489c59c72ff2867337"


def test_all_bundled_profiles_validate():
    result = validate_all_bundled()
    assert result["valid"] is True, result["errors"]
    assert result["profile_count"] == len(EXPECTED_PROFILE_IDS)


def test_expected_profile_ids_present():
    ids = {p.profile_id for p in list_bundled_profiles()}
    assert ids == EXPECTED_PROFILE_IDS


def test_no_bundled_profile_is_production_ready():
    for profile in list_bundled_profiles():
        assert profile.status in ALLOWED_STATUSES
        assert profile.status != "production"


def test_data_roots_resolve_under_boundary():
    for profile in list_bundled_profiles():
        data_root = profile.backup_policy.get("data_root")
        if data_root:
            assert is_within_boundary(data_root, DATA_BOUNDARY), profile.profile_id


def test_public_profile_distinctions():
    public = get_profile("public_scientific_lighthouse")
    assert public.privacy_policy.get("local_only") is False
    assert public.publishing_policy.get("public_publishing") is False
    assert public.publishing_policy.get("requires_operator_approval") is True
    assert public.dead_mans_switch_policy.get("eligible", False) is False


def test_preservation_profile_declares_dms_eligibility_only():
    preservation = get_profile("scientific_preservation")
    # Declarative eligibility is allowed; enrollment/activation must remain disabled.
    assert preservation.dead_mans_switch_policy.get("eligible") is True
    assert preservation.dead_mans_switch_policy.get("enrolled", False) is False
    assert preservation.dead_mans_switch_policy.get("activated", False) is False
    assert preservation.publishing_policy.get("disclosure_package") is False


def test_preservation_profile_owned_detector_metadata():
    """LHP-V1-SP-01 — seed declares the 04C detector ownership; CLI activation still deferred."""
    preservation = get_profile("scientific_preservation")
    assert preservation.profile_id == SP_OWNER_PROFILE_ID
    assert preservation.metadata.get("owned_detector_id") == SP_DETECTOR_ID
    assert preservation.metadata.get("owned_candidate_type") == SP_CANDIDATE_TYPE
    assert preservation.metadata.get("owned_rule_id") == SP_RULE_ID
    digest = profile_content_sha256(preservation)
    assert digest != _PRIOR_SP_DIGEST


def test_rcd_profile_truthful_contract():
    """LHP-V1-PROFILE-01B — RCD seed matches the committed operator loop, not future packs."""
    rcd = get_profile("repository_capability_discovery")

    assert rcd.profile_id == OWNER_PROFILE_ID
    assert rcd.profile_version == 2
    assert rcd.status == "ready_for_validation"
    assert rcd.status != "active"
    assert rcd.status in ALLOWED_STATUSES

    assert rcd.source_policy.get("allowed_source_types") == ["repository"]
    assert "repo_source_pack" not in rcd.source_policy.get("allowed_source_types", [])
    assert "reference_repo_pack" not in rcd.source_policy.get("allowed_source_types", [])
    assert rcd.source_policy.get("requires_gateway_approval") is False
    assert rcd.source_policy.get("external_intake") is False
    assert rcd.source_policy.get("external_repo_execution") is False
    assert rcd.source_policy.get("external_repo_install") is False

    assert rcd.collector_policy.get("enabled_collectors") == ["repository_intake"]

    assert rcd.metadata.get("owned_detector_id") == DETECTOR_ID
    assert rcd.metadata.get("owned_candidate_type") == CANDIDATE_TYPE
    assert rcd.metadata.get("owned_rule_id") == RULE_ID
    assert rcd.metadata.get("engine_migrated") is True
    assert rcd.metadata.get("is_hypothesis") is False
    assert rcd.metadata.get("is_finding") is False
    assert rcd.metadata.get("authorizes_execution") is False
    assert rcd.metadata.get("authorizes_publication") is False

    assert rcd.backup_policy.get("backup_required_on_activation") is True
    assert rcd.backup_policy.get("data_root") == "/data"
    assert rcd.backup_policy.get("data_root") != ("/data/profiles/repository_capability_discovery")

    assert rcd.publishing_policy.get("public_publishing") is False
    assert rcd.privacy_policy.get("local_only") is True

    digest = profile_content_sha256(rcd)
    assert len(digest) == 64
    assert digest != _PRIOR_RCD_DIGEST


def test_other_bundled_profiles_unchanged_by_rcd_truthup():
    """Only the RCD seed may change in LHP-V1-PROFILE-01B."""
    for profile_id, expected_digest in UNCHANGED_PEER_DIGESTS.items():
        profile = get_profile(profile_id)
        assert profile.profile_version == 1
        assert profile_content_sha256(profile) == expected_digest
