"""The hardened public-source intake adapter (LHP-INTEL-04B).

This is the one module in the fact-first pipeline permitted to touch the network. It resolves and
validates every address before it connects, refuses anything that is not an admitted public HTTPS
locator, streams within a bound, and turns every direct condition into one closed factual outcome —
**never** into a claim that a source is gone.

The security core (address classification, the resolve-then-connect gate, redirect revalidation,
bounded streaming) is exercised here with an injected fake resolver and fake transport, so the
DNS-rebinding gate is proven — a prohibited resolution never reaches the transport — without a real
socket. The real guarded connection's TLS is covered by the Docker smoke.
"""

from __future__ import annotations

import ast
import hashlib
import ssl
from pathlib import Path

import pytest

from modules import public_source_check as psc
from modules import public_source_intake as intake

# --------------------------------------------------------------------------------------------
# fakes
# --------------------------------------------------------------------------------------------


class FakeResponse:
    def __init__(self, status: int, *, body: bytes = b"", headers: dict[str, str] | None = None):
        self.status = status
        self._body = body
        self._pos = 0
        self._headers = {k.lower(): v for k, v in (headers or {}).items()}
        self.read_calls: list[int] = []
        self.closed = False

    def getheader(self, name: str, default=None):
        return self._headers.get(name.lower(), default)

    def read(self, amt: int) -> bytes:
        # Every read MUST carry an explicit positive size — a whole-response read is forbidden.
        assert isinstance(amt, int) and amt > 0, f"unbounded read: {amt!r}"
        self.read_calls.append(amt)
        chunk = self._body[self._pos : self._pos + amt]
        self._pos += len(chunk)
        return chunk

    def close(self):
        self.closed = True


