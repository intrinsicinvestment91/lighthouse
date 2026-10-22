"""Tests for the activation capability descriptor (LHP-PROFILE-14C).

The descriptor's whole product is honesty, so the tests are adversarial about over-claiming:
it must be impossible to reach "activation is available" by editing a value, re-stamping a
digest, or smuggling a Gateway-owned policy field into a provider self-description.
"""

import hashlib
import json
import subprocess
import sys
from dataclasses import FrozenInstanceError, replace
from pathlib import Path
from typing import Any

import pytest

from modules.activation_capability import (
    ACTIVATION_CAPABILITY_SCHEMA,
    ACTIVATION_CAPABILITY_SCHEMA_VERSION,
    ALLOWED_IMPLEMENTATION_STATES,
    CANONICAL_CONTRACT_REFERENCES,
    CANONICAL_DECLARED_OPERATIONS,
    CANONICAL_LIMITATIONS,
    CANONICAL_REQUIRED_EVIDENCE,
    CANONICAL_SECURITY_CONSTRAINTS,
    CAPABILITY_ID,
    GATEWAY_ACTIVE_PROFILE_STORE,
    GATEWAY_POST_SWITCH_VALIDATION,
    GATEWAY_PROFILE_ACTIVATION_ENDPOINT,
    NOT_IMPLEMENTED,
    PROVIDER_ID,
    PROVIDER_TYPE,
    ROLE_ACTIVE_PROFILE_RECORD,
    ROLE_REVIEW_ARTIFACT,
    ROLE_TRANSITION_REFERENCE,
    ActivationCapabilityDescriptor,
    ActivationCapabilityError,
    ContractReference,
    DeclaredOperation,
    capability_evidence_reference,
    describe_activation_capability,
)
from modules.activation_intent import ACTIVATION_INTENT_SCHEMA_VERSION
from modules.active_profile_state import ACTIVE_PROFILE_STATE_SCHEMA_VERSION
from modules.profile_transition import PROFILE_TRANSITION_PREVIEW_SCHEMA_VERSION

VERSION = "0.1.0"
COMMIT = "a" * 40


def _descriptor(**overrides: Any) -> ActivationCapabilityDescriptor:
    """A deterministic canonical descriptor (explicit stamps — no detection, no subprocess)."""
    fields: dict[str, Any] = {"provider_version": VERSION, "provider_commit": COMMIT}
    fields.update(overrides)
    descriptor = ActivationCapabilityDescriptor(**fields)
    descriptor.artifact_digest = descriptor.compute_digest()
    return descriptor


def _payload(**overrides: Any) -> dict[str, Any]:
    data = _descriptor().to_dict()
    data.update(overrides)
    return data


def _restamped(data: dict[str, Any]) -> dict[str, Any]:
    """Re-stamp a mutated payload's digest so it is internally self-consistent.

    A tamperer who controls the bytes also controls the digest — so every anti-over-claim rule
    must hold WITHOUT relying on the digest. These tests use this to prove exactly that.
    """
    content = {k: v for k, v in data.items() if k != "artifact_digest"}
    canonical = json.dumps(content, sort_keys=True, separators=(",", ":"))
    data["artifact_digest"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return data


# --------------------------------------------------------------------------------------
# The descriptor validates, and says what it must say
# --------------------------------------------------------------------------------------


def test_canonical_descriptor_validates() -> None:
    _descriptor().validate()


def test_descriptor_declares_activation_not_implemented() -> None:
    descriptor = _descriptor()
    assert descriptor.implementation_state == NOT_IMPLEMENTED
    assert descriptor.activation_available is False


def test_no_declared_operation_claims_to_be_implemented() -> None:
    for operation in CANONICAL_DECLARED_OPERATIONS:
        assert operation.implementation_state == NOT_IMPLEMENTED


def test_implementation_state_vocabulary_cannot_express_availability() -> None:
    # The single-member vocabulary is the point: there is no word for "available" to set.
    assert frozenset({NOT_IMPLEMENTED}) == ALLOWED_IMPLEMENTATION_STATES


def test_descriptor_roundtrips_through_dict() -> None:
    descriptor = _descriptor()
    restored = ActivationCapabilityDescriptor.from_dict(descriptor.to_dict())
    assert restored.to_dict() == descriptor.to_dict()
    assert restored.artifact_digest == descriptor.artifact_digest


def test_to_dict_validates_first() -> None:
    descriptor = _descriptor()
    descriptor.implementation_state = "available"
    with pytest.raises(ActivationCapabilityError):
        descriptor.to_dict()


def test_descriptor_serializes_the_expected_key_set() -> None:
    assert set(_descriptor().to_dict()) == {
        "schema_version",
        "capability_id",
        "capability_version",
        "provider_type",
        "provider_id",
        "provider_version",
        "provider_commit",
        "implementation_state",
        "activation_available",
        "declared_operations",
        "contract_references",
        "required_evidence",
        "limitations",
        "security_constraints",
        "artifact_digest",
    }


# --------------------------------------------------------------------------------------
# Unknown capability states fail — including when the digest is re-stamped to match
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "state",
    ["available", "implemented", "ready", "enabled", "", "not_implemented ", "NOT_IMPLEMENTED"],
)
def test_unknown_implementation_state_fails(state: str) -> None:
    with pytest.raises(ActivationCapabilityError, match="implementation_state"):
        ActivationCapabilityDescriptor.from_dict(_restamped(_payload(implementation_state=state)))


