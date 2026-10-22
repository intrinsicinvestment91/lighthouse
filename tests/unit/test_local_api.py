"""Unit tests for /local/* routes in portal/app.py"""

import pytest

pytest.importorskip("fastapi")

from unittest.mock import MagicMock

from fastapi import HTTPException

import portal.app as app_module

# ---------------------------------------------------------------------------
# /local/health
# ---------------------------------------------------------------------------


async def test_local_health_returns_ok():
    result = await app_module.local_health()
    assert result["status"] == "ok"
    assert result["service"] == "lighthouse-local-gateway"


# ---------------------------------------------------------------------------
# /local/methods
# ---------------------------------------------------------------------------


async def test_local_methods_returns_all_methods():
    result = await app_module.local_methods()
    assert "methods" in result
    expected = {
        "callsign.summary",
        "facility.summary",
        "correlations.recent",
        "graph.summary",
        "intelligence.daily_brief",
    }
    assert set(result["methods"]) == expected


async def test_local_methods_is_sorted():
    result = await app_module.local_methods()
    methods = result["methods"]
    assert methods == sorted(methods)


# ---------------------------------------------------------------------------
# /local/request
# ---------------------------------------------------------------------------


async def test_local_request_dispatches(monkeypatch):
    fake_response = {"status": "ok", "method": "callsign.summary", "result": {}}
    monkeypatch.setattr(app_module, "dispatch", lambda m, p: fake_response)

    req = MagicMock()
    req.method = "callsign.summary"
    req.params = {"callsign": "PAT16"}

    result = await app_module.local_request(req)
    assert result == fake_response


async def test_local_request_unknown_method_returns_gateway_error(monkeypatch):
    monkeypatch.setattr(
        app_module,
        "dispatch",
        lambda m, p: {"status": "error", "method": m, "error": f"unknown method: {m!r}"},
    )

    req = MagicMock()
    req.method = "not.a.method"
    req.params = {}

    result = await app_module.local_request(req)
    assert result["status"] == "error"


# ---------------------------------------------------------------------------
# _require_local_client dependency
# ---------------------------------------------------------------------------


async def test_require_local_client_localhost_allowed():
    req = MagicMock()
    req.client = MagicMock()
    req.client.host = "127.0.0.1"
    # Should not raise
    await app_module._require_local_client(req)


async def test_require_local_client_ipv6_localhost_allowed():
    req = MagicMock()
    req.client = MagicMock()
    req.client.host = "::1"
    await app_module._require_local_client(req)


async def test_require_local_client_remote_rejected():
    req = MagicMock()
    req.client = MagicMock()
    req.client.host = "192.168.1.100"
    with pytest.raises(HTTPException) as exc_info:
        await app_module._require_local_client(req)
    assert exc_info.value.status_code == 403


async def test_require_local_client_no_client_rejected():
    req = MagicMock()
    req.client = None
    with pytest.raises(HTTPException) as exc_info:
        await app_module._require_local_client(req)
    assert exc_info.value.status_code == 403
