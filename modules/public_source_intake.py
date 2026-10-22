"""The hardened public-source intake adapter (LHP-INTEL-04B).

**This is the only module in the fact-first pipeline permitted to touch the network.** Everything
else — the contracts, the ledger, the service reads, the CLI rendering — is network-free, and a
repository-wide test fails if a network-capable import appears anywhere but here.

What one check does, in order, and what each step closes:

1. the host must be on the operator's exact allowlist (no suffix trust) — else ``policy_refused``;
2. resolve the host, and **validate every returned address before connecting** — a loopback,
   private, link-local, multicast, reserved, CGNAT, IPv4-mapped-private or metadata address, or a
   *mixed* answer, refuses the whole host. A prohibited resolution never reaches a socket;
3. connect only to a validated address, with TLS verified against the real hostname (SNI + cert),
   and re-check the actual peer as defense in depth;
4. handle redirects manually, bounded, revalidating every hop from step 1;
5. require ``Accept-Encoding: identity`` and refuse any other content encoding, so the bytes hashed
   are the exact bytes served;
6. stream the body in bounded reads and hash it — a body over the bound gets no digest.

Every direct condition becomes one closed factual outcome. A 404 is ``not_found``; a timeout is
``timeout``; a TLS failure is ``tls_failure`` — **none of them is a claim that the source is gone.**
The adapter builds no artifact, writes nothing, and creates no candidate; it hands a plain result to
the service, which shapes and persists it.
"""

from __future__ import annotations

import contextlib
import hashlib
import http.client
import ipaddress
import socket
import ssl
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import urljoin, urlsplit

from modules import public_source_check as psc
from modules.content_identity import HASH_CHUNK_BYTES

ADAPTER_ID = "public_source_intake"
ADAPTER_VERSION = "public_source_intake/1"

USER_AGENT = "Lighthouse/1 (+public_source_check.v1)"

# Bounds — stated here, with rationale, rather than chosen silently.
MAX_TARGETS = 64  # one operator run is a short deliberate list, not a crawl
CONNECT_TIMEOUT = 10.0  # seconds to establish TCP + TLS
READ_TIMEOUT = 30.0  # seconds any single read may block
MAX_REDIRECTS = 3  # a public artifact does not hide behind a long chain
MAX_RESPONSE_BYTES = 25 * 1024 * 1024  # generous for a document; caps a decompression/DoS body
MAX_AGGREGATE_BYTES = 100 * 1024 * 1024  # a whole run's byte budget
CHUNK_BYTES = HASH_CHUNK_BYTES  # 1 MiB, the shared streaming chunk

_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
_CGNAT = ipaddress.ip_network("100.64.0.0/10")

# The policy the checks in a run were produced under — a digest binds the version + the bounds so a
# stored check can be read against the policy that made it. It embeds no host (the allowlist is
# operator configuration, not a fact to publish).
POLICY_PARAMETERS: dict[str, Any] = {
    "policy_version": psc.POLICY_VERSION,
    "scheme": "https",
    "method": "GET",
    "max_redirects": MAX_REDIRECTS,
    "max_response_bytes": MAX_RESPONSE_BYTES,
    "max_aggregate_bytes": MAX_AGGREGATE_BYTES,
    "connect_timeout": CONNECT_TIMEOUT,
    "read_timeout": READ_TIMEOUT,
    "content_encoding": "identity",
    "tls_verification": "required",
}


def policy_digest() -> str:
    return psc._sha256_hex(psc._canonical_json(POLICY_PARAMETERS))


Resolver = Callable[[str], list[str]]


class _Response(Protocol):
    status: int

    def getheader(self, name: str, default: Any = ...) -> Any: ...

    def read(self, amt: int) -> bytes: ...

    def close(self) -> None: ...


class Transport(Protocol):
    def open(
        self,
        *,
        ip: str,
        hostname: str,
        path: str,
        headers: dict[str, str],
        connect_timeout: float,
        read_timeout: float,
    ) -> _Response: ...


@dataclass(frozen=True)
class AdapterResult:
    """One direct outcome, plus the bytes it consumed so a run can keep its aggregate budget."""

    outcome: str
    http_status: int | None
    content_digest: str | None
    body_size_bytes: int | None
    content_type: str | None
    final_locator: str
    redirected: bool
    redirect_chain: tuple[dict[str, Any], ...]
    bytes_consumed: int


# -- internal control-flow signals (converted to outcomes; never escape the module) --------------


class _DnsFailure(Exception):
    pass


class _AddressRefused(Exception):
    pass


class _PeerRefused(Exception):
    pass