def test_restamped_available_claim_still_fails() -> None:
    """You cannot digest your way to "available" — the vocabulary has no such word."""
    payload = _restamped(_payload(implementation_state="available"))
    # The payload is internally consistent: its digest matches its (tampered) content.
    descriptor = ActivationCapabilityDescriptor(
        provider_version=VERSION, provider_commit=COMMIT, implementation_state="available"
    )
    assert payload["artifact_digest"] == descriptor.compute_digest()
    with pytest.raises(ActivationCapabilityError, match="cannot express an available"):
        ActivationCapabilityDescriptor.from_dict(payload)


def test_restamped_operation_state_claim_still_fails() -> None:
    payload = _payload()
    payload["declared_operations"][0]["implementation_state"] = "available"
    with pytest.raises(ActivationCapabilityError, match="requires a new contract"):
        ActivationCapabilityDescriptor.from_dict(_restamped(payload))


@pytest.mark.parametrize("value", [True, 1, "false", "False", None, 0.0])
def test_activation_available_is_identity_pinned_to_false(value: Any) -> None:
    with pytest.raises(ActivationCapabilityError, match="constant False"):
        ActivationCapabilityDescriptor.from_dict(_restamped(_payload(activation_available=value)))


def test_activation_available_zero_is_not_false_enough() -> None:
    # 0 == False in Python; identity checking is what makes the pin real.
    with pytest.raises(ActivationCapabilityError, match="constant False"):
        ActivationCapabilityDescriptor.from_dict(_restamped(_payload(activation_available=0)))


# --------------------------------------------------------------------------------------
# Malformed descriptors fail
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("version", [0, 2, -1, True, "1", 1.0, None])
def test_bad_schema_version_fails(version: Any) -> None:
    with pytest.raises(ActivationCapabilityError, match="schema_version"):
        ActivationCapabilityDescriptor.from_dict(_restamped(_payload(schema_version=version)))


@pytest.mark.parametrize(
    "capability_id",
    ["lighthouse.profile_activation_endpoint", "lighthouse.other", "", None],
)
def test_wrong_capability_id_fails(capability_id: Any) -> None:
    with pytest.raises(ActivationCapabilityError, match="capability_id"):
        ActivationCapabilityDescriptor.from_dict(_restamped(_payload(capability_id=capability_id)))


@pytest.mark.parametrize("version", [0, -1, True, "1", None])
def test_bad_capability_version_fails(version: Any) -> None:
    with pytest.raises(ActivationCapabilityError, match="capability_version"):
        ActivationCapabilityDescriptor.from_dict(_restamped(_payload(capability_version=version)))


