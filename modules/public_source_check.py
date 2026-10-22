"""Public-source check contracts (LHP-INTEL-04B) — the pure record of one HTTPS check.

Three versioned contracts, defined once and validated fail-closed:

* ``public_source_targets.v1`` — the operator's declared work list of locators. It is **not** a
  trust boundary: it has no field in which a host could be authorized (that lives in ``config.py``),
  and a duplicate canonical locator is refused rather than silently collapsed.
* ``public_source_check.v1`` — one direct factual outcome of one attempt to fetch one admitted
  locator. It records *what happened* — a status, a byte count, an exact-byte digest, a time — and
  has **no field in which it could record what that outcome means**. A 404 is a 404, never a
  disappearance; a timeout is a timeout, never an absence.
* ``public_source_check_run.v1`` — one operator invocation grouping its checks.

Two things set these apart from the observation substrate. **Time is part of a check's identity**
(``checked_at`` is digest-covered), because "I checked and it was there, then I checked again and it
was not" is the whole point — the ledger is an event log, never idempotent. And a run carries a
``run_nonce``, so two runs of an identical target set at the same instant remain distinct.

Pure: no network, no database, no clock, no environment, no LLM. Callers supply the time and the
outcome; this module only shapes and checks the record.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from modules.content_identity import DIGEST_ALGORITHM

# -- contract versions ---------------------------------------------------------------------------

TARGETS_SCHEMA_VERSION = "public_source_targets.v1"
CHECK_SCHEMA_VERSION = "public_source_check.v1"
RUN_SCHEMA_VERSION = "public_source_check_run.v1"
POLICY_VERSION = "public_source_policy.v1"

# -- identity prefixes ---------------------------------------------------------------------------

SOURCE_ID_PREFIX = "public-source:"
LOCATOR_ID_PREFIX = "public-locator:"
CHECK_ID_PREFIX = "psc_"
RUN_ID_PREFIX = "psr_"

# -- fixed classification (a target file may never override these) --------------------------------

SOURCE_CLASS = "public_source"
PRIVACY_CLASS = "public"

# -- bounds that the contract itself enforces ----------------------------------------------------

MAX_URL_LENGTH = 2048

# -- the closed outcome vocabulary ---------------------------------------------------------------
# Each names one direct fact. There is deliberately no member for disappeared / suppressed /
# removed / important — those are interpretations, and interpretation is a later layer's to make.

AVAILABLE = "available"
NOT_FOUND = "not_found"  # exactly 404 or 410 — an HTTP answer, not a verdict
AUTHENTICATION_REQUIRED = "authentication_required"  # 401 / 403
RATE_LIMITED = "rate_limited"  # 429
CLIENT_ERROR = "client_error"  # other 4xx
SERVER_ERROR = "server_error"  # 5xx
REDIRECTED_FAILURE = "redirected_failure"
POLICY_REFUSED = "policy_refused"
DNS_FAILURE = "dns_failure"
TLS_FAILURE = "tls_failure"
CONNECTION_FAILURE = "connection_failure"
TIMEOUT = "timeout"
RESPONSE_TOO_LARGE = "response_too_large"
UNSUPPORTED_CONTENT_ENCODING = "unsupported_content_encoding"
INCOMPLETE_RESPONSE = "incomplete_response"
ADAPTER_FAILURE = "adapter_failure"
UNKNOWN = "unknown"
RUN_BYTE_BUDGET_EXCEEDED = "run_byte_budget_exceeded"

OUTCOMES = frozenset(
    {
        AVAILABLE,
        NOT_FOUND,
        AUTHENTICATION_REQUIRED,
        RATE_LIMITED,
        CLIENT_ERROR,
        SERVER_ERROR,
        REDIRECTED_FAILURE,
        POLICY_REFUSED,
        DNS_FAILURE,
        TLS_FAILURE,
        CONNECTION_FAILURE,
        TIMEOUT,
        RESPONSE_TOO_LARGE,
        UNSUPPORTED_CONTENT_ENCODING,
        INCOMPLETE_RESPONSE,
        ADAPTER_FAILURE,
        UNKNOWN,
        RUN_BYTE_BUDGET_EXCEEDED,
    }
)

# Outcomes reached only when a complete HTTP response arrived — a status is REQUIRED, range-checked.
HTTP_RESPONSE_OUTCOMES = frozenset(
    {AVAILABLE, NOT_FOUND, AUTHENTICATION_REQUIRED, RATE_LIMITED, CLIENT_ERROR, SERVER_ERROR}
)

# Outcomes where an HTTP response arrived but its body was unusable — a status MAY be recorded (the
# exact final status when there was one), but the outcome is not the status's own meaning.
STATUS_OPTIONAL_OUTCOMES = frozenset(
    {RESPONSE_TOO_LARGE, UNSUPPORTED_CONTENT_ENCODING, INCOMPLETE_RESPONSE, REDIRECTED_FAILURE}
)

# -- authority pins (const False, identity-checked; digest-covered) ------------------------------
# The record cannot authorize anything and is not a candidate/hypothesis/finding. These are not
# flags to be flipped; they are the shape of a thing that has no authority to give.

CHECK_AUTHORITY_PINS: dict[str, bool] = {
    "creates_candidate": False,
    "is_candidate": False,
    "is_hypothesis": False,
    "is_finding": False,
    "is_verified_finding": False,
    "authorizes_preservation": False,
    "authorizes_publication": False,
    "authorizes_disclosure": False,
    "authorizes_release": False,
    "contacts_gateway": False,
    "uses_llm": False,
}

RUN_AUTHORITY_PINS: dict[str, bool] = {
    "scheduled": False,
    "background": False,
    "creates_candidate": False,
    "contacts_gateway": False,
    "uses_llm": False,
    "authorizes_action": False,
}

_HOSTNAME_RE = re.compile(
    r"^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)*$"
)


class PublicSourceCheckError(Exception):
    """Base for every fail-closed refusal in this module."""


class LocatorError(PublicSourceCheckError):
    """The locator is not a thing v1 will fetch."""


class HostPolicyError(PublicSourceCheckError):
    """A configured host-allowlist entry is not a bare, valid hostname."""


class TargetsError(PublicSourceCheckError):
    """The declared targets file is malformed, unknown, or says something it may not say."""


# -- small digest helpers (the standard canonical-JSON → SHA-256 recipe) --------------------------


def _canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _artifact_digest(content: dict[str, Any]) -> str:
    return _sha256_hex(_canonical_json(content))


def _is_hex64(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(char in "0123456789abcdef" for char in value)
    )


def confidence_for(outcome: str) -> float:
    """Epistemic, not evaluative: *did I determine this outcome?* — never *how likely anything?*"""
    return 0.0 if outcome == UNKNOWN else 1.0


# -- host + locator policy (static; DNS and address checks live in the adapter) -------------------


def _canonical_host(host: str) -> str:
    host = host.strip().rstrip(".").lower()
    if not host:
        raise LocatorError("locator has no host")
    try:
        ascii_host = host.encode("idna").decode("ascii") if not host.isascii() else host
    except (UnicodeError, ValueError) as exc:
        raise LocatorError(f"locator host is not a valid hostname: {host!r}") from exc
    if not _HOSTNAME_RE.fullmatch(ascii_host):
        raise LocatorError(f"locator host is not a valid hostname: {ascii_host!r}")
    return ascii_host


def canonical_locator(raw: Any) -> str:
    """Canonicalize an admitted locator, or refuse it. HTTPS + GET + default port only in v1.

    The canonical form lowercases scheme and host, drops a default 443 port and the fragment (which
    is never sent to the server), normalizes an empty path to ``/``, and preserves path case and
    query order and content verbatim. Two encodings of the same locator share one identity;
    a semantically equivalent but differently-encoded URL may still be a different identity in v1,
    and that is a disclosed limitation.
    """
    if not isinstance(raw, str) or not raw.strip():
        raise LocatorError("locator must be a non-empty string")
    if len(raw) > MAX_URL_LENGTH:
        raise LocatorError(f"locator exceeds the {MAX_URL_LENGTH}-character bound")

    try:
        parts = urlsplit(raw.strip())
    except ValueError as exc:
        raise LocatorError(f"locator is malformed: {exc}") from exc

    if parts.scheme.lower() != "https":
        raise LocatorError(f"only https locators are supported in v1, not {parts.scheme!r}")
    if parts.username or parts.password:
        raise LocatorError("locator must not carry credentials")

    try:
        port = parts.port
    except ValueError as exc:
        raise LocatorError(f"locator has an invalid port: {exc}") from exc
    if port is not None and port != 443:
        raise LocatorError(f"only the default https port is supported in v1, not {port}")

    raw_host = parts.hostname
    if not raw_host:
        raise LocatorError("locator has no host")
    # An IP literal is refused in v1: an allowlist is by hostname, and a literal skips DNS policy.
    try:
        ipaddress.ip_address(raw_host)
    except ValueError:
        pass
    else:
        raise LocatorError("locator must name a host, not an IP literal, in v1")

    host = _canonical_host(raw_host)
    path = parts.path or "/"
    canonical = f"https://{host}{path}"
    if parts.query:
        canonical += f"?{parts.query}"
    if len(canonical) > MAX_URL_LENGTH:
        raise LocatorError(f"canonical locator exceeds the {MAX_URL_LENGTH}-character bound")
    return canonical


def normalize_host(raw: Any) -> str:
    """A single allowlist entry, normalized to a bare hostname — or refused.

    An entry that carries a scheme, path, port, userinfo or wildcard is refused rather than
    interpreted: a trusted host is a hostname, and anything else is either a mistake or an attempt
    to widen trust past what an exact match allows.
    """
    if not isinstance(raw, str):
        raise HostPolicyError("host entry must be a string")
    host = raw.strip()
    if not host:
        raise HostPolicyError("empty host entry")
    if any(ch in host for ch in "/@*:") or any(ch.isspace() for ch in host):
        raise HostPolicyError(f"host entry must be a bare hostname: {raw!r}")
    try:
        return _canonical_host(host)
    except LocatorError as exc:
        raise HostPolicyError(str(exc)) from exc


def normalize_host_allowlist(hosts: Any) -> frozenset[str]:
    """The resolved outer allowlist — exact hostnames, deduplicated. Empty means *trust nothing*."""
    if hosts is None:
        return frozenset()
    if isinstance(hosts, str) or not hasattr(hosts, "__iter__"):
        raise HostPolicyError("host allowlist must be a list of hostnames")
    return frozenset(normalize_host(entry) for entry in hosts)


def host_allowed(host: str, allowlist: frozenset[str]) -> bool:
    """Exact match only. A subdomain of an allowed host is not an allowed host."""
    try:
        return normalize_host(host) in allowlist
    except HostPolicyError:
        return False


# -- identity recipes ----------------------------------------------------------------------------


def source_id_for(canonical: str) -> str:
    return SOURCE_ID_PREFIX + _sha256_hex(canonical)


def locator_id_for(canonical: str) -> str:
    return LOCATOR_ID_PREFIX + _sha256_hex(canonical)


def target_set_digest(canonical_locators: tuple[str, ...]) -> str:
    return _sha256_hex(_canonical_json(list(canonical_locators)))


def compute_run_id(
    *,
    started_at: str,
    run_nonce: str,
    adapter_id: str,
    adapter_version: str,
    policy_digest: str,
    target_count: int,
    target_set_digest: str,
    policy_version: str = POLICY_VERSION,
) -> str:
    """The run identity, knowable before the checks run — so each check can embed its ``run_id``.

    ``build_run`` recomputes the same value from the same inputs, so a caller may compute it and
    pass it to ``build_check`` without the two disagreeing.
    """
    identity = {
        "schema_version": RUN_SCHEMA_VERSION,
        "started_at": started_at,
        "run_nonce": run_nonce,
        "adapter_id": adapter_id,
        "adapter_version": adapter_version,
        "policy_version": policy_version,
        "policy_digest": policy_digest,
        "target_count": target_count,
        "target_set_digest": target_set_digest,
    }
    return RUN_ID_PREFIX + _sha256_hex(_canonical_json(identity))


# -- the check artifact --------------------------------------------------------------------------


@dataclass(frozen=True)
class PublicSourceCheck:
    """One direct factual outcome of one attempt to fetch one admitted locator."""

    schema_version: str
    check_id: str
    run_id: str
    source_id: str
    locator_id: str
    canonical_locator: str
    final_locator: str
    redirected: bool
    redirect_chain: tuple[dict[str, Any], ...]
    outcome: str
    confidence: float
    http_status: int | None
    content_digest: str | None
    content_digest_algorithm: str | None
    body_size_bytes: int | None
    content_type: str | None
    checked_at: str
    adapter_id: str
    adapter_version: str
    policy_version: str
    policy_digest: str
    digest_algorithm: str
    artifact_digest: str

    @property
    def source_class(self) -> str:
        return SOURCE_CLASS

    @property
    def privacy_class(self) -> str:
        return PRIVACY_CLASS

    def _content(self) -> dict[str, Any]:
        """Everything the identity covers — every field but ``check_id`` and ``artifact_digest``,
        including the fixed classification and the const authority pins, so none of them can be
        forged without breaking the digest."""
        return {
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "source_id": self.source_id,
            "locator_id": self.locator_id,
            "canonical_locator": self.canonical_locator,
            "final_locator": self.final_locator,
            "redirected": self.redirected,
            "redirect_chain": [dict(hop) for hop in self.redirect_chain],
            "outcome": self.outcome,
            "confidence": self.confidence,
            "http_status": self.http_status,
            "content_digest": self.content_digest,
            "content_digest_algorithm": self.content_digest_algorithm,
            "body_size_bytes": self.body_size_bytes,
            "content_type": self.content_type,
            "checked_at": self.checked_at,
            "adapter_id": self.adapter_id,
            "adapter_version": self.adapter_version,
            "policy_version": self.policy_version,
            "policy_digest": self.policy_digest,
            "digest_algorithm": self.digest_algorithm,
            "source_class": SOURCE_CLASS,
            "privacy_class": PRIVACY_CLASS,
            "publication_authorized": False,
            "disclosure_authorized": False,
            **{pin: False for pin in CHECK_AUTHORITY_PINS},
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            **self._content(),
            "artifact_digest": self.artifact_digest,
            "check_id": self.check_id,
        }

    def validate(self) -> None:
        if self.schema_version != CHECK_SCHEMA_VERSION:
            raise PublicSourceCheckError(f"unsupported check schema: {self.schema_version!r}")
        if self.outcome not in OUTCOMES:
            raise PublicSourceCheckError(f"unknown outcome: {self.outcome!r}")
        if self.confidence != confidence_for(self.outcome):
            raise PublicSourceCheckError("confidence does not match the outcome")
        if self.digest_algorithm != DIGEST_ALGORITHM:
            raise PublicSourceCheckError("digest algorithm must be sha256")
        if not isinstance(self.redirected, bool):
            raise PublicSourceCheckError("redirected must be a boolean")
        if not self.redirected and self.redirect_chain:
            raise PublicSourceCheckError("a non-redirected check has no redirect chain")
        self._validate_status_and_content()
        digest = _artifact_digest(self._content())
        if self.artifact_digest != digest:
            raise PublicSourceCheckError("artifact_digest does not match the check content")
        if self.check_id != CHECK_ID_PREFIX + digest:
            raise PublicSourceCheckError("check_id does not match the artifact digest")

    def _validate_status_and_content(self) -> None:
        if self.outcome == AVAILABLE:
            if not _is_hex64(self.content_digest):
                raise PublicSourceCheckError("an available check requires a sha256 content digest")
            if self.content_digest_algorithm != DIGEST_ALGORITHM:
                raise PublicSourceCheckError("content digest algorithm must be sha256")
            if not isinstance(self.body_size_bytes, int) or self.body_size_bytes < 0:
                raise PublicSourceCheckError("an available check requires a byte count")
        else:
            if self.content_digest is not None:
                raise PublicSourceCheckError(f"{self.outcome} must not carry a content digest")
            if self.content_digest_algorithm is not None:
                raise PublicSourceCheckError(f"{self.outcome} must not carry a digest algorithm")
            if self.body_size_bytes is not None:
                raise PublicSourceCheckError(f"{self.outcome} must not carry a byte count")

        if self.outcome in HTTP_RESPONSE_OUTCOMES:
            if not isinstance(self.http_status, int):
                raise PublicSourceCheckError(f"{self.outcome} requires an http status")
            _require_status_in_range(self.outcome, self.http_status)
        elif self.outcome in STATUS_OPTIONAL_OUTCOMES:
            if self.http_status is not None and not (
                isinstance(self.http_status, int) and 100 <= self.http_status < 600
            ):
                raise PublicSourceCheckError(f"{self.outcome} carries an implausible http status")
        elif self.http_status is not None:
            raise PublicSourceCheckError(f"{self.outcome} carries no http status")

        if self.content_type is not None and (
            not isinstance(self.content_type, str) or len(self.content_type) > 128
        ):
            raise PublicSourceCheckError("content_type must be a short string or null")

    @classmethod
    def from_dict(cls, data: Any) -> PublicSourceCheck:
        if not isinstance(data, dict):
            raise PublicSourceCheckError("a check must be an object")
        _require_exact_keys("check", data, _CHECK_KEYS, PublicSourceCheckError)
        if data.get("source_class") != SOURCE_CLASS:
            raise PublicSourceCheckError("source_class must be public_source")
        if data.get("privacy_class") != PRIVACY_CLASS:
            raise PublicSourceCheckError("privacy_class must be public")
        for pin in ("publication_authorized", "disclosure_authorized"):
            if data.get(pin) is not False:
                raise PublicSourceCheckError(f"{pin} must be literal False")
        for pin in CHECK_AUTHORITY_PINS:
            if data.get(pin) is not False:
                raise PublicSourceCheckError(f"authority pin {pin} must be literal False")
        return cls(
            schema_version=data["schema_version"],
            check_id=data["check_id"],
            run_id=data["run_id"],
            source_id=data["source_id"],
            locator_id=data["locator_id"],
            canonical_locator=data["canonical_locator"],
            final_locator=data["final_locator"],
            redirected=data["redirected"],
            redirect_chain=tuple(dict(hop) for hop in data["redirect_chain"]),
            outcome=data["outcome"],
            confidence=data["confidence"],
            http_status=data["http_status"],
            content_digest=data["content_digest"],
            content_digest_algorithm=data["content_digest_algorithm"],
            body_size_bytes=data["body_size_bytes"],
            content_type=data["content_type"],
            checked_at=data["checked_at"],
            adapter_id=data["adapter_id"],
            adapter_version=data["adapter_version"],
            policy_version=data["policy_version"],
            policy_digest=data["policy_digest"],
            digest_algorithm=data["digest_algorithm"],
            artifact_digest=data["artifact_digest"],
        )


def _require_status_in_range(outcome: str, status: int) -> None:
    ok = {
        AVAILABLE: 200 <= status < 300,
        NOT_FOUND: status in (404, 410),
        AUTHENTICATION_REQUIRED: status in (401, 403),
        RATE_LIMITED: status == 429,
        CLIENT_ERROR: 400 <= status < 500 and status not in (401, 403, 404, 410, 429),
        SERVER_ERROR: 500 <= status < 600,
    }[outcome]
    if not ok:
        raise PublicSourceCheckError(f"{outcome} is inconsistent with http status {status}")


def build_check(
    *,
    run_id: str,
    canonical_locator: str,
    final_locator: str,
    redirected: bool,
    redirect_chain: tuple[dict[str, Any], ...],
    outcome: str,
    http_status: int | None,
    content_digest: str | None,
    body_size_bytes: int | None,
    content_type: str | None,
    checked_at: str,
    adapter_id: str,
    adapter_version: str,
    policy_digest: str,
    policy_version: str = POLICY_VERSION,
) -> PublicSourceCheck:
    """Assemble one check, compute its identity, and validate it fail-closed."""
    if outcome not in OUTCOMES:
        raise PublicSourceCheckError(f"unknown outcome: {outcome!r}")
    check = PublicSourceCheck(
        schema_version=CHECK_SCHEMA_VERSION,
        check_id="",
        run_id=run_id,
        source_id=source_id_for(canonical_locator),
        locator_id=locator_id_for(canonical_locator),
        canonical_locator=canonical_locator,
        final_locator=final_locator,
        redirected=redirected,
        redirect_chain=tuple(dict(hop) for hop in redirect_chain),
        outcome=outcome,
        confidence=confidence_for(outcome),
        http_status=http_status,
        content_digest=content_digest,
        content_digest_algorithm=DIGEST_ALGORITHM if content_digest is not None else None,
        body_size_bytes=body_size_bytes,
        content_type=content_type,
        checked_at=checked_at,
        adapter_id=adapter_id,
        adapter_version=adapter_version,
        policy_version=policy_version,
        policy_digest=policy_digest,
        digest_algorithm=DIGEST_ALGORITHM,
        artifact_digest="",
    )
    digest = _artifact_digest(check._content())
    check = replace(check, artifact_digest=digest, check_id=CHECK_ID_PREFIX + digest)
    check.validate()
    return check


# -- the run artifact ----------------------------------------------------------------------------


@dataclass(frozen=True)
class PublicSourceCheckRun:
    """One operator invocation, grouping the checks it produced. Persisted only when complete."""

    schema_version: str
    run_id: str
    started_at: str
    completed_at: str
    run_nonce: str
    adapter_id: str
    adapter_version: str
    policy_version: str
    policy_digest: str
    target_count: int
    target_set_digest: str
    targets_ordered: tuple[str, ...]
    completed_check_count: int
    outcome_counts: dict[str, int]
    check_ids: tuple[str, ...]
    run_status: str
    digest_algorithm: str
    artifact_digest: str

    def _identity(self) -> dict[str, Any]:
        """What ``run_id`` binds — knowable before the checks run, so a check can embed it."""
        return {
            "schema_version": self.schema_version,
            "started_at": self.started_at,
            "run_nonce": self.run_nonce,
            "adapter_id": self.adapter_id,
            "adapter_version": self.adapter_version,
            "policy_version": self.policy_version,
            "policy_digest": self.policy_digest,
            "target_count": self.target_count,
            "target_set_digest": self.target_set_digest,
        }

    def _content(self) -> dict[str, Any]:
        return {
            **self._identity(),
            "run_id": self.run_id,
            "completed_at": self.completed_at,
            "targets_ordered": list(self.targets_ordered),
            "completed_check_count": self.completed_check_count,
            "outcome_counts": dict(self.outcome_counts),
            "check_ids": list(self.check_ids),
            "run_status": self.run_status,
            "digest_algorithm": self.digest_algorithm,
            **{pin: False for pin in RUN_AUTHORITY_PINS},
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self._content(), "artifact_digest": self.artifact_digest}

    def validate(self) -> None:
        if self.schema_version != RUN_SCHEMA_VERSION:
            raise PublicSourceCheckError(f"unsupported run schema: {self.schema_version!r}")
        if self.run_status != "completed":
            raise PublicSourceCheckError("a persisted run is always completed")
        if self.target_count != len(self.targets_ordered):
            raise PublicSourceCheckError("target_count does not match targets_ordered")
        if self.completed_check_count != len(self.check_ids):
            raise PublicSourceCheckError("completed_check_count does not match check_ids")
        if len(set(self.check_ids)) != len(self.check_ids):
            raise PublicSourceCheckError("check ids must be unique within a run")
        if sum(self.outcome_counts.values()) != self.completed_check_count:
            raise PublicSourceCheckError("outcome_counts do not sum to the check count")
        for outcome in self.outcome_counts:
            if outcome not in OUTCOMES:
                raise PublicSourceCheckError(f"unknown outcome in counts: {outcome!r}")
        if self.run_id != RUN_ID_PREFIX + _sha256_hex(_canonical_json(self._identity())):
            raise PublicSourceCheckError("run_id does not match the run identity")
        if self.artifact_digest != _artifact_digest(self._content()):
            raise PublicSourceCheckError("artifact_digest does not match the run content")

    @classmethod
    def from_dict(cls, data: Any) -> PublicSourceCheckRun:
        if not isinstance(data, dict):
            raise PublicSourceCheckError("a run must be an object")
        _require_exact_keys("run", data, _RUN_KEYS, PublicSourceCheckError)
        for pin in RUN_AUTHORITY_PINS:
            if data.get(pin) is not False:
                raise PublicSourceCheckError(f"authority pin {pin} must be literal False")
        return cls(
            schema_version=data["schema_version"],
            run_id=data["run_id"],
            started_at=data["started_at"],
            completed_at=data["completed_at"],
            run_nonce=data["run_nonce"],
            adapter_id=data["adapter_id"],
            adapter_version=data["adapter_version"],
            policy_version=data["policy_version"],
            policy_digest=data["policy_digest"],
            target_count=data["target_count"],
            target_set_digest=data["target_set_digest"],
            targets_ordered=tuple(data["targets_ordered"]),
            completed_check_count=data["completed_check_count"],
            outcome_counts=dict(data["outcome_counts"]),
            check_ids=tuple(data["check_ids"]),
            run_status=data["run_status"],
            digest_algorithm=data["digest_algorithm"],
            artifact_digest=data["artifact_digest"],
        )


def build_run(
    *,
    started_at: str,
    completed_at: str,
    run_nonce: str,
    adapter_id: str,
    adapter_version: str,
    policy_digest: str,
    targets_ordered: tuple[str, ...],
    checks: tuple[PublicSourceCheck, ...],
    policy_version: str = POLICY_VERSION,
) -> PublicSourceCheckRun:
    """Assemble one completed run over its checks, compute both identities, validate fail-closed."""
    ordered = tuple(targets_ordered)
    counts: dict[str, int] = {}
    for check in checks:
        counts[check.outcome] = counts.get(check.outcome, 0) + 1

    run = PublicSourceCheckRun(
        schema_version=RUN_SCHEMA_VERSION,
        run_id="",
        started_at=started_at,
        completed_at=completed_at,
        run_nonce=run_nonce,
        adapter_id=adapter_id,
        adapter_version=adapter_version,
        policy_version=policy_version,
        policy_digest=policy_digest,
        target_count=len(ordered),
        target_set_digest=target_set_digest(ordered),
        targets_ordered=ordered,
        completed_check_count=len(checks),
        outcome_counts=counts,
        check_ids=tuple(check.check_id for check in checks),
        run_status="completed",
        digest_algorithm=DIGEST_ALGORITHM,
        artifact_digest="",
    )
    run = replace(run, run_id=RUN_ID_PREFIX + _sha256_hex(_canonical_json(run._identity())))
    run = replace(run, artifact_digest=_artifact_digest(run._content()))
    run.validate()
    return run


# -- the targets work list -----------------------------------------------------------------------

_TARGET_KEYS = frozenset({"locator"})
_TARGETS_KEYS = frozenset({"schema_version", "targets"})


@dataclass(frozen=True)
class PublicSourceTarget:
    locator: str

    def canonical(self) -> str:
        return canonical_locator(self.locator)

    @classmethod
    def from_dict(cls, data: Any) -> PublicSourceTarget:
        if not isinstance(data, dict):
            raise TargetsError("every target must be an object")
        _require_exact_keys("target", data, _TARGET_KEYS, TargetsError)
        if not isinstance(data["locator"], str):
            raise TargetsError("locator must be a string")
        return cls(locator=data["locator"])


@dataclass(frozen=True)
class PublicSourceTargets:
    schema_version: str
    targets: tuple[PublicSourceTarget, ...]

    def canonical_locators(self) -> tuple[str, ...]:
        return tuple(target.canonical() for target in self.targets)

    def validate(self) -> None:
        if self.schema_version != TARGETS_SCHEMA_VERSION:
            raise TargetsError(f"unsupported targets schema: {self.schema_version!r}")
        if not self.targets:
            raise TargetsError("targets must be a non-empty list")
        seen: set[str] = set()
        for target in self.targets:
            try:
                canonical = target.canonical()
            except LocatorError as exc:
                raise TargetsError(str(exc)) from exc
            if canonical in seen:
                raise TargetsError(f"duplicate canonical locator: {canonical}")
            seen.add(canonical)

    @classmethod
    def from_dict(cls, data: Any) -> PublicSourceTargets:
        if not isinstance(data, dict):
            raise TargetsError("the targets file must be an object")
        _require_exact_keys("targets file", data, _TARGETS_KEYS, TargetsError)
        raw = data["targets"]
        if not isinstance(raw, list):
            raise TargetsError("targets must be a list")
        return cls(
            schema_version=data["schema_version"],
            targets=tuple(PublicSourceTarget.from_dict(item) for item in raw),
        )


def load_targets(path: str | Path) -> PublicSourceTargets:
    """Read and validate the targets file. ``path`` is required — this module reads no env."""
    target = Path(path)
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise TargetsError(f"targets file not found: {target}") from exc
    except (OSError, ValueError) as exc:
        raise TargetsError(f"targets file is unreadable: {exc}") from exc
    targets = PublicSourceTargets.from_dict(raw)
    targets.validate()
    return targets


# -- closed key sets + shared helper -------------------------------------------------------------


def _require_exact_keys(
    label: str,
    data: dict[str, Any],
    expected: frozenset[str],
    error_cls: type[PublicSourceCheckError],
) -> None:
    unexpected = set(data) - expected
    if unexpected:
        raise error_cls(f"{label} carries unexpected keys: {sorted(unexpected)}")
    missing = expected - set(data)
    if missing:
        raise error_cls(f"{label} is missing required keys: {sorted(missing)}")


_CHECK_KEYS = frozenset(
    {
        "schema_version",
        "check_id",
        "run_id",
        "source_id",
        "locator_id",
        "canonical_locator",
        "final_locator",
        "redirected",
        "redirect_chain",
        "outcome",
        "confidence",
        "http_status",
        "content_digest",
        "content_digest_algorithm",
        "body_size_bytes",
        "content_type",
        "checked_at",
        "adapter_id",
        "adapter_version",
        "policy_version",
        "policy_digest",
        "digest_algorithm",
        "artifact_digest",
        "source_class",
        "privacy_class",
        "publication_authorized",
        "disclosure_authorized",
        *CHECK_AUTHORITY_PINS,
    }
)

_RUN_KEYS = frozenset(
    {
        "schema_version",
        "run_id",
        "started_at",
        "completed_at",
        "run_nonce",
        "adapter_id",
        "adapter_version",
        "policy_version",
        "policy_digest",
        "target_count",
        "target_set_digest",
        "targets_ordered",
        "completed_check_count",
        "outcome_counts",
        "check_ids",
        "run_status",
        "digest_algorithm",
        "artifact_digest",
        *RUN_AUTHORITY_PINS,
    }
)