class _TooLarge(Exception):
    pass


class _BudgetExceeded(Exception):
    pass


class _IncompleteRead(Exception):
    pass


# -- address policy (pure; the SSRF core) --------------------------------------------------------


def classify_address(ip_str: str) -> str | None:
    """Return a refusal reason, or ``None`` if the address is globally reachable and permitted.

    Only globally-routable addresses are allowed; everything else — loopback, private, link-local,
    multicast, unspecified, reserved, IPv4-mapped-private, carrier-grade NAT, and the cloud metadata
    address (which is link-local) — is refused. IPv4-mapped IPv6 is unwrapped first so a mapped
    private address cannot slip through as "an IPv6 address".
    """
    try:
        ip: ipaddress.IPv4Address | ipaddress.IPv6Address = ipaddress.ip_address(ip_str)
    except ValueError:
        return "unparseable"
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    # `is_global` alone is not enough (it misses IPv6 multicast); check each dangerous class too.
    if (
        ip.is_loopback
        or ip.is_private
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_unspecified
        or ip.is_reserved
        or not ip.is_global
    ):
        return "prohibited"
    if isinstance(ip, ipaddress.IPv4Address) and ip in _CGNAT:
        return "prohibited"
    return None


def _approved_addresses(host: str, resolver: Resolver) -> list[str]:
    try:
        addresses = list(resolver(host))
    except Exception as exc:  # any resolver error is a DNS failure, sanitized
        raise _DnsFailure() from exc
    if not addresses:
        raise _DnsFailure()
    # Fail closed on a mixed answer: if ANY resolved address is prohibited, refuse the whole host.
    # A public+private answer is the shape of a rebinding attempt, not a thing to partially trust.
    if any(classify_address(addr) is not None for addr in addresses):
        raise _AddressRefused()
    return addresses


def default_resolver(host: str) -> list[str]:
    infos = socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
    return [info[4][0] for info in infos]


# -- the guarded TLS connection (the real transport) ---------------------------------------------


def _ssl_context() -> ssl.SSLContext:
    # Verified by default: check_hostname on, CERT_REQUIRED, system trust store. No verify=False,
    # no custom insecure context, no HTTP fallback.
    return ssl.create_default_context()


class _GuardedHTTPSConnection(http.client.HTTPSConnection):
    """Connect to a pre-validated address, but present the real hostname to TLS.

    Overriding ``connect`` is what makes the DNS-rebinding claim true: we dial the exact address we
    validated, and TLS SNI + certificate verification use the hostname — not the address — so the
    connection cannot be rebound to an internal host between our resolution and the client's.
    """

    def __init__(
        self,
        host: str,
        *,
        approved_ip: str,
        ssl_context: ssl.SSLContext,
        connect_timeout: float,
        read_timeout: float,
    ) -> None:
        super().__init__(host, timeout=connect_timeout, context=ssl_context)
        self._approved_ip = approved_ip
        self._read_timeout = read_timeout

    def connect(self) -> None:
        raw = socket.create_connection((self._approved_ip, self.port), timeout=self.timeout)
        raw.settimeout(self._read_timeout)
        self.sock = self._context.wrap_socket(raw, server_hostname=self.host)
        peer = self.sock.getpeername()[0]
        reason = classify_address(peer)
        if reason is not None:  # defense in depth: the address we actually reached must be allowed
            self.sock.close()
            raise _PeerRefused(reason)


class _GuardedHTTPSTransport:
    def open(
        self,
        *,
        ip: str,
        hostname: str,
        path: str,
        headers: dict[str, str],
        connect_timeout: float,
        read_timeout: float,
    ) -> _Response:
        conn = _GuardedHTTPSConnection(
            hostname,
            approved_ip=ip,
            ssl_context=_ssl_context(),
            connect_timeout=connect_timeout,
            read_timeout=read_timeout,
        )
        conn.request("GET", path, headers=headers)
        return conn.getresponse()


def _request_headers() -> dict[str, str]:
    # A fixed, honest identity. No cookies, no authorization, no referrer, no browser impersonation.
    return {
        "User-Agent": USER_AGENT,
        "Accept": "*/*",
        "Accept-Encoding": "identity",
        "Connection": "close",
    }


# -- the check --------------------------------------------------------------------------------