@pytest.mark.parametrize("provider_type", ["gateway", "operator", "", None])
def test_wrong_provider_type_fails(provider_type: Any) -> None:
    with pytest.raises(ActivationCapabilityError, match="provider_type"):
        ActivationCapabilityDescriptor.from_dict(_restamped(_payload(provider_type=provider_type)))


@pytest.mark.parametrize("provider_id", ["lighthouse", "external-operator-gateway", "", None])
def test_wrong_provider_id_fails(provider_id: Any) -> None:
    with pytest.raises(ActivationCapabilityError, match="provider_id"):
        ActivationCapabilityDescriptor.from_dict(_restamped(_payload(provider_id=provider_id)))


@pytest.mark.parametrize("field", ["provider_version", "provider_commit"])
@pytest.mark.parametrize("value", ["", "   ", 7, []])
def test_bad_provenance_stamp_fails(field: str, value: Any) -> None:
    with pytest.raises(ActivationCapabilityError, match=field):
        ActivationCapabilityDescriptor.from_dict(_restamped(_payload(**{field: value})))


@pytest.mark.parametrize("field", ["provider_version", "provider_commit"])
def test_absent_provenance_stamp_is_allowed(field: str) -> None:
    # Deployed images ship without package metadata or a git checkout: None is honest.
    descriptor = _descriptor(**{field: None})
    descriptor.validate()


def test_empty_declared_operations_fails() -> None:
    with pytest.raises(ActivationCapabilityError, match="declared_operations"):
        ActivationCapabilityDescriptor.from_dict(_restamped(_payload(declared_operations=[])))


def test_empty_contract_references_fails() -> None:
    with pytest.raises(ActivationCapabilityError, match="contract_references"):
        ActivationCapabilityDescriptor.from_dict(_restamped(_payload(contract_references=[])))


def test_duplicate_operation_id_fails() -> None:
    payload = _payload()
    payload["declared_operations"].append(dict(payload["declared_operations"][0]))
    with pytest.raises(ActivationCapabilityError, match="duplicate"):
        ActivationCapabilityDescriptor.from_dict(_restamped(payload))


def test_duplicate_gateway_capability_id_fails() -> None:
    payload = _payload()
    clone = dict(payload["declared_operations"][0])
    clone["operation_id"] = "validate_post_switch"
    payload["declared_operations"] = [payload["declared_operations"][0], clone]
    with pytest.raises(ActivationCapabilityError, match="duplicate gateway_capability_id"):
        ActivationCapabilityDescriptor.from_dict(_restamped(payload))


def test_unknown_operation_id_fails() -> None:
    payload = _payload()
    payload["declared_operations"][0]["operation_id"] = "activate_now"
    with pytest.raises(ActivationCapabilityError, match="operation_id"):
        ActivationCapabilityDescriptor.from_dict(_restamped(payload))


@pytest.mark.parametrize(
    "capability_id",
    ["lighthouse.profile_activation", "lighthouse.production_restore", "gateway.lighthouse_bridge"],
)
def test_unknown_gateway_capability_id_fails(capability_id: str) -> None:
    # Notably: the Lighthouse-side umbrella id is NOT a Gateway capability id.
    payload = _payload()
    payload["declared_operations"][0]["gateway_capability_id"] = capability_id
    with pytest.raises(ActivationCapabilityError, match="gateway_capability_id"):
        ActivationCapabilityDescriptor.from_dict(_restamped(payload))


def test_operation_with_empty_inputs_fails() -> None:
    payload = _payload()
    payload["declared_operations"][0]["required_inputs"] = []
    with pytest.raises(ActivationCapabilityError, match="required_inputs"):
        ActivationCapabilityDescriptor.from_dict(_restamped(payload))


def test_operation_with_non_bool_mutates_state_fails() -> None:
    payload = _payload()
    payload["declared_operations"][0]["mutates_state"] = "yes"
    with pytest.raises(ActivationCapabilityError, match="mutates_state"):
        ActivationCapabilityDescriptor.from_dict(_restamped(payload))