class FakeTransport:
    """Returns queued responses; records exactly what it was asked to connect to."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.opened: list[dict] = []

    def open(self, *, ip, hostname, path, headers, connect_timeout, read_timeout):
        self.opened.append({"ip": ip, "hostname": hostname, "path": path, "headers": dict(headers)})
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _allow(*hosts):
    return psc.normalize_host_allowlist(list(hosts))


def _public_resolver(host):  # every host resolves to one approved public address
    return ["8.8.8.8"]


def _check(canonical, *, responses, resolver=_public_resolver, allowlist=None, budget=None):
    transport = FakeTransport(responses if isinstance(responses, list) else [responses])
    result = intake.check_locator(
        canonical,
        allowlist=allowlist if allowlist is not None else _allow("example.org"),
        resolver=resolver,
        transport=transport,
        aggregate_remaining=budget if budget is not None else intake.MAX_RESPONSE_BYTES * 4,
    )
    return result, transport


# --------------------------------------------------------------------------------------------
# address classification (the SSRF core, pure)
# --------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "addr",
    [
        "127.0.0.1",
        "::1",
        "10.0.0.5",
        "192.168.1.1",
        "172.16.0.1",
        "169.254.169.254",  # link-local + the cloud metadata address
        "fe80::1",  # IPv6 link-local
        "ff02::1",  # multicast
        "0.0.0.0",  # unspecified
        "::ffff:10.0.0.1",  # IPv4-mapped private
        "100.64.0.1",  # carrier-grade NAT
    ],
)
def test_prohibited_addresses_are_rejected(addr):
    assert intake.classify_address(addr) is not None


@pytest.mark.parametrize("addr", ["8.8.8.8", "2606:4700:4700::1111"])
def test_public_addresses_are_approved(addr):
    assert intake.classify_address(addr) is None


# --------------------------------------------------------------------------------------------
# the resolve-then-connect gate (DNS-rebinding defense)
# --------------------------------------------------------------------------------------------


def test_a_prohibited_resolution_never_reaches_the_transport():
    # The whole point: we validate BEFORE we connect. A private answer must not be dialed.
    result, transport = _check(
        "https://example.org/a",
        responses=[FakeResponse(200, body=b"x")],
        resolver=lambda h: ["10.0.0.9"],
    )
    assert result.outcome == psc.POLICY_REFUSED
    assert transport.opened == []  # never connected


def test_mixed_public_and_private_resolution_fails_closed():
    result, transport = _check(
        "https://example.org/a",
        responses=[FakeResponse(200, body=b"x")],
        resolver=lambda h: ["8.8.8.8", "10.0.0.9"],
    )
    assert result.outcome == psc.POLICY_REFUSED
    assert transport.opened == []


def test_dns_failure_is_its_own_outcome():
    def _boom(host):
        raise OSError("name resolution failed")

    result, transport = _check(
        "https://example.org/a", responses=[FakeResponse(200)], resolver=_boom
    )
    assert result.outcome == psc.DNS_FAILURE
    assert transport.opened == []


def test_empty_resolution_is_dns_failure():
    result, _ = _check(
        "https://example.org/a", responses=[FakeResponse(200)], resolver=lambda h: []
    )
    assert result.outcome == psc.DNS_FAILURE


def test_unlisted_host_is_refused_before_any_network():
    result, transport = _check(
        "https://not-allowed.example/a",
        responses=[FakeResponse(200)],
        allowlist=_allow("example.org"),
    )
    assert result.outcome == psc.POLICY_REFUSED
    assert transport.opened == []


def test_connects_to_the_validated_address_with_the_real_hostname():
    result, transport = _check(
        "https://example.org/a?q=1", responses=[FakeResponse(200, body=b"x")]
    )
    assert result.outcome == psc.AVAILABLE
    assert transport.opened[0]["ip"] == "8.8.8.8"
    assert transport.opened[0]["hostname"] == "example.org"
    assert transport.opened[0]["path"] == "/a?q=1"


# --------------------------------------------------------------------------------------------
# terminal HTTP outcomes
# --------------------------------------------------------------------------------------------


def test_200_is_available_with_the_exact_byte_digest():
    body = b"hello world"
    result, _ = _check("https://example.org/a", responses=[FakeResponse(200, body=body)])
    assert result.outcome == psc.AVAILABLE
    assert result.http_status == 200
    assert result.content_digest == hashlib.sha256(body).hexdigest()
    assert result.body_size_bytes == len(body)


def test_204_is_available_with_an_empty_body_digest():
    result, _ = _check("https://example.org/a", responses=[FakeResponse(204, body=b"")])
    assert result.outcome == psc.AVAILABLE
    assert result.body_size_bytes == 0
    assert result.content_digest == hashlib.sha256(b"").hexdigest()


@pytest.mark.parametrize("status", [404, 410])
def test_404_and_410_are_not_found_without_a_digest(status):
    result, _ = _check("https://example.org/a", responses=[FakeResponse(status)])
    assert result.outcome == psc.NOT_FOUND
    assert result.http_status == status
    assert result.content_digest is None


@pytest.mark.parametrize("status", [401, 403])
def test_auth_wall_is_not_disappearance(status):
    result, _ = _check("https://example.org/a", responses=[FakeResponse(status)])
    assert result.outcome == psc.AUTHENTICATION_REQUIRED


def test_429_is_rate_limited():
    result, _ = _check("https://example.org/a", responses=[FakeResponse(429)])
    assert result.outcome == psc.RATE_LIMITED


def test_other_4xx_is_client_error():
    result, _ = _check("https://example.org/a", responses=[FakeResponse(400)])
    assert result.outcome == psc.CLIENT_ERROR


@pytest.mark.parametrize("status", [500, 503])
def test_5xx_is_server_error_not_disappearance(status):
    result, _ = _check("https://example.org/a", responses=[FakeResponse(status)])
    assert result.outcome == psc.SERVER_ERROR


# --------------------------------------------------------------------------------------------
# redirects
# --------------------------------------------------------------------------------------------


def test_safe_redirect_to_available_records_the_chain():
    result, _ = _check(
        "https://example.org/a",
        responses=[
            FakeResponse(301, headers={"Location": "https://example.org/b"}),
            FakeResponse(200, body=b"final"),
        ],
    )
    assert result.outcome == psc.AVAILABLE
    assert result.redirected is True
    assert result.final_locator == "https://example.org/b"
    assert len(result.redirect_chain) == 1
    assert result.redirect_chain[0]["http_status"] == 301


def test_redirect_to_not_found_stays_not_found_with_a_chain():
    result, _ = _check(
        "https://example.org/a",
        responses=[
            FakeResponse(302, headers={"Location": "https://example.org/gone"}),
            FakeResponse(404),
        ],
    )
    assert result.outcome == psc.NOT_FOUND
    assert result.redirected is True


def test_redirect_downgrade_to_http_is_refused():
    result, _ = _check(
        "https://example.org/a",
        responses=[FakeResponse(301, headers={"Location": "http://example.org/b"})],
    )
    assert result.outcome == psc.REDIRECTED_FAILURE


def test_redirect_to_unlisted_host_is_refused():
    result, transport = _check(
        "https://example.org/a",
        responses=[
            FakeResponse(301, headers={"Location": "https://evil.example/b"}),
            FakeResponse(200, body=b"x"),
        ],
        allowlist=_allow("example.org"),
    )
    assert result.outcome == psc.POLICY_REFUSED
    assert len(transport.opened) == 1  # never dialed the second host


def test_redirect_to_private_address_is_refused():
    result, _ = _check(
        "https://example.org/a",
        responses=[
            FakeResponse(301, headers={"Location": "https://internal.example/b"}),
        ],
        resolver=lambda h: ["10.0.0.1"] if h == "internal.example" else ["8.8.8.8"],
        allowlist=_allow("example.org", "internal.example"),
    )
    assert result.outcome == psc.POLICY_REFUSED


def test_missing_location_is_redirected_failure():
    result, _ = _check("https://example.org/a", responses=[FakeResponse(301)])
    assert result.outcome == psc.REDIRECTED_FAILURE


def test_redirect_loop_is_refused():
    result, _ = _check(
        "https://example.org/a",
        responses=[
            FakeResponse(301, headers={"Location": "https://example.org/b"}),
            FakeResponse(301, headers={"Location": "https://example.org/a"}),
            FakeResponse(301, headers={"Location": "https://example.org/b"}),
        ],
    )
    assert result.outcome == psc.REDIRECTED_FAILURE


def test_too_many_redirects_is_refused():
    responses = [
        FakeResponse(301, headers={"Location": f"https://example.org/{i}"}) for i in range(10)
    ]
    result, _ = _check("https://example.org/a", responses=responses)
    assert result.outcome == psc.REDIRECTED_FAILURE


# --------------------------------------------------------------------------------------------
# content encoding, bounds, streaming
# --------------------------------------------------------------------------------------------


def test_non_identity_content_encoding_is_unsupported_with_no_digest():
    result, _ = _check(
        "https://example.org/a",
        responses=[FakeResponse(200, body=b"\x1f\x8b", headers={"Content-Encoding": "gzip"})],
    )
    assert result.outcome == psc.UNSUPPORTED_CONTENT_ENCODING
    assert result.content_digest is None


def test_identity_content_encoding_is_accepted():
    result, _ = _check(
        "https://example.org/a",
        responses=[FakeResponse(200, body=b"x", headers={"Content-Encoding": "identity"})],
    )
    assert result.outcome == psc.AVAILABLE


def test_response_over_the_bound_is_too_large_with_no_digest(monkeypatch):
    monkeypatch.setattr(intake, "MAX_RESPONSE_BYTES", 8)
    result, _ = _check("https://example.org/a", responses=[FakeResponse(200, body=b"x" * 64)])
    assert result.outcome == psc.RESPONSE_TOO_LARGE
    assert result.content_digest is None


def test_response_exactly_at_the_bound_succeeds(monkeypatch):
    monkeypatch.setattr(intake, "MAX_RESPONSE_BYTES", 8)
    result, _ = _check("https://example.org/a", responses=[FakeResponse(200, body=b"x" * 8)])
    assert result.outcome == psc.AVAILABLE
    assert result.body_size_bytes == 8


def test_streaming_reads_are_always_bounded(monkeypatch):
    monkeypatch.setattr(intake, "CHUNK_BYTES", 4)
    resp = FakeResponse(200, body=b"x" * 20)
    transport = FakeTransport([resp])
    intake.check_locator(
        "https://example.org/a",
        allowlist=_allow("example.org"),
        resolver=_public_resolver,
        transport=transport,
        aggregate_remaining=intake.MAX_RESPONSE_BYTES,
    )
    assert resp.read_calls  # it streamed
    assert all(0 < n <= intake.CHUNK_BYTES + 1 for n in resp.read_calls)


def test_aggregate_budget_exhaustion_is_its_own_outcome():
    # A later target with no budget left is named, not dropped and not called "unavailable".
    result, transport = _check(
        "https://example.org/a", responses=[FakeResponse(200, body=b"x")], budget=0
    )
    assert result.outcome == psc.RUN_BYTE_BUDGET_EXCEEDED
    assert transport.opened == []


def test_same_bytes_at_two_locators_share_a_content_digest():
    body = b"identical bytes"
    a, _ = _check("https://example.org/a", responses=[FakeResponse(200, body=body)])
    b, _ = _check("https://example.org/b", responses=[FakeResponse(200, body=body)])
    assert a.content_digest == b.content_digest


def test_line_endings_change_the_digest():
    a, _ = _check("https://example.org/a", responses=[FakeResponse(200, body=b"line\n")])
    b, _ = _check("https://example.org/a", responses=[FakeResponse(200, body=b"line\r\n")])
    assert a.content_digest != b.content_digest


# --------------------------------------------------------------------------------------------
# transport-level failures → factual outcomes (never disappearance)
# --------------------------------------------------------------------------------------------


def test_timeout_is_timeout():
    result, _ = _check("https://example.org/a", responses=[TimeoutError("read timed out")])
    assert result.outcome == psc.TIMEOUT


def test_tls_failure_is_tls_failure():
    result, _ = _check(
        "https://example.org/a", responses=[ssl.SSLCertVerificationError("bad cert")]
    )
    assert result.outcome == psc.TLS_FAILURE


def test_connection_failure_is_connection_failure():
    result, _ = _check("https://example.org/a", responses=[ConnectionResetError("reset")])
    assert result.outcome == psc.CONNECTION_FAILURE


def test_unexpected_error_is_sanitized_to_adapter_failure():
    result, _ = _check("https://example.org/a", responses=[RuntimeError("boom")])
    assert result.outcome == psc.ADAPTER_FAILURE


def test_headers_carry_identity_encoding_and_no_cookies():
    _, transport = _check("https://example.org/a", responses=[FakeResponse(200, body=b"x")])
    headers = {k.lower(): v for k, v in transport.opened[0]["headers"].items()}
    assert headers.get("accept-encoding") == "identity"
    assert "cookie" not in headers
    assert "authorization" not in headers
    assert "user-agent" in headers


# --------------------------------------------------------------------------------------------
# structural: this module IS the network chokepoint, and reads are always bounded
# --------------------------------------------------------------------------------------------


def test_module_source_never_reads_a_whole_response():
    tree = ast.parse(Path(intake.__file__).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "read"
        ):
            assert node.args, "every .read(...) must pass an explicit max size"
