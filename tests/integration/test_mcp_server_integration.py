"""
Integration tests for the Lighthouse MCP server.

These tests exercise run_module and run_pipeline through a real MCP client
session, hitting the real module code (which may make live network calls).
They are skipped when the optional `mcp` package is not installed.

Run with:
    python -m pytest tests/integration/test_mcp_server_integration.py -m integration -v

Why a client session and not `mcp.call_tool()` directly:
`FastMCP.get_context()` never returns None. Outside a request it returns a
Context whose methods raise "Context is not available outside of a request",
and the server's tools log and report progress through that context before
their own error handling begins. Calling `mcp.call_tool()` directly therefore
cannot work against the real MCP SDK — it only ever worked against the
fallback shim in portal/mcp_server.py, which passes no context at all.

Driving the server through an in-memory client session gives the tools a bound
request context and exercises the real MCP path end to end: initialize ->
tools/call -> content serialization.
"""

import json

import pytest

pytest.importorskip("mcp")

from mcp.shared.memory import create_connected_server_and_client_session

from portal.mcp_server import mcp


def _tool(result) -> dict | list:
    """Unwrap a CallToolResult into the JSON payload the tool returned."""
    assert not result.isError, f"tool call reported an error: {result.content}"
    return json.loads(result.content[0].text)


async def _call(tool_name: str, arguments: dict) -> dict | list:
    """Call one tool over a fresh in-memory client session and return its payload.

    The session is opened and closed inside the caller's own task on purpose.
    `create_connected_server_and_client_session` runs the server in an anyio task
    group, and anyio refuses to exit a cancel scope from a different task than
    entered it — which is exactly what happens if this is wrapped in an
    async-generator pytest fixture, whose setup and teardown run in different
    tasks.
    """
    async with create_connected_server_and_client_session(mcp) as client:
        return _tool(await client.call_tool(tool_name, arguments))


@pytest.mark.integration
async def test_run_module_invalid_returns_error():
    result = await _call(
        "run_module",
        {
            "module_name": "not_a_module",
            "dry_run": True,
        },
    )
    assert "error" in result


@pytest.mark.integration
async def test_run_module_velocity_dry_run():
    """Velocity runs without network — reads from DB only."""
    result = await _call(
        "run_module",
        {
            "module_name": "velocity",
            "dry_run": True,
        },
    )
    assert result["module"] == "velocity"
    assert result["dry_run"] is True
    assert "anomalies" in result or "error" in result


@pytest.mark.integration
async def test_run_module_arxiv_dry_run():
    """arXiv sentinel runs against live API if researchers are configured."""
    result = await _call(
        "run_module",
        {
            "module_name": "arxiv",
            "dry_run": True,
        },
    )
    assert result["module"] == "arxiv"
    assert "silence_alerts" in result or "error" in result


@pytest.mark.integration
async def test_run_module_flight_dry_run():
    result = await _call(
        "run_module",
        {
            "module_name": "flight",
            "dry_run": True,
        },
    )
    assert result["module"] == "flight"
    assert "observations" in result or "error" in result


@pytest.mark.integration
async def test_run_module_nostr_no_key():
    result = await _call(
        "run_module",
        {
            "module_name": "nostr",
            "dry_run": True,
        },
    )
    assert result["module"] == "nostr"
    assert "skipped" in result or "events_published" in result


@pytest.mark.integration
async def test_run_pipeline_dry_run_returns_all_module_keys():
    result = await _call(
        "run_pipeline",
        {
            "since_days": 1,
            "dry_run": True,
        },
    )
    assert result["dry_run"] is True
    for key in ("patents", "velocity", "arxiv", "archive", "narrative", "flight", "nostr"):
        assert key in result, f"Module key '{key}' missing from pipeline result"


@pytest.mark.integration
async def test_run_pipeline_no_module_raises_entire_pipeline():
    """Each module failure is isolated — pipeline result must always have all keys."""
    result = await _call(
        "run_pipeline",
        {
            "since_days": 1,
            "dry_run": True,
        },
    )
    # Every key must be a dict (either results or {"error": ...})
    for key in ("patents", "velocity", "arxiv", "archive", "narrative", "flight", "nostr"):
        assert isinstance(result[key], dict), f"Module '{key}' result is not a dict"