@pytest.mark.parametrize(
    "field",
    ["required_evidence", "limitations", "security_constraints"],
)
def test_unknown_vocabulary_token_fails(field: str) -> None:
    with pytest.raises(ActivationCapabilityError, match="closed vocabulary"):
        ActivationCapabilityDescriptor.from_dict(
            _restamped(_payload(**{field: ["activation_is_totally_fine"]}))
        )


@pytest.mark.parametrize(
    ("field", "token"),
    [
        ("required_evidence", "verified_backup_manifest"),
        ("limitations", "activation_not_implemented"),
        ("security_constraints", "exactly_one_active_profile"),
    ],
)
def test_duplicate_vocabulary_token_fails(field: str, token: str) -> None:
    with pytest.raises(ActivationCapabilityError, match="duplicate"):
        ActivationCapabilityDescriptor.from_dict(_restamped(_payload(**{field: [token, token]})))


@pytest.mark.parametrize("field", ["required_evidence", "limitations", "security_constraints"])
def test_empty_vocabulary_tuple_fails(field: str) -> None:
    with pytest.raises(ActivationCapabilityError, match=field):
        ActivationCapabilityDescriptor.from_dict(_restamped(_payload(**{field: []})))


@pytest.mark.parametrize("payload", [None, [], "descriptor", 7])
def test_non_object_payload_fails(payload: Any) -> None:
    with pytest.raises(ActivationCapabilityError, match="must be an object"):
        ActivationCapabilityDescriptor.from_dict(payload)


def test_missing_keys_fail() -> None:
    payload = _payload()
    del payload["limitations"]
    with pytest.raises(ActivationCapabilityError, match="missing required keys"):
        ActivationCapabilityDescriptor.from_dict(payload)


def test_unexpected_keys_fail() -> None:
    with pytest.raises(ActivationCapabilityError, match="unexpected keys"):
        ActivationCapabilityDescriptor.from_dict(_payload(activation_endpoint_url="http://x"))


def test_unexpected_operation_key_fails() -> None:
    payload = _payload()
    payload["declared_operations"][0]["authorized"] = True
    with pytest.raises(ActivationCapabilityError, match="unexpected keys"):
        ActivationCapabilityDescriptor.from_dict(_restamped(payload))


@pytest.mark.parametrize("digest", ["", "abc", "A" * 64, "z" * 64, None, 1])
def test_malformed_digest_fails(digest: Any) -> None:
    with pytest.raises(ActivationCapabilityError, match="artifact_digest"):
        ActivationCapabilityDescriptor.from_dict(_payload(artifact_digest=digest))


# --------------------------------------------------------------------------------------
# Contract references — the packet's three artifacts, pinned to their live schema versions
# --------------------------------------------------------------------------------------


def test_descriptor_references_the_three_contracts() -> None:
    by_role = {ref.role: ref.contract for ref in CANONICAL_CONTRACT_REFERENCES}
    assert by_role == {
        ROLE_REVIEW_ARTIFACT: "profile_transition_preview.v1",
        ROLE_TRANSITION_REFERENCE: "activation_intent.v1",
        ROLE_ACTIVE_PROFILE_RECORD: "active_profile_state.v1",
    }


def test_contract_references_track_the_live_schema_versions() -> None:
    by_role = {ref.role: ref.schema_version for ref in CANONICAL_CONTRACT_REFERENCES}
    assert by_role[ROLE_REVIEW_ARTIFACT] == PROFILE_TRANSITION_PREVIEW_SCHEMA_VERSION
    assert by_role[ROLE_TRANSITION_REFERENCE] == ACTIVATION_INTENT_SCHEMA_VERSION
    assert by_role[ROLE_ACTIVE_PROFILE_RECORD] == ACTIVE_PROFILE_STATE_SCHEMA_VERSION


def test_stale_contract_schema_version_fails() -> None:
    """A contract bump that forgets this descriptor must fail loudly, not ship a stale claim."""
    payload = _payload()
    payload["contract_references"][0]["schema_version"] = (
        PROFILE_TRANSITION_PREVIEW_SCHEMA_VERSION + 1
    )
    with pytest.raises(ActivationCapabilityError, match="stale"):
        ActivationCapabilityDescriptor.from_dict(_restamped(payload))