def check_locator(
    canonical: str,
    *,
    allowlist: frozenset[str],
    resolver: Resolver | None = None,
    transport: Transport | None = None,
    aggregate_remaining: int = MAX_AGGREGATE_BYTES,
) -> AdapterResult:
    """Check one admitted canonical locator, returning one closed factual outcome.

    Never raises for a network or policy condition — every one becomes an outcome. A truly
    unexpected internal error is sanitized to ``adapter_failure`` rather than escaping as a
    traceback.
    """
    resolver = resolver or default_resolver
    transport = transport or _GuardedHTTPSTransport()
    try:
        return _check_locator(
            canonical,
            allowlist=allowlist,
            resolver=resolver,
            transport=transport,
            aggregate_remaining=aggregate_remaining,
        )
    except Exception:
        return _refusal(psc.ADAPTER_FAILURE, canonical)


def _check_locator(
    canonical: str,
    *,
    allowlist: frozenset[str],
    resolver: Resolver,
    transport: Transport,
    aggregate_remaining: int,
) -> AdapterResult:
    if aggregate_remaining <= 0:
        return _refusal(psc.RUN_BYTE_BUDGET_EXCEEDED, canonical)

    current = canonical
    chain: list[dict[str, Any]] = []
    visited = {canonical}

    for _hop in range(MAX_REDIRECTS + 1):
        parts = urlsplit(current)
        host = parts.hostname
        redirected = bool(chain)
        if host is None or not psc.host_allowed(host, allowlist):
            return _refusal(psc.POLICY_REFUSED, canonical, final=current, chain=chain)

        try:
            approved = _approved_addresses(host, resolver)
        except _DnsFailure:
            return _refusal(psc.DNS_FAILURE, canonical, final=current, chain=chain)
        except _AddressRefused:
            return _refusal(psc.POLICY_REFUSED, canonical, final=current, chain=chain)

        path = parts.path or "/"
        if parts.query:
            path += "?" + parts.query

        try:
            response = transport.open(
                ip=approved[0],
                hostname=host,
                path=path,
                headers=_request_headers(),
                connect_timeout=CONNECT_TIMEOUT,
                read_timeout=READ_TIMEOUT,
            )
        except _PeerRefused:
            return _refusal(psc.POLICY_REFUSED, canonical, final=current, chain=chain)
        except TimeoutError:
            return _refusal(psc.TIMEOUT, canonical, final=current, chain=chain)
        except ssl.SSLError:
            return _refusal(psc.TLS_FAILURE, canonical, final=current, chain=chain)
        except (ConnectionError, OSError):
            return _refusal(psc.CONNECTION_FAILURE, canonical, final=current, chain=chain)

        try:
            status = response.status
            if status in _REDIRECT_STATUSES:
                location = response.getheader("Location")
                if not location:
                    return _refusal(
                        psc.REDIRECTED_FAILURE, canonical, final=current, chain=chain, status=status
                    )
                try:
                    nxt = psc.canonical_locator(urljoin(current, location))
                except psc.LocatorError:
                    return _refusal(
                        psc.REDIRECTED_FAILURE, canonical, final=current, chain=chain, status=status
                    )
                chain.append({"from_locator": current, "to_locator": nxt, "http_status": status})
                if nxt in visited:
                    return _refusal(
                        psc.REDIRECTED_FAILURE, canonical, final=nxt, chain=chain, status=status
                    )
                visited.add(nxt)
                current = nxt
                continue

            return _classify_terminal(
                status,
                response,
                canonical=canonical,
                final=current,
                redirected=redirected,
                chain=chain,
                aggregate_remaining=aggregate_remaining,
            )
        finally:
            with contextlib.suppress(Exception):
                response.close()

    # More hops than we will follow. The last recorded status (if any) rides along in the chain.
    last_status = chain[-1]["http_status"] if chain else None
    return _refusal(
        psc.REDIRECTED_FAILURE, canonical, final=current, chain=chain, status=last_status
    )


