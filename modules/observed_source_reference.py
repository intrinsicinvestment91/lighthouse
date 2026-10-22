"""Observed-source reference contract (LHP-INTEL-04D-R1 / 04D-R2).

An immutable, byte-honest reference emitted by Lighthouse from ONE validated ``available``
``public_source_check.v1``. It carries locator metadata, content identity, and check provenance —
**not the body** and **no authority**.

Identity matches the authoritative OSS/ACL consumer exclude set (schema description + consumer
validators): ``artifact_digest`` hashes every field EXCEPT ``reference_id``, ``artifact_digest``,
``producer_repository``, and ``producer_commit``. Producer repo/commit stamps are provenance only.

Pure: no network, no database, no clock. Callers supply producer stamps; this module shapes and
checks the record.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, fields, replace
from typing import Any

from modules.content_identity import DIGEST_ALGORITHM
from modules.public_source_check import (
    AVAILABLE,
    CHECK_SCHEMA_VERSION,
    PublicSourceCheck,
)

OSR_SCHEMA_VERSION = "observed_source_reference.v0"
DETERMINISTIC_EXPORT_VERSION = "observed_source_reference_exporter.v0"
CANONICALIZATION_VERSION = "lighthouse_canonical_json.v1"
PRODUCER_SYSTEM = "lighthouse"
PRODUCER_CONTRACT = CHECK_SCHEMA_VERSION

REFERENCE_ID_PREFIX = "osr_"

SOURCE_CLASS = "public_source"
PRIVACY_CLASS = "public"

# Authoritative consumer schema (OSS retained_source; ACL vendors the same bytes).
AUTHORITATIVE_SCHEMA_SOURCE = (
    "offline-sovereign-stack/services/retained_source/schemas/"
    "observed_source_reference.v0.schema.json"
)
AUTHORITATIVE_SCHEMA_COMMIT = "53b9761"
AUTHORITATIVE_SCHEMA_SHA256 = "ca9211fd65f79cb7ba19fd3cfbe829ad1382c4afd03ed1351f4242ecc209cc0e"

# Same exclude set as OSS ``OSR_DIGEST_EXCLUDE`` and ACL ``_OSR_EXCLUDE``.
OSR_DIGEST_EXCLUDE = frozenset(
    {"reference_id", "artifact_digest", "producer_repository", "producer_commit"}
)

CONSUMER_ARTICLE_TO_SKILLS = "article-to-skills"
CONSUMER_OFFLINE_SOVEREIGN_STACK = "offline-sovereign-stack"

USE_ARTICLE_TO_SKILLS = "exact_source_byte_preflight_before_skill_derivation"
USE_OFFLINE_SOVEREIGN_STACK = "exact_source_byte_preflight_for_local_retention"

CONSUMER_PROFILES: dict[str, str] = {
    CONSUMER_ARTICLE_TO_SKILLS: USE_ARTICLE_TO_SKILLS,
    CONSUMER_OFFLINE_SOVEREIGN_STACK: USE_OFFLINE_SOVEREIGN_STACK,
}

AUTHORITY_PINS: dict[str, bool] = {
    "authenticity_established": False,
    "scientific_correctness_established": False,
    "legal_retention_rights_established": False,
    "redistribution_rights_established": False,
    "retention_authorized": False,
    "skill_derivation_authorized": False,
    "candidate_approval_granted": False,
    "registry_publication_authorized": False,
    "external_publication_authorized": False,
    "installation_authorized": False,
    "execution_authorized": False,
    "disclosure_authorized": False,
    "gateway_approval_granted": False,
}

OPERATOR_PINS: dict[str, bool] = {
    "operator_handoff_required": True,
    "operator_derivation_invocation_required": True,
}

BODY_PINS: dict[str, bool] = {
    "body_included": False,
    "body_retained_by_producer": False,
    "consumer_must_supply_local_file": True,
    "consumer_must_verify_exact_digest": True,
    "local_only": True,
}

_OSR_ID_RE = re.compile(r"^osr_[0-9a-f]{64}$")
_HEX64_RE = re.compile(r"^[0-9a-f]{64}$")
_COMMIT40_RE = re.compile(r"^[0-9a-f]{40}$")
_PSC_RE = re.compile(r"^psc_[0-9a-f]{64}$")
_PSR_RE = re.compile(r"^psr_[0-9a-f]{64}$")
_PUBSOURCE_RE = re.compile(r"^public-source:[0-9a-f]{64}$")
_PUBLOCATOR_RE = re.compile(r"^public-locator:[0-9a-f]{64}$")
_HTTPS_RE = re.compile(r"^https://")
_CHECKED_AT_RE = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]+)?(Z|[+-][0-9]{2}:[0-9]{2})$"
)


class ObservedSourceReferenceError(Exception):
    """The reference contract refused to build or validate."""


def _canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _artifact_digest(content: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(content).encode("utf-8")).hexdigest()


def _require_consumer(intended_consumer: str) -> str:
    if intended_consumer not in CONSUMER_PROFILES:
        allowed = ", ".join(sorted(CONSUMER_PROFILES))
        raise ObservedSourceReferenceError(
            f"unknown intended_consumer {intended_consumer!r} (allowed: {allowed})"
        )
    return CONSUMER_PROFILES[intended_consumer]


@dataclass(frozen=True)
class ObservedSourceReference:
    """One immutable reference from a single successful public-source check."""

    schema_version: str
    reference_id: str
    artifact_digest: str
    producer_system: str
    producer_contract: str
    deterministic_export_version: str
    canonicalization_version: str
    producer_repository: str | None
    producer_commit: str | None
    intended_consumer: str
    intended_use: str
    source_id: str
    locator_id: str
    canonical_locator: str
    final_locator: str
    redirected: bool
    source_class: str
    privacy_class: str
    local_only: bool
    check_id: str
    run_id: str
    check_artifact_digest: str
    run_artifact_digest: str | None
    checked_at: str
    outcome: str
    http_status: int
    content_type: str | None
    content_size_bytes: int
    content_identity: str
    content_digest: str
    content_digest_algorithm: str
    body_included: bool
    body_retained_by_producer: bool
    consumer_must_supply_local_file: bool
    consumer_must_verify_exact_digest: bool
    authenticity_established: bool
    scientific_correctness_established: bool
    legal_retention_rights_established: bool
    redistribution_rights_established: bool
    retention_authorized: bool
    skill_derivation_authorized: bool
    candidate_approval_granted: bool
    registry_publication_authorized: bool
    external_publication_authorized: bool
    installation_authorized: bool
    execution_authorized: bool
    disclosure_authorized: bool
    gateway_approval_granted: bool
    operator_handoff_required: bool
    operator_derivation_invocation_required: bool

    def to_dict(self) -> dict[str, Any]:
        return {f.name: getattr(self, f.name) for f in fields(self)}

    def _identity_content(self) -> dict[str, Any]:
        return {k: v for k, v in self.to_dict().items() if k not in OSR_DIGEST_EXCLUDE}

    def validate(self) -> None:
        if self.schema_version != OSR_SCHEMA_VERSION:
            raise ObservedSourceReferenceError(f"unsupported schema: {self.schema_version!r}")
        if self.producer_system != PRODUCER_SYSTEM:
            raise ObservedSourceReferenceError("producer_system must be 'lighthouse'")
        if self.producer_contract != PRODUCER_CONTRACT:
            raise ObservedSourceReferenceError(f"producer_contract must be {PRODUCER_CONTRACT!r}")
        if self.deterministic_export_version != DETERMINISTIC_EXPORT_VERSION:
            raise ObservedSourceReferenceError(
                f"deterministic_export_version must be {DETERMINISTIC_EXPORT_VERSION!r}"
            )
        if self.canonicalization_version != CANONICALIZATION_VERSION:
            raise ObservedSourceReferenceError(
                f"canonicalization_version must be {CANONICALIZATION_VERSION!r}"
            )
        intended_use = _require_consumer(self.intended_consumer)
        if self.intended_use != intended_use:
            raise ObservedSourceReferenceError(
                "intended_use does not match intended_consumer profile"
            )
        if self.outcome != AVAILABLE:
            raise ObservedSourceReferenceError("only available checks may be exported")
        if self.source_class != SOURCE_CLASS:
            raise ObservedSourceReferenceError("source_class must be 'public_source'")
        if self.privacy_class != PRIVACY_CLASS:
            raise ObservedSourceReferenceError("privacy_class must be 'public'")
        if not (200 <= self.http_status <= 299):
            raise ObservedSourceReferenceError("http_status must be a 2xx response")
        if self.content_digest_algorithm != DIGEST_ALGORITHM:
            raise ObservedSourceReferenceError("content_digest_algorithm must be sha256")
        if not _HEX64_RE.fullmatch(self.content_digest):
            raise ObservedSourceReferenceError("content_digest must be a 64-char hex digest")
        if self.content_identity != f"content:{self.content_digest}":
            raise ObservedSourceReferenceError("content_identity must be content:+content_digest")
        if not _PSC_RE.fullmatch(self.check_id):
            raise ObservedSourceReferenceError("check_id must be psc_<sha256>")
        if not _PSR_RE.fullmatch(self.run_id):
            raise ObservedSourceReferenceError("run_id must be psr_<sha256>")
        if not _HEX64_RE.fullmatch(self.check_artifact_digest):
            raise ObservedSourceReferenceError("check_artifact_digest must be a 64-char hex digest")
        if self.run_artifact_digest is not None and not _HEX64_RE.fullmatch(
            self.run_artifact_digest
        ):
            raise ObservedSourceReferenceError("run_artifact_digest must be a 64-char hex digest")
        if not _PUBSOURCE_RE.fullmatch(self.source_id):
            raise ObservedSourceReferenceError("source_id must be public-source:<sha256>")
        if not _PUBLOCATOR_RE.fullmatch(self.locator_id):
            raise ObservedSourceReferenceError("locator_id must be public-locator:<sha256>")
        for name in ("canonical_locator", "final_locator"):
            value = getattr(self, name)
            if not isinstance(value, str) or not _HTTPS_RE.match(value) or len(value) > 2048:
                raise ObservedSourceReferenceError(f"{name} must be an https:// locator")
        if not isinstance(self.checked_at, str) or not _CHECKED_AT_RE.fullmatch(self.checked_at):
            raise ObservedSourceReferenceError("checked_at must be an ISO-8601 UTC timestamp")
        if not isinstance(self.content_size_bytes, int) or isinstance(
            self.content_size_bytes, bool
        ):
            raise ObservedSourceReferenceError("content_size_bytes must be a non-negative int")
        if self.content_size_bytes < 0:
            raise ObservedSourceReferenceError("content_size_bytes must be a non-negative int")
        if self.content_type is not None and (
            not isinstance(self.content_type, str) or len(self.content_type) > 128
        ):
            raise ObservedSourceReferenceError("content_type must be a short string or null")
        if self.producer_repository is not None and not isinstance(self.producer_repository, str):
            raise ObservedSourceReferenceError("producer_repository must be a string or null")
        if self.producer_commit is not None and (
            not isinstance(self.producer_commit, str)
            or not _COMMIT40_RE.fullmatch(self.producer_commit)
        ):
            raise ObservedSourceReferenceError(
                "producer_commit must be a 40-char hex digest or null"
            )
        for name, expected in {**AUTHORITY_PINS, **BODY_PINS, **OPERATOR_PINS}.items():
            if getattr(self, name) is not expected:
                raise ObservedSourceReferenceError(f"{name} must be {expected!r}")
        digest = _artifact_digest(self._identity_content())
        if self.artifact_digest != digest:
            raise ObservedSourceReferenceError("artifact_digest does not match content")
        if self.reference_id != REFERENCE_ID_PREFIX + digest:
            raise ObservedSourceReferenceError("reference_id does not match artifact_digest")
        if not _OSR_ID_RE.fullmatch(self.reference_id):
            raise ObservedSourceReferenceError("reference_id must be osr_<sha256>")


_ALLOWED_FIELDS = frozenset(f.name for f in fields(ObservedSourceReference))


def build_observed_source_reference(
    check: PublicSourceCheck,
    *,
    intended_consumer: str,
    run_artifact_digest: str | None = None,
    producer_repository: str | None = None,
    producer_commit: str | None = None,
) -> ObservedSourceReference:
    """Build one OSR from a validated available check. Refuses every other outcome."""
    check.validate()
    intended_use = _require_consumer(intended_consumer)
    if check.outcome != AVAILABLE:
        raise ObservedSourceReferenceError(
            f"only outcome {AVAILABLE!r} may be exported, not {check.outcome!r}"
        )
    if check.content_digest is None or check.body_size_bytes is None:
        raise ObservedSourceReferenceError("available checks must carry content identity")
    if check.http_status is None or not (200 <= check.http_status <= 299):
        raise ObservedSourceReferenceError("available checks must carry a 2xx http_status")

    ref = ObservedSourceReference(
        schema_version=OSR_SCHEMA_VERSION,
        reference_id="",
        artifact_digest="",
        producer_system=PRODUCER_SYSTEM,
        producer_contract=PRODUCER_CONTRACT,
        deterministic_export_version=DETERMINISTIC_EXPORT_VERSION,
        canonicalization_version=CANONICALIZATION_VERSION,
        producer_repository=producer_repository,
        producer_commit=producer_commit,
        intended_consumer=intended_consumer,
        intended_use=intended_use,
        source_id=check.source_id,
        locator_id=check.locator_id,
        canonical_locator=check.canonical_locator,
        final_locator=check.final_locator,
        redirected=check.redirected,
        source_class=SOURCE_CLASS,
        privacy_class=PRIVACY_CLASS,
        local_only=True,
        check_id=check.check_id,
        run_id=check.run_id,
        check_artifact_digest=check.artifact_digest,
        run_artifact_digest=run_artifact_digest,
        checked_at=check.checked_at,
        outcome=AVAILABLE,
        http_status=check.http_status,
        content_type=check.content_type,
        content_size_bytes=check.body_size_bytes,
        content_identity=f"content:{check.content_digest}",
        content_digest=check.content_digest,
        content_digest_algorithm=DIGEST_ALGORITHM,
        body_included=False,
        body_retained_by_producer=False,
        consumer_must_supply_local_file=True,
        consumer_must_verify_exact_digest=True,
        authenticity_established=False,
        scientific_correctness_established=False,
        legal_retention_rights_established=False,
        redistribution_rights_established=False,
        retention_authorized=False,
        skill_derivation_authorized=False,
        candidate_approval_granted=False,
        registry_publication_authorized=False,
        external_publication_authorized=False,
        installation_authorized=False,
        execution_authorized=False,
        disclosure_authorized=False,
        gateway_approval_granted=False,
        operator_handoff_required=True,
        operator_derivation_invocation_required=True,
    )
    digest = _artifact_digest(ref._identity_content())
    stamped = replace(ref, artifact_digest=digest, reference_id=REFERENCE_ID_PREFIX + digest)
    stamped.validate()
    return stamped


def from_dict(data: dict[str, Any]) -> ObservedSourceReference:
    if not isinstance(data, dict):
        raise ObservedSourceReferenceError("reference must be a JSON object")
    unknown = set(data) - _ALLOWED_FIELDS
    if unknown:
        raise ObservedSourceReferenceError(f"unknown field(s): {sorted(unknown)}")
    missing = _ALLOWED_FIELDS - set(data)
    if missing:
        raise ObservedSourceReferenceError(f"missing required field(s): {sorted(missing)}")
    try:
        ref = ObservedSourceReference(**{name: data[name] for name in _ALLOWED_FIELDS})
    except TypeError as exc:
        raise ObservedSourceReferenceError(f"invalid field types: {exc}") from exc
    ref.validate()
    return ref