def test_contract_reference_with_wrong_contract_for_role_fails() -> None:
    payload = _payload()
    payload["contract_references"][0]["contract"] = "activation_intent.v1"
    with pytest.raises(ActivationCapabilityError, match="must reference"):
        ActivationCapabilityDescriptor.from_dict(_restamped(payload))


def test_unknown_contract_role_fails() -> None:
    payload = _payload()
    payload["contract_references"][0]["role"] = "authorization"
    with pytest.raises(ActivationCapabilityError, match="role"):
        ActivationCapabilityDescriptor.from_dict(_restamped(payload))


def test_duplicate_contract_role_fails() -> None:
    payload = _payload()
    payload["contract_references"].append(dict(payload["contract_references"][0]))
    with pytest.raises(ActivationCapabilityError, match="duplicate contract role"):
        ActivationCapabilityDescriptor.from_dict(_restamped(payload))


def test_outputs_without_a_contract_reference_are_the_declared_gaps() -> None:
    """activation_result and validation_report have no Lighthouse contract yet — and say so."""
    roles = {ref.role for ref in CANONICAL_CONTRACT_REFERENCES}
    outputs = {out for op in CANONICAL_DECLARED_OPERATIONS for out in op.produced_outputs}
    assert outputs - roles == {"activation_result", "validation_report"}
    assert "active_profile_record" in (outputs & roles)


# --------------------------------------------------------------------------------------
# Cross-repo pins — the Gateway is the consumer; its ids and bounds are load-bearing
# --------------------------------------------------------------------------------------


def test_declared_operations_map_to_the_canonical_gateway_capability_ids() -> None:
    assert {op.gateway_capability_id for op in CANONICAL_DECLARED_OPERATIONS} == {
        "lighthouse.profile_activation_endpoint",
        "lighthouse.active_profile_store",
        "lighthouse.post_switch_validation",
    }


def test_activation_endpoint_id_keeps_its_endpoint_suffix() -> None:
    # The bare "lighthouse.profile_activation" appears nowhere in the Gateway's registry; it is
    # the Lighthouse-side umbrella id only. Dropping the suffix would orphan every attestation.
    assert GATEWAY_PROFILE_ACTIVATION_ENDPOINT == "lighthouse.profile_activation_endpoint"
    assert CAPABILITY_ID != GATEWAY_PROFILE_ACTIVATION_ENDPOINT


@pytest.mark.parametrize(
    ("gateway_capability_id", "mutates", "inputs", "outputs"),
    [
        (
            GATEWAY_PROFILE_ACTIVATION_ENDPOINT,
            True,
            ("target_profile_id", "target_profile_version", "transition_reference"),
            ("activation_result",),
        ),
        (
            GATEWAY_ACTIVE_PROFILE_STORE,
            True,
            ("profile_id", "profile_version"),
            ("active_profile_record",),
        ),
        (
            GATEWAY_POST_SWITCH_VALIDATION,
            False,
            ("expected_profile_id",),
            ("validation_report",),
        ),
    ],
)
def test_operations_mirror_the_gateway_definitions(
    gateway_capability_id: str,
    mutates: bool,
    inputs: tuple[str, ...],
    outputs: tuple[str, ...],
) -> None:
    """Field-for-field agreement with the Gateway capability definitions (read-only audit)."""
    operation = next(
        op
        for op in CANONICAL_DECLARED_OPERATIONS
        if op.gateway_capability_id == gateway_capability_id
    )
    assert operation.mutates_state is mutates
    assert operation.required_inputs == inputs
    assert operation.produced_outputs == outputs


def test_provider_identity_matches_the_gateway_expectation() -> None:
    # The Gateway pins EXPECTED_LIGHTHOUSE_REPOSITORY = "pattern-match-sentinel" and its
    # attestation provider_type Literal includes "lighthouse".
    assert PROVIDER_ID == "pattern-match-sentinel"
    assert PROVIDER_TYPE == "lighthouse"