def _classify_terminal(
    status: int,
    response: _Response,
    *,
    canonical: str,
    final: str,
    redirected: bool,
    chain: list[dict[str, Any]],
    aggregate_remaining: int,
) -> AdapterResult:
    chain_t = tuple(dict(hop) for hop in chain)

    if status in (404, 410):
        return _http(psc.NOT_FOUND, status, final, redirected, chain_t)
    if status in (401, 403):
        return _http(psc.AUTHENTICATION_REQUIRED, status, final, redirected, chain_t)
    if status == 429:
        return _http(psc.RATE_LIMITED, status, final, redirected, chain_t)
    if status == 206:
        # A partial answer we never asked for is not the whole resource.
        return _optional(psc.INCOMPLETE_RESPONSE, None, final, redirected, chain_t)
    if status == 204:
        return _available(
            hashlib.sha256(b"").hexdigest(), 0, None, status, final, redirected, chain_t
        )
    if 200 <= status < 300:
        encoding = response.getheader("Content-Encoding")
        if encoding and encoding.strip().lower() != "identity":
            return _optional(psc.UNSUPPORTED_CONTENT_ENCODING, status, final, redirected, chain_t)
        try:
            digest, size = _stream_and_hash(
                response,
                per_response_limit=MAX_RESPONSE_BYTES,
                aggregate_remaining=aggregate_remaining,
            )
        except _TooLarge:
            return _optional(psc.RESPONSE_TOO_LARGE, status, final, redirected, chain_t)
        except _BudgetExceeded:
            return _refusal(
                psc.RUN_BYTE_BUDGET_EXCEEDED,
                canonical,
                final=final,
                chain=chain,
                redirected=redirected,
            )
        except _IncompleteRead:
            return _optional(psc.INCOMPLETE_RESPONSE, status, final, redirected, chain_t)
        content_type = _normalize_content_type(response.getheader("Content-Type"))
        return _available(digest, size, content_type, status, final, redirected, chain_t)
    if 400 <= status < 500:
        return _http(psc.CLIENT_ERROR, status, final, redirected, chain_t)
    if 500 <= status < 600:
        return _http(psc.SERVER_ERROR, status, final, redirected, chain_t)
    return _refusal(psc.UNKNOWN, canonical, final=final, chain=chain, redirected=redirected)


def _stream_and_hash(
    response: _Response, *, per_response_limit: int, aggregate_remaining: int
) -> tuple[str, int]:
    hasher = hashlib.sha256()
    size = 0
    hard = min(per_response_limit, aggregate_remaining)
    while True:
        # +1 so a body of exactly `hard` reads clean, while one byte more is detected as overflow.
        to_read = min(CHUNK_BYTES, hard - size + 1)
        if to_read <= 0:
            to_read = 1
        try:
            chunk = response.read(to_read)
        except (TimeoutError, ssl.SSLError, ConnectionError, OSError) as exc:
            raise _IncompleteRead() from exc
        if not chunk:
            break
        size += len(chunk)
        if size > hard:
            if per_response_limit <= aggregate_remaining:
                raise _TooLarge()
            raise _BudgetExceeded()
        hasher.update(chunk)
    return hasher.hexdigest(), size


def _normalize_content_type(raw: Any) -> str | None:
    if not raw or not isinstance(raw, str):
        return None
    value = raw.split(";", 1)[0].strip().lower()
    if not value or len(value) > 128:
        return None
    if not all(ch.isalnum() or ch in "!#$&^_.+/-" for ch in value):
        return None
    return value


# -- result constructors -------------------------------------------------------------------------


def _available(
    digest: str,
    size: int,
    content_type: str | None,
    status: int,
    final: str,
    redirected: bool,
    chain: tuple[dict[str, Any], ...],
) -> AdapterResult:
    return AdapterResult(
        outcome=psc.AVAILABLE,
        http_status=status,
        content_digest=digest,
        body_size_bytes=size,
        content_type=content_type,
        final_locator=final,
        redirected=redirected,
        redirect_chain=chain,
        bytes_consumed=size,
    )


def _http(
    outcome: str, status: int, final: str, redirected: bool, chain: tuple[dict[str, Any], ...]
) -> AdapterResult:
    return AdapterResult(
        outcome=outcome,
        http_status=status,
        content_digest=None,
        body_size_bytes=None,
        content_type=None,
        final_locator=final,
        redirected=redirected,
        redirect_chain=chain,
        bytes_consumed=0,
    )


def _optional(
    outcome: str,
    status: int | None,
    final: str,
    redirected: bool,
    chain: tuple[dict[str, Any], ...],
) -> AdapterResult:
    return AdapterResult(
        outcome=outcome,
        http_status=status,
        content_digest=None,
        body_size_bytes=None,
        content_type=None,
        final_locator=final,
        redirected=redirected,
        redirect_chain=chain,
        bytes_consumed=0,
    )


def _refusal(
    outcome: str,
    canonical: str,
    *,
    final: str | None = None,
    chain: list[dict[str, Any]] | None = None,
    redirected: bool | None = None,
    status: int | None = None,
) -> AdapterResult:
    chain = chain or []
    return AdapterResult(
        outcome=outcome,
        http_status=status,
        content_digest=None,
        body_size_bytes=None,
        content_type=None,
        final_locator=final or canonical,
        redirected=bool(chain) if redirected is None else redirected,
        redirect_chain=tuple(dict(hop) for hop in chain),
        bytes_consumed=0,
    )
