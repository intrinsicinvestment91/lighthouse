"""Activation capability descriptor — ``lighthouse_activation_capability.v1`` (LHP-PROFILE-14C).

Answers exactly one question, machine-readably: **what activation capability does Lighthouse
claim to provide?** Today's honest answer is "none yet" — and this contract states that as a
closed, digest-covered fact rather than as prose scattered across ADRs.

This is a **provider self-description**. It is NOT a Gateway capability *definition* (which owns
trust floors, freshness windows, lifecycle status, and `repo_audit_status`) and NOT an
*attestation* (which the Gateway issues about a provider). Lighthouse describes itself; the
Gateway defines what it requires and decides whether to trust the claim. Keeping those apart is
what prevents duplicate authority (ADR-0017, ADR-0001).

Contract rules (fail closed):

- ``implementation_state`` is a **closed vocabulary whose only member in v1 is
  ``"not_implemented"``**. This is the packet's ``activation_available = false`` expressed as a
  state rather than a flippable boolean: the vocabulary cannot express "available", so claiming
  availability is a deliberate contract change (new member + new capability), never a value
  edit. ``"available"``/``"implemented"`` are rejected even if the digest is re-stamped.
- ``activation_available`` is additionally pinned to the constant ``False`` and checked by
  identity (``is not False``), so ``True``/``1``/``"false"``/``None`` all fail. This mirrors the
  Gateway's ``Literal[False]`` authority pins and gives a future authorization packet a single
  refusal hook.
- ``artifact_digest`` (SHA-256 over the canonical content, excluding only itself) is recomputed
  by ``validate()``. The descriptor carries no volatile fields — no id, no timestamp — so it is
  a pure function of its content and its digest is stable across builds with the same stamps.
- Each declared operation maps 1:1 onto a canonical **Gateway** capability id (the ``_endpoint``
  suffix on the activation endpoint is load-bearing: the bare id does not exist Gateway-side)
  and mirrors that definition's ``mutating_capability`` / ``required_inputs`` /
  ``guaranteed_outputs`` field for field, so a future attestation can cross-check the provider's
  claim against the Gateway's definition.
- A declared output with no matching ``contract_references`` role is a machine-discoverable gap:
  ``active_profile_record`` has a Lighthouse contract (``active_profile_state.v1``);
  ``activation_result`` and ``validation_report`` do not exist yet, and this artifact says so.

Field consumers (packet design rule — no consumer, no field). Future Gateway capability
attestation: ``capability_id``/``capability_version`` (join), ``provider_type``/``provider_id``/
``provider_version``/``provider_commit`` (its provider fields; ``provider_id`` equals the
Gateway's pinned ``EXPECTED_LIGHTHOUSE_REPOSITORY``), ``implementation_state`` (maps to
``availability_status: "unavailable"``), ``limitations`` (its bounded ``restrictions``),
``artifact_digest`` (its ``evidence_reference``). Future authorization: ``activation_available``
(refusal), ``required_evidence``, ``security_constraints``. Future execution planning:
``declared_operations`` (inputs/outputs per capability) and ``contract_references`` (what to put
in the activation endpoint's ``transition_reference`` input). Audit: all of the above.

**Nothing here activates, switches, stores, executes, dispatches, or contacts the Gateway.** A
description of a capability is not a capability.
"""

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any

from modules.activation_intent import ACTIVATION_INTENT_SCHEMA_VERSION
from modules.active_profile_state import ACTIVE_PROFILE_STATE_SCHEMA_VERSION
from modules.backup_manifest import detect_lighthouse_version, detect_repository_commit
from modules.profile_transition import PROFILE_TRANSITION_PREVIEW_SCHEMA_VERSION

ACTIVATION_CAPABILITY_SCHEMA_VERSION = 1
ACTIVATION_CAPABILITY_SCHEMA = "lighthouse_activation_capability.v1"

# Identity of the capability this descriptor speaks for. Lighthouse-side umbrella name: the
# activation surface as a whole. It is NOT a Gateway capability id — those are per-operation
# (see GATEWAY_* below), and a future attestation must be keyed on those, not on this.
CAPABILITY_ID = "lighthouse.profile_activation"
CAPABILITY_VERSION = 1