def test_evidence_reference_matches_the_gateway_convention() -> None:
    descriptor = _descriptor()
    reference = capability_evidence_reference(descriptor)
    assert reference == (
        f"{ACTIVATION_CAPABILITY_SCHEMA}:{CAPABILITY_ID}:{descriptor.artifact_digest}"
    )
    schema, capability, digest = reference.split(":")
    assert schema == "lighthouse_activation_capability.v1"
    assert capability == CAPABILITY_ID
    assert digest == descriptor.artifact_digest
    assert len(reference) <= 512  # Gateway MAX_EVIDENCE_REFERENCE_LENGTH


def test_limitations_fit_the_gateway_restrictions_bounds() -> None:
    # A future attestation carries these as `restrictions`: <=16 entries of <=256 chars.
    assert len(CANONICAL_LIMITATIONS) <= 16
    assert all(len(token) <= 256 for token in CANONICAL_LIMITATIONS)


GATEWAY_OWNED_POLICY_KEYS = frozenset(
    {
        "minimum_trust_level",
        "trust_level",
        "verification_status",
        "availability_status",
        "attestation_status",
        "freshness_required",
        "maximum_age_seconds",
        "expires_at",
        "repo_audit_status",
        "required_for_execution",
        "required_for_authorization",
        "definition_digest",
        "capability_definition_digest",
        "grants_execution_authority",
        "authorizes_execution",
        "executes",
        "status",
    }
)


def _all_keys(value: Any) -> set[str]:
    keys: set[str] = set()
    if isinstance(value, dict):
        for key, child in value.items():
            keys.add(key)
            keys |= _all_keys(child)
    elif isinstance(value, list):
        for child in value:
            keys |= _all_keys(child)
    return keys


def test_descriptor_claims_no_gateway_owned_policy() -> None:
    """No duplicate authority: a provider describes itself, it never grades itself.

    Trust floors, freshness windows, lifecycle status and audit status are Gateway-owned. A
    descriptor that carried them would be a second, competing capability registry.
    """
    assert _all_keys(_descriptor().to_dict()) & GATEWAY_OWNED_POLICY_KEYS == set()


# --------------------------------------------------------------------------------------
# Deterministic digest behavior
# --------------------------------------------------------------------------------------


def test_digest_is_deterministic() -> None:
    assert _descriptor().artifact_digest == _descriptor().artifact_digest


def test_digest_is_stable_across_equivalent_builds() -> None:
    first = describe_activation_capability(provider_version=VERSION, provider_commit=COMMIT)
    second = describe_activation_capability(provider_version=VERSION, provider_commit=COMMIT)
    assert first.artifact_digest == second.artifact_digest
    assert first.to_dict() == second.to_dict()


def test_different_provenance_yields_a_different_digest() -> None:
    first = describe_activation_capability(provider_version=VERSION, provider_commit=COMMIT)
    second = describe_activation_capability(provider_version="9.9.9", provider_commit="b" * 40)
    assert first.artifact_digest != second.artifact_digest


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schema_version", 2),
        ("capability_id", "lighthouse.something_else"),
        ("capability_version", 2),
        ("provider_type", "gateway"),
        ("provider_id", "external-operator-gateway"),
        ("provider_version", "9.9.9"),
        ("provider_commit", "b" * 40),
        ("implementation_state", "available"),
        ("activation_available", True),
        ("declared_operations", CANONICAL_DECLARED_OPERATIONS[:2]),
        ("contract_references", CANONICAL_CONTRACT_REFERENCES[:2]),
        ("required_evidence", CANONICAL_REQUIRED_EVIDENCE[:2]),
        ("limitations", CANONICAL_LIMITATIONS[:2]),
        ("security_constraints", CANONICAL_SECURITY_CONSTRAINTS[:2]),
    ],
)
def test_every_content_field_is_digest_covered(field: str, value: Any) -> None:
    base = _descriptor()
    assert getattr(base, field) != value
    mutated = replace(base, **{field: value})
    assert mutated.compute_digest() != base.compute_digest()


def test_token_order_is_digest_covered() -> None:
    base = _descriptor()
    reordered = replace(base, limitations=tuple(reversed(CANONICAL_LIMITATIONS)))
    assert reordered.compute_digest() != base.compute_digest()


