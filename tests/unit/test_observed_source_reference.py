"""Observed-source reference export (LHP-INTEL-04D-R1 / 04D-R2)."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from modules import db
from modules import public_source_check as psc
from modules.observed_source_exporter import ExportError, export_observed_source_reference
from modules.observed_source_reference import (
    AUTHORITATIVE_SCHEMA_COMMIT,
    AUTHORITATIVE_SCHEMA_SHA256,
    AUTHORITATIVE_SCHEMA_SOURCE,
    CANONICALIZATION_VERSION,
    CONSUMER_ARTICLE_TO_SKILLS,
    CONSUMER_OFFLINE_SOVEREIGN_STACK,
    DETERMINISTIC_EXPORT_VERSION,
    OSR_DIGEST_EXCLUDE,
    OSR_SCHEMA_VERSION,
    PRODUCER_CONTRACT,
    PRODUCER_SYSTEM,
    ObservedSourceReferenceError,
    build_observed_source_reference,
    from_dict,
)

POLICY_DIGEST = hashlib.sha256(b"policy").hexdigest()
ADAPTER_ID = "public_source_intake"
ADAPTER_VERSION = "public_source_intake/1"
LOCATOR = "https://archive.example.org/dataset.csv"
DIGEST = hashlib.sha256(b"dataset-bytes").hexdigest()

SCHEMA_PATH = (
    Path(__file__).resolve().parents[2]
    / "docs"
    / "schemas"
    / "observed_source_reference.v0.schema.json"
)


def _available_check(
    *, run_id: str, checked_at: str = "2026-07-14T12:00:00+00:00"
) -> psc.PublicSourceCheck:
    return psc.build_check(
        run_id=run_id,
        canonical_locator=LOCATOR,
        final_locator=LOCATOR,
        redirected=False,
        redirect_chain=(),
        outcome=psc.AVAILABLE,
        http_status=200,
        content_digest=DIGEST,
        body_size_bytes=len(b"dataset-bytes"),
        content_type="text/csv",
        checked_at=checked_at,
        adapter_id=ADAPTER_ID,
        adapter_version=ADAPTER_VERSION,
        policy_digest=POLICY_DIGEST,
    )


def _store_check(check: psc.PublicSourceCheck) -> None:
    run = psc.build_run(
        started_at=check.checked_at,
        completed_at=check.checked_at,
        run_nonce="osr-nonce",
        adapter_id=ADAPTER_ID,
        adapter_version=ADAPTER_VERSION,
        policy_digest=POLICY_DIGEST,
        targets_ordered=(LOCATOR,),
        checks=(check,),
    )
    db.store_check_run(run=run.to_dict(), checks=[check.to_dict()])


def _consumer_recompute(payload: dict) -> str:
    """Mirror OSS/ACL: sha256(canonical_json(payload minus OSR_DIGEST_EXCLUDE))."""
    material = {k: v for k, v in payload.items() if k not in OSR_DIGEST_EXCLUDE}
    canonical = json.dumps(material, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "state.db")
    return tmp_path / "state.db"


def test_build_and_validate_both_consumer_profiles():
    check = _available_check(run_id="psr_" + "a" * 64)
    for consumer in (CONSUMER_ARTICLE_TO_SKILLS, CONSUMER_OFFLINE_SOVEREIGN_STACK):
        ref = build_observed_source_reference(
            check,
            intended_consumer=consumer,
            producer_repository="pattern-match-sentinel",
            producer_commit="c" * 40,
        )
        ref.validate()
        roundtrip = from_dict(ref.to_dict())
        assert roundtrip.reference_id == ref.reference_id
        assert roundtrip.intended_consumer == consumer


def test_refuses_non_available_outcome():
    check = psc.build_check(
        run_id="psr_" + "b" * 64,
        canonical_locator=LOCATOR,
        final_locator=LOCATOR,
        redirected=False,
        redirect_chain=(),
        outcome=psc.NOT_FOUND,
        http_status=404,
        content_digest=None,
        body_size_bytes=None,
        content_type=None,
        checked_at="2026-07-14T12:00:00+00:00",
        adapter_id=ADAPTER_ID,
        adapter_version=ADAPTER_VERSION,
        policy_digest=POLICY_DIGEST,
    )
    with pytest.raises(ObservedSourceReferenceError, match="only outcome 'available'"):
        build_observed_source_reference(check, intended_consumer=CONSUMER_ARTICLE_TO_SKILLS)


def test_exporter_reads_ledger(ledger):
    check = _available_check(run_id="psr_" + "d" * 64)
    _store_check(check)
    result = export_observed_source_reference(
        check.check_id,
        intended_consumer=CONSUMER_OFFLINE_SOVEREIGN_STACK,
        producer_commit="e" * 40,
    )
    ref = result["reference"]
    assert ref["reference_id"].startswith("osr_")
    assert ref["intended_consumer"] == CONSUMER_OFFLINE_SOVEREIGN_STACK
    assert ref["content_digest"] == DIGEST
    assert ref["execution_authorized"] is False


def test_exporter_refuses_missing_check(ledger):
    with pytest.raises(ExportError):
        export_observed_source_reference(
            "psc_" + "f" * 64,
            intended_consumer=CONSUMER_ARTICLE_TO_SKILLS,
        )


# --- LHP-INTEL-04D-R2: consumer-compatible identity + validation ---


def test_digest_exclude_set_matches_oss_and_acl_consumers():
    assert (
        frozenset({"reference_id", "artifact_digest", "producer_repository", "producer_commit"})
        == OSR_DIGEST_EXCLUDE
    )


def test_artifact_digest_matches_consumer_exclude_set_recompute():
    check = _available_check(run_id="psr_" + "1" * 64)
    ref = build_observed_source_reference(
        check,
        intended_consumer=CONSUMER_ARTICLE_TO_SKILLS,
        producer_repository="pattern-match-sentinel",
        producer_commit="a" * 40,
    )
    payload = ref.to_dict()
    assert payload["artifact_digest"] == _consumer_recompute(payload)
    assert payload["reference_id"] == "osr_" + payload["artifact_digest"]


def test_identity_stable_across_producer_repository_and_commit_stamps():
    check = _available_check(run_id="psr_" + "2" * 64)
    a = build_observed_source_reference(
        check,
        intended_consumer=CONSUMER_OFFLINE_SOVEREIGN_STACK,
        producer_repository="pattern-match-sentinel",
        producer_commit="a" * 40,
    )
    b = build_observed_source_reference(
        check,
        intended_consumer=CONSUMER_OFFLINE_SOVEREIGN_STACK,
        producer_repository="other-checkout-name",
        producer_commit="b" * 40,
    )
    c = build_observed_source_reference(
        check,
        intended_consumer=CONSUMER_OFFLINE_SOVEREIGN_STACK,
        producer_repository=None,
        producer_commit=None,
    )
    assert a.artifact_digest == b.artifact_digest == c.artifact_digest
    assert a.reference_id == b.reference_id == c.reference_id
    assert a.producer_repository != b.producer_repository
    assert a.to_dict()["producer_commit"] != b.to_dict()["producer_commit"]


def test_from_dict_refuses_unknown_top_level_field():
    check = _available_check(run_id="psr_" + "3" * 64)
    payload = build_observed_source_reference(
        check, intended_consumer=CONSUMER_ARTICLE_TO_SKILLS
    ).to_dict()
    payload["sneaky_score"] = 0.9
    with pytest.raises(ObservedSourceReferenceError, match="unknown"):
        from_dict(payload)


def test_from_dict_refuses_unknown_nested_authority_shaped_field():
    """OSR is flat; an unknown key that looks nested-authority still fails closed."""
    check = _available_check(run_id="psr_" + "4" * 64)
    payload = build_observed_source_reference(
        check, intended_consumer=CONSUMER_ARTICLE_TO_SKILLS
    ).to_dict()
    payload["authority_extensions"] = {"retention_authorized": True}
    with pytest.raises(ObservedSourceReferenceError, match="unknown"):
        from_dict(payload)


def test_required_producer_contract_and_version_constants():
    assert PRODUCER_SYSTEM == "lighthouse"
    assert PRODUCER_CONTRACT == "public_source_check.v1"
    assert DETERMINISTIC_EXPORT_VERSION == "observed_source_reference_exporter.v0"
    assert CANONICALIZATION_VERSION == "lighthouse_canonical_json.v1"
    assert OSR_SCHEMA_VERSION == "observed_source_reference.v0"

    check = _available_check(run_id="psr_" + "5" * 64)
    payload = build_observed_source_reference(
        check, intended_consumer=CONSUMER_ARTICLE_TO_SKILLS
    ).to_dict()
    assert payload["producer_system"] == PRODUCER_SYSTEM
    assert payload["producer_contract"] == PRODUCER_CONTRACT
    assert payload["deterministic_export_version"] == DETERMINISTIC_EXPORT_VERSION
    assert payload["canonicalization_version"] == CANONICALIZATION_VERSION

    payload["producer_system"] = "not-lighthouse"
    # restamp so only the constant guard can catch it
    payload["artifact_digest"] = _consumer_recompute(payload)
    payload["reference_id"] = "osr_" + payload["artifact_digest"]
    with pytest.raises(ObservedSourceReferenceError, match="producer_system"):
        from_dict(payload)


def test_identifier_and_https_locator_patterns_enforced():
    check = _available_check(run_id="psr_" + "6" * 64)
    base = build_observed_source_reference(
        check, intended_consumer=CONSUMER_ARTICLE_TO_SKILLS
    ).to_dict()

    bad_source = dict(base)
    bad_source["source_id"] = "source:" + "a" * 64
    bad_source["artifact_digest"] = _consumer_recompute(bad_source)
    bad_source["reference_id"] = "osr_" + bad_source["artifact_digest"]
    with pytest.raises(ObservedSourceReferenceError, match="source_id"):
        from_dict(bad_source)

    bad_locator = dict(base)
    bad_locator["canonical_locator"] = "http://archive.example.org/x"
    bad_locator["artifact_digest"] = _consumer_recompute(bad_locator)
    bad_locator["reference_id"] = "osr_" + bad_locator["artifact_digest"]
    with pytest.raises(ObservedSourceReferenceError, match="canonical_locator|https"):
        from_dict(bad_locator)


def test_source_and_privacy_class_constants_enforced():
    check = _available_check(run_id="psr_" + "7" * 64)
    payload = build_observed_source_reference(
        check, intended_consumer=CONSUMER_OFFLINE_SOVEREIGN_STACK
    ).to_dict()
    payload["source_class"] = "private_source"
    payload["artifact_digest"] = _consumer_recompute(payload)
    payload["reference_id"] = "osr_" + payload["artifact_digest"]
    with pytest.raises(ObservedSourceReferenceError, match="source_class"):
        from_dict(payload)

    payload = build_observed_source_reference(
        check, intended_consumer=CONSUMER_OFFLINE_SOVEREIGN_STACK
    ).to_dict()
    payload["privacy_class"] = "confidential"
    payload["artifact_digest"] = _consumer_recompute(payload)
    payload["reference_id"] = "osr_" + payload["artifact_digest"]
    with pytest.raises(ObservedSourceReferenceError, match="privacy_class"):
        from_dict(payload)


def test_authoritative_schema_digest_and_byte_mirror_pin():
    assert AUTHORITATIVE_SCHEMA_SHA256 == (
        "ca9211fd65f79cb7ba19fd3cfbe829ad1382c4afd03ed1351f4242ecc209cc0e"
    )
    assert AUTHORITATIVE_SCHEMA_COMMIT.startswith("53b9761")
    assert AUTHORITATIVE_SCHEMA_SOURCE.endswith(
        "services/retained_source/schemas/observed_source_reference.v0.schema.json"
    )
    digest = hashlib.sha256(SCHEMA_PATH.read_bytes()).hexdigest()
    assert digest == AUTHORITATIVE_SCHEMA_SHA256
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    assert schema["additionalProperties"] is False
    assert "producer_repository, producer_commit" in schema["description"]