# Provider identity, in the Gateway's own vocabulary. PROVIDER_ID is the value the Gateway pins
# as EXPECTED_LIGHTHOUSE_REPOSITORY and expects in an attestation's provider_id/provider_repository.
PROVIDER_TYPE = "lighthouse"
PROVIDER_ID = "pattern-match-sentinel"

# Closed state vocabulary. v1 has exactly one member: nothing is implemented. Adding a member is
# a contract change that must land WITH the capability it describes — never before it.
NOT_IMPLEMENTED = "not_implemented"
ALLOWED_IMPLEMENTATION_STATES = frozenset({NOT_IMPLEMENTED})

# Canonical Gateway capability ids, verbatim from the Gateway's definition registry (all three
# carry repo_audit_status="missing" there). The `_endpoint` suffix is exact: the bare id
# "lighthouse.profile_activation" appears nowhere Gateway-side.
GATEWAY_PROFILE_ACTIVATION_ENDPOINT = "lighthouse.profile_activation_endpoint"
GATEWAY_ACTIVE_PROFILE_STORE = "lighthouse.active_profile_store"
GATEWAY_POST_SWITCH_VALIDATION = "lighthouse.post_switch_validation"
ALLOWED_GATEWAY_CAPABILITY_IDS = frozenset(
    {
        GATEWAY_PROFILE_ACTIVATION_ENDPOINT,
        GATEWAY_ACTIVE_PROFILE_STORE,
        GATEWAY_POST_SWITCH_VALIDATION,
    }
)

OPERATION_REQUEST_ACTIVATION = "request_activation"
OPERATION_RECORD_ACTIVE_PROFILE = "record_active_profile"
OPERATION_VALIDATE_POST_SWITCH = "validate_post_switch"
ALLOWED_OPERATION_IDS = frozenset(
    {
        OPERATION_REQUEST_ACTIVATION,
        OPERATION_RECORD_ACTIVE_PROFILE,
        OPERATION_VALIDATE_POST_SWITCH,
    }
)

# Contract roles, named for the Gateway input/output they satisfy.
ROLE_REVIEW_ARTIFACT = "review_artifact"
ROLE_TRANSITION_REFERENCE = "transition_reference"
ROLE_ACTIVE_PROFILE_RECORD = "active_profile_record"
ALLOWED_CONTRACT_ROLES = frozenset(
    {ROLE_REVIEW_ARTIFACT, ROLE_TRANSITION_REFERENCE, ROLE_ACTIVE_PROFILE_RECORD}
)

CONTRACT_TRANSITION_PREVIEW = "profile_transition_preview.v1"
CONTRACT_ACTIVATION_INTENT = "activation_intent.v1"
CONTRACT_ACTIVE_PROFILE_STATE = "active_profile_state.v1"

# Runtime drift pin: the expected (contract, schema_version) pairs are derived from the IMPORTED
# constants, so a future contract bump that forgets this descriptor fails validation loudly
# instead of silently shipping a stale claim.
_EXPECTED_CONTRACTS: dict[str, tuple[str, int]] = {
    ROLE_REVIEW_ARTIFACT: (
        CONTRACT_TRANSITION_PREVIEW,
        PROFILE_TRANSITION_PREVIEW_SCHEMA_VERSION,
    ),
    ROLE_TRANSITION_REFERENCE: (
        CONTRACT_ACTIVATION_INTENT,
        ACTIVATION_INTENT_SCHEMA_VERSION,
    ),
    ROLE_ACTIVE_PROFILE_RECORD: (
        CONTRACT_ACTIVE_PROFILE_STATE,
        ACTIVE_PROFILE_STATE_SCHEMA_VERSION,
    ),
}

# What the capability would require before it would act. Mirrors the gates the shipped
# activation-preconditions evaluator already enforces (LHP-PROFILE-14B).
ALLOWED_REQUIRED_EVIDENCE = frozenset(
    {
        "verified_backup_manifest",
        "passing_backup_verification_report",
        "completed_restore_drill",
        "ready_transition_preview",
        "ready_activation_intent",
    }
)

# What the capability cannot do — starting with "exist".
ALLOWED_LIMITATIONS = frozenset(
    {
        "activation_not_implemented",
        "no_activation_endpoint_exists",
        "no_active_profile_store_exists",
        "no_post_switch_validation_exists",
        "production_restore_not_provided",
        "restore_drill_proves_isolated_restore_only",
        "no_rollback_target_until_activation_exists",
    }
)