def test_tampered_content_with_a_stale_digest_fails() -> None:
    payload = _payload(provider_version="9.9.9")  # digest still describes the old version
    with pytest.raises(ActivationCapabilityError, match="artifact_digest mismatch"):
        ActivationCapabilityDescriptor.from_dict(payload)


def test_reordered_limitations_with_a_stale_digest_fails() -> None:
    payload = _payload(limitations=list(reversed(CANONICAL_LIMITATIONS)))
    with pytest.raises(ActivationCapabilityError, match="artifact_digest mismatch"):
        ActivationCapabilityDescriptor.from_dict(payload)


# --------------------------------------------------------------------------------------
# The factory, and the absence of any activation surface
# --------------------------------------------------------------------------------------


def test_factory_produces_a_valid_canonical_descriptor() -> None:
    descriptor = describe_activation_capability(provider_version=VERSION, provider_commit=COMMIT)
    descriptor.validate()
    assert descriptor.capability_id == CAPABILITY_ID
    assert descriptor.schema_version == ACTIVATION_CAPABILITY_SCHEMA_VERSION
    assert descriptor.declared_operations == CANONICAL_DECLARED_OPERATIONS
    assert descriptor.contract_references == CANONICAL_CONTRACT_REFERENCES


def test_factory_detects_provenance_when_not_supplied() -> None:
    # Best-effort: both stamps are None in a deployed image, and that is honest, not an error.
    descriptor = describe_activation_capability()
    descriptor.validate()
    assert descriptor.provider_version is None or isinstance(descriptor.provider_version, str)
    assert descriptor.provider_commit is None or isinstance(descriptor.provider_commit, str)


def test_describing_the_capability_writes_nothing(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.chdir(tmp_path)
    describe_activation_capability(provider_version=VERSION, provider_commit=COMMIT)
    capability_evidence_reference(_descriptor())
    assert list(tmp_path.iterdir()) == []


FORBIDDEN_SURFACE = (
    "activate",
    "activate_profile",
    "switch_profile",
    "set_active_profile",
    "write_active_profile",
    "request_activation",
    "execute",
    "dispatch",
    "persist",
    "save",
    "store",
)


@pytest.mark.parametrize("name", FORBIDDEN_SURFACE)
def test_module_exposes_no_activation_surface(name: str) -> None:
    import modules.activation_capability as module

    assert not hasattr(module, name)


def test_module_declares_no_direct_persistence_or_network_imports() -> None:
    # The provenance stamps are the only borrowed code (read-only helpers from backup_manifest);
    # the descriptor itself never reaches a database, a socket, or the filesystem.
    repo_root = Path(__file__).resolve().parents[2]
    source = (repo_root / "modules" / "activation_capability.py").read_text(encoding="utf-8")
    for forbidden in ("modules.db", "sqlite3", "httpx", "requests", "websockets", "subprocess"):
        assert f"import {forbidden}" not in source
        assert f"from {forbidden}" not in source


def test_importing_the_module_has_no_side_effects(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [sys.executable, "-c", "import modules.activation_capability"],
        cwd=tmp_path,
        env={
            "PYTHONPATH": str(repo_root),
            "PATH": "/usr/bin:/bin",
            "PYTHONDONTWRITEBYTECODE": "1",
        },
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert list(tmp_path.iterdir()) == []


def test_declared_operation_and_reference_records_are_immutable() -> None:
    operation = CANONICAL_DECLARED_OPERATIONS[0]
    reference = CANONICAL_CONTRACT_REFERENCES[0]
    with pytest.raises(FrozenInstanceError):
        operation.implementation_state = "available"  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        reference.schema_version = 99  # type: ignore[misc]


def test_declared_operation_rejects_a_non_object() -> None:
    with pytest.raises(ActivationCapabilityError, match="must be an object"):
        DeclaredOperation.from_dict("request_activation")


def test_contract_reference_rejects_a_non_object() -> None:
    with pytest.raises(ActivationCapabilityError, match="must be an object"):
        ContractReference.from_dict(["activation_intent.v1"])