# Invariants any future activation must preserve. Each maps to a shipped Lighthouse enforcement
# and to a Gateway definition token.
ALLOWED_SECURITY_CONSTRAINTS = frozenset(
    {
        "operator_approved_transition_required",
        "exactly_one_active_profile",
        "single_activation_request_no_blind_retry",
        "fail_closed_on_ambiguous_state",
        "verified_backup_required_before_activation",
        "intent_and_approval_never_authorize_activation",
    }
)

_SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")

_OPERATION_KEYS = frozenset(
    {
        "operation_id",
        "gateway_capability_id",
        "implementation_state",
        "mutates_state",
        "required_inputs",
        "produced_outputs",
    }
)
_REFERENCE_KEYS = frozenset({"contract", "schema_version", "role"})
_EXPECTED_KEYS = frozenset(
    {
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
)


class ActivationCapabilityError(Exception):
    """Raised when a capability descriptor is malformed, over-claiming, or tampered."""


@dataclass(frozen=True)
class DeclaredOperation:
    """One operation the activation capability would provide, mapped to its Gateway capability.

    Every field mirrors the corresponding Gateway capability definition field
    (``mutating_capability``, ``required_inputs``, ``guaranteed_outputs``), so a future
    attestation can cross-check the provider's claim against the definition it is attesting to.
    """

    operation_id: str
    gateway_capability_id: str
    implementation_state: str
    mutates_state: bool
    required_inputs: tuple[str, ...]
    produced_outputs: tuple[str, ...]

    def validate(self) -> None:
        if self.operation_id not in ALLOWED_OPERATION_IDS:
            raise ActivationCapabilityError(
                f"operation_id must be one of {sorted(ALLOWED_OPERATION_IDS)}, "
                f"got {self.operation_id!r}"
            )
        if self.gateway_capability_id not in ALLOWED_GATEWAY_CAPABILITY_IDS:
            raise ActivationCapabilityError(
                f"gateway_capability_id must be one of {sorted(ALLOWED_GATEWAY_CAPABILITY_IDS)}, "
                f"got {self.gateway_capability_id!r}"
            )
        if self.implementation_state not in ALLOWED_IMPLEMENTATION_STATES:
            raise ActivationCapabilityError(
                f"operation {self.operation_id!r} claims implementation_state "
                f"{self.implementation_state!r}; the only state this contract can express is "
                f"{NOT_IMPLEMENTED!r} — claiming an implemented capability requires a new contract"
            )
        if not isinstance(self.mutates_state, bool):
            raise ActivationCapabilityError("mutates_state must be a boolean")
        _validate_token_tuple(
            self.required_inputs, field="required_inputs", context=self.operation_id
        )
        _validate_token_tuple(
            self.produced_outputs, field="produced_outputs", context=self.operation_id
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "operation_id": self.operation_id,
            "gateway_capability_id": self.gateway_capability_id,
            "implementation_state": self.implementation_state,
            "mutates_state": self.mutates_state,
            "required_inputs": list(self.required_inputs),
            "produced_outputs": list(self.produced_outputs),
        }

    @classmethod
    def from_dict(cls, data: Any) -> "DeclaredOperation":
        if not isinstance(data, dict):
            raise ActivationCapabilityError("declared operation must be an object")
        missing = _OPERATION_KEYS - set(data)
        if missing:
            raise ActivationCapabilityError(
                f"declared operation is missing required keys: {sorted(missing)}"
            )
        unexpected = set(data) - _OPERATION_KEYS
        if unexpected:
            raise ActivationCapabilityError(
                f"declared operation carries unexpected keys: {sorted(unexpected)}"
            )
        operation = cls(
            operation_id=data["operation_id"],
            gateway_capability_id=data["gateway_capability_id"],
            implementation_state=data["implementation_state"],
            mutates_state=data["mutates_state"],
            required_inputs=_as_tuple(data["required_inputs"], field="required_inputs"),
            produced_outputs=_as_tuple(data["produced_outputs"], field="produced_outputs"),
        )
        operation.validate()
        return operation


@dataclass(frozen=True)
class ContractReference:
    """A versioned Lighthouse contract this capability would consume or produce.

    Referenced, never executed: the descriptor names the artifact shapes an activation would
    exchange; it does not build, validate, or act on them here.
    """

    contract: str
    schema_version: int
    role: str

    def validate(self) -> None:
        if self.role not in ALLOWED_CONTRACT_ROLES:
            raise ActivationCapabilityError(
                f"contract role must be one of {sorted(ALLOWED_CONTRACT_ROLES)}, got {self.role!r}"
            )
        expected_contract, expected_version = _EXPECTED_CONTRACTS[self.role]
        if self.contract != expected_contract:
            raise ActivationCapabilityError(
                f"role {self.role!r} must reference {expected_contract!r}, got {self.contract!r}"
            )
        if isinstance(self.schema_version, bool) or not isinstance(self.schema_version, int):
            raise ActivationCapabilityError(
                f"schema_version for {self.contract!r} must be an integer"
            )
        if self.schema_version != expected_version:
            raise ActivationCapabilityError(
                f"{self.contract!r} is at schema_version {expected_version} in this build, "
                f"but the descriptor claims {self.schema_version!r} — the descriptor is stale"
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "contract": self.contract,
            "schema_version": self.schema_version,
            "role": self.role,
        }

    @classmethod
    def from_dict(cls, data: Any) -> "ContractReference":
        if not isinstance(data, dict):
            raise ActivationCapabilityError("contract reference must be an object")
        missing = _REFERENCE_KEYS - set(data)
        if missing:
            raise ActivationCapabilityError(
                f"contract reference is missing required keys: {sorted(missing)}"
            )
        unexpected = set(data) - _REFERENCE_KEYS
        if unexpected:
            raise ActivationCapabilityError(
                f"contract reference carries unexpected keys: {sorted(unexpected)}"
            )
        reference = cls(
            contract=data["contract"],
            schema_version=data["schema_version"],
            role=data["role"],
        )
        reference.validate()
        return reference


# The canonical content: the single source of truth for what Lighthouse claims today. Each
# operation mirrors its Gateway definition field for field.
CANONICAL_DECLARED_OPERATIONS: tuple[DeclaredOperation, ...] = (
    DeclaredOperation(
        operation_id=OPERATION_REQUEST_ACTIVATION,
        gateway_capability_id=GATEWAY_PROFILE_ACTIVATION_ENDPOINT,
        implementation_state=NOT_IMPLEMENTED,
        mutates_state=True,
        required_inputs=("target_profile_id", "target_profile_version", "transition_reference"),
        produced_outputs=("activation_result",),
    ),
    DeclaredOperation(
        operation_id=OPERATION_RECORD_ACTIVE_PROFILE,
        gateway_capability_id=GATEWAY_ACTIVE_PROFILE_STORE,
        implementation_state=NOT_IMPLEMENTED,
        mutates_state=True,
        required_inputs=("profile_id", "profile_version"),
        produced_outputs=("active_profile_record",),
    ),
    DeclaredOperation(
        operation_id=OPERATION_VALIDATE_POST_SWITCH,
        gateway_capability_id=GATEWAY_POST_SWITCH_VALIDATION,
        implementation_state=NOT_IMPLEMENTED,
        mutates_state=False,
        required_inputs=("expected_profile_id",),
        produced_outputs=("validation_report",),
    ),
)

CANONICAL_CONTRACT_REFERENCES: tuple[ContractReference, ...] = (
    ContractReference(
        contract=CONTRACT_TRANSITION_PREVIEW,
        schema_version=PROFILE_TRANSITION_PREVIEW_SCHEMA_VERSION,
        role=ROLE_REVIEW_ARTIFACT,
    ),
    ContractReference(
        contract=CONTRACT_ACTIVATION_INTENT,
        schema_version=ACTIVATION_INTENT_SCHEMA_VERSION,
        role=ROLE_TRANSITION_REFERENCE,
    ),
    ContractReference(
        contract=CONTRACT_ACTIVE_PROFILE_STATE,
        schema_version=ACTIVE_PROFILE_STATE_SCHEMA_VERSION,
        role=ROLE_ACTIVE_PROFILE_RECORD,
    ),
)

CANONICAL_REQUIRED_EVIDENCE: tuple[str, ...] = (
    "verified_backup_manifest",
    "passing_backup_verification_report",
    "completed_restore_drill",
    "ready_transition_preview",
    "ready_activation_intent",
)

CANONICAL_LIMITATIONS: tuple[str, ...] = (
    "activation_not_implemented",
    "no_activation_endpoint_exists",
    "no_active_profile_store_exists",
    "no_post_switch_validation_exists",
    "production_restore_not_provided",
    "restore_drill_proves_isolated_restore_only",
    "no_rollback_target_until_activation_exists",
)

CANONICAL_SECURITY_CONSTRAINTS: tuple[str, ...] = (
    "operator_approved_transition_required",
    "exactly_one_active_profile",
    "single_activation_request_no_blind_retry",
    "fail_closed_on_ambiguous_state",
    "verified_backup_required_before_activation",
    "intent_and_approval_never_authorize_activation",
)


@dataclass
class ActivationCapabilityDescriptor:
    """One ``lighthouse_activation_capability.v1`` descriptor. See the module docstring."""

    capability_id: str = CAPABILITY_ID
    capability_version: int = CAPABILITY_VERSION
    provider_type: str = PROVIDER_TYPE
    provider_id: str = PROVIDER_ID
    provider_version: str | None = None
    provider_commit: str | None = None
    implementation_state: str = NOT_IMPLEMENTED
    activation_available: bool = False
    declared_operations: tuple[DeclaredOperation, ...] = CANONICAL_DECLARED_OPERATIONS
    contract_references: tuple[ContractReference, ...] = CANONICAL_CONTRACT_REFERENCES
    required_evidence: tuple[str, ...] = CANONICAL_REQUIRED_EVIDENCE
    limitations: tuple[str, ...] = CANONICAL_LIMITATIONS
    security_constraints: tuple[str, ...] = CANONICAL_SECURITY_CONSTRAINTS
    schema_version: int = ACTIVATION_CAPABILITY_SCHEMA_VERSION
    artifact_digest: str = ""

    def _content(self) -> dict[str, Any]:
        """Digest-covered content: every claim; excludes only ``artifact_digest`` itself.

        The descriptor has no volatile fields (no id, no timestamp), so it is a pure function of
        what Lighthouse claims — two builds of the same code with the same provenance stamps
        produce the same digest.
        """
        return {
            "schema_version": self.schema_version,
            "capability_id": self.capability_id,
            "capability_version": self.capability_version,
            "provider_type": self.provider_type,
            "provider_id": self.provider_id,
            "provider_version": self.provider_version,
            "provider_commit": self.provider_commit,
            "implementation_state": self.implementation_state,
            "activation_available": self.activation_available,
            "declared_operations": [op.to_dict() for op in self.declared_operations],
            "contract_references": [ref.to_dict() for ref in self.contract_references],
            "required_evidence": list(self.required_evidence),
            "limitations": list(self.limitations),
            "security_constraints": list(self.security_constraints),
        }

    def compute_digest(self) -> str:
        canonical = json.dumps(self._content(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def validate(self) -> None:
        if isinstance(self.schema_version, bool) or not isinstance(self.schema_version, int):
            raise ActivationCapabilityError("schema_version must be an integer")
        if self.schema_version != ACTIVATION_CAPABILITY_SCHEMA_VERSION:
            raise ActivationCapabilityError(
                f"unsupported schema_version: {self.schema_version!r} "
                f"(this build understands {ACTIVATION_CAPABILITY_SCHEMA_VERSION})"
            )
        if self.capability_id != CAPABILITY_ID:
            raise ActivationCapabilityError(
                f"capability_id must be {CAPABILITY_ID!r}, got {self.capability_id!r}"
            )
        if (
            isinstance(self.capability_version, bool)
            or not isinstance(self.capability_version, int)
            or self.capability_version < 1
        ):
            raise ActivationCapabilityError("capability_version must be an integer >= 1")
        if self.provider_type != PROVIDER_TYPE:
            raise ActivationCapabilityError(
                f"provider_type must be {PROVIDER_TYPE!r}, got {self.provider_type!r}"
            )
        if self.provider_id != PROVIDER_ID:
            raise ActivationCapabilityError(
                f"provider_id must be {PROVIDER_ID!r}, got {self.provider_id!r}"
            )
        _validate_optional_str(self.provider_version, field="provider_version")
        _validate_optional_str(self.provider_commit, field="provider_commit")

        # The two anti-overclaim pins. Neither can be reached by editing a value: the vocabulary
        # has no word for "available", and the boolean is checked by identity.
        if self.implementation_state not in ALLOWED_IMPLEMENTATION_STATES:
            raise ActivationCapabilityError(
                f"implementation_state must be one of "
                f"{sorted(ALLOWED_IMPLEMENTATION_STATES)}, got {self.implementation_state!r} — "
                f"this contract cannot express an available activation capability"
            )
        if self.activation_available is not False:
            raise ActivationCapabilityError(
                "activation_available is a constant False — a descriptor describes a capability, "
                "it never makes one available"
            )

        self._validate_operations()
        self._validate_contract_references()
        _validate_vocabulary_tuple(
            self.required_evidence, ALLOWED_REQUIRED_EVIDENCE, field="required_evidence"
        )
        _validate_vocabulary_tuple(self.limitations, ALLOWED_LIMITATIONS, field="limitations")
        _validate_vocabulary_tuple(
            self.security_constraints,
            ALLOWED_SECURITY_CONSTRAINTS,
            field="security_constraints",
        )

        if not isinstance(self.artifact_digest, str) or not _SHA256_HEX.match(self.artifact_digest):
            raise ActivationCapabilityError(
                "artifact_digest must be a 64-char lowercase hex SHA-256"
            )
        if self.artifact_digest != self.compute_digest():
            raise ActivationCapabilityError(
                "artifact_digest mismatch — descriptor content does not match its digest"
            )

    def _validate_operations(self) -> None:
        if not isinstance(self.declared_operations, tuple) or not self.declared_operations:
            raise ActivationCapabilityError("declared_operations must be a non-empty tuple")
        seen_operations: set[str] = set()
        seen_capabilities: set[str] = set()
        for operation in self.declared_operations:
            if not isinstance(operation, DeclaredOperation):
                raise ActivationCapabilityError(
                    "declared_operations must contain DeclaredOperation records"
                )
            operation.validate()
            if operation.operation_id in seen_operations:
                raise ActivationCapabilityError(
                    f"duplicate operation_id: {operation.operation_id!r}"
                )
            if operation.gateway_capability_id in seen_capabilities:
                raise ActivationCapabilityError(
                    f"duplicate gateway_capability_id: {operation.gateway_capability_id!r}"
                )
            seen_operations.add(operation.operation_id)
            seen_capabilities.add(operation.gateway_capability_id)

    def _validate_contract_references(self) -> None:
        if not isinstance(self.contract_references, tuple) or not self.contract_references:
            raise ActivationCapabilityError("contract_references must be a non-empty tuple")
        seen_roles: set[str] = set()
        for reference in self.contract_references:
            if not isinstance(reference, ContractReference):
                raise ActivationCapabilityError(
                    "contract_references must contain ContractReference records"
                )
            reference.validate()
            if reference.role in seen_roles:
                raise ActivationCapabilityError(f"duplicate contract role: {reference.role!r}")
            seen_roles.add(reference.role)

    def to_dict(self) -> dict[str, Any]:
        """Deterministic serialization. Validates first — an invalid descriptor never serializes."""
        self.validate()
        content = self._content()
        content["artifact_digest"] = self.artifact_digest
        return content

    @classmethod
    def from_dict(cls, data: Any) -> "ActivationCapabilityDescriptor":
        """Strict, fail-closed deserialization: missing and unexpected keys both raise."""
        if not isinstance(data, dict):
            raise ActivationCapabilityError("capability descriptor must be an object")
        missing = _EXPECTED_KEYS - set(data)
        if missing:
            raise ActivationCapabilityError(
                f"capability descriptor is missing required keys: {sorted(missing)}"
            )
        unexpected = set(data) - _EXPECTED_KEYS
        if unexpected:
            raise ActivationCapabilityError(
                f"capability descriptor carries unexpected keys: {sorted(unexpected)}"
            )
        descriptor = cls(
            capability_id=data["capability_id"],
            capability_version=data["capability_version"],
            provider_type=data["provider_type"],
            provider_id=data["provider_id"],
            provider_version=data["provider_version"],
            provider_commit=data["provider_commit"],
            implementation_state=data["implementation_state"],
            activation_available=data["activation_available"],
            declared_operations=_operations_from(data["declared_operations"]),
            contract_references=_references_from(data["contract_references"]),
            required_evidence=_as_tuple(data["required_evidence"], field="required_evidence"),
            limitations=_as_tuple(data["limitations"], field="limitations"),
            security_constraints=_as_tuple(
                data["security_constraints"], field="security_constraints"
            ),
            schema_version=data["schema_version"],
            artifact_digest=data["artifact_digest"],
        )
        descriptor.validate()
        return descriptor


def describe_activation_capability(
    *,
    provider_version: str | None = None,
    provider_commit: str | None = None,
) -> ActivationCapabilityDescriptor:
    """The canonical descriptor: what Lighthouse claims about profile activation today.

    Provenance stamps are best-effort and detected when not supplied (both are ``None`` in
    deployed images, which ship without package metadata or a git checkout). Pure: constructs an
    in-memory record, writes nothing, and activates nothing.
    """
    descriptor = ActivationCapabilityDescriptor(
        provider_version=(
            provider_version if provider_version is not None else detect_lighthouse_version()
        ),
        provider_commit=(
            provider_commit if provider_commit is not None else detect_repository_commit()
        ),
    )
    descriptor.artifact_digest = descriptor.compute_digest()
    descriptor.validate()
    return descriptor


def capability_evidence_reference(descriptor: ActivationCapabilityDescriptor) -> str:
    """The by-reference string a future Gateway attestation would carry as its evidence.

    Matches the Gateway's ``"<schema>:<id>:<digest>"`` convention and stays far inside its
    512-character bound. Lighthouse never issues an attestation — that is Gateway authority; this
    only names the descriptor an attestation would point at.
    """
    descriptor.validate()
    return f"{ACTIVATION_CAPABILITY_SCHEMA}:{descriptor.capability_id}:{descriptor.artifact_digest}"


def _validate_optional_str(value: Any, *, field: str) -> None:
    if value is None:
        return
    if not isinstance(value, str) or not value.strip():
        raise ActivationCapabilityError(f"{field} must be a non-empty string or None")


def _validate_token_tuple(value: Any, *, field: str, context: str) -> None:
    if not isinstance(value, tuple) or not value:
        raise ActivationCapabilityError(f"{context}: {field} must be a non-empty tuple")
    seen: set[str] = set()
    for token in value:
        if not isinstance(token, str) or not token.strip():
            raise ActivationCapabilityError(f"{context}: {field} entries must be non-empty strings")
        if token in seen:
            raise ActivationCapabilityError(f"{context}: duplicate {field} entry {token!r}")
        seen.add(token)


def _validate_vocabulary_tuple(value: Any, allowed: frozenset[str], *, field: str) -> None:
    """Closed-vocabulary membership, not exact-set equality.

    Membership keeps unknown tokens fail-closed while letting an honest, narrower claim from
    another build still validate; the canonical content of today's descriptor is pinned by tests.
    """
    if not isinstance(value, tuple) or not value:
        raise ActivationCapabilityError(f"{field} must be a non-empty tuple")
    seen: set[str] = set()
    for token in value:
        if token not in allowed:
            raise ActivationCapabilityError(
                f"{field} entry {token!r} is not in the closed vocabulary {sorted(allowed)}"
            )
        if token in seen:
            raise ActivationCapabilityError(f"duplicate {field} entry: {token!r}")
        seen.add(token)


def _as_tuple(value: Any, *, field: str) -> tuple[str, ...]:
    if not isinstance(value, list | tuple):
        raise ActivationCapabilityError(f"{field} must be a list")
    return tuple(value)


def _operations_from(value: Any) -> tuple[DeclaredOperation, ...]:
    if not isinstance(value, list | tuple):
        raise ActivationCapabilityError("declared_operations must be a list")
    return tuple(DeclaredOperation.from_dict(entry) for entry in value)


def _references_from(value: Any) -> tuple[ContractReference, ...]:
    if not isinstance(value, list | tuple):
        raise ActivationCapabilityError("contract_references must be a list")
    return tuple(ContractReference.from_dict(entry) for entry in value)
