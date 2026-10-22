"""Unit tests for portal/mcp_server.py — in-process via mcp.call_tool / mcp.read_resource."""

import json

import pytest

from portal.mcp_server import (
    _NOTE_TYPES,
    _REQUIRED_FIELDS,
    _build_note,
    _validate_payload,
    mcp,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _tool(result) -> dict | list:
    # dict-returning tools → [TextContent(text='...')]
    # list-returning tools → ([], {'result': [...]})
    if isinstance(result, tuple):
        return result[1].get("result", result[1])
    return json.loads(result[0].text)


def _resource(result) -> str:
    return result[0].content


# ---------------------------------------------------------------------------
# Validation helpers (pure, no I/O)
# ---------------------------------------------------------------------------


def test_validate_payload_unknown_type():
    err = _validate_payload("unknown", {})
    assert err is not None
    assert "unknown" in err


def test_validate_payload_missing_fields():
    err = _validate_payload("patent", {"patent_id": "US1"})
    assert err is not None
    assert "title" in err


def test_validate_payload_all_required_present():
    payload = {
        "agency": "DoD",
        "url": "https://example.com",
        "ipfs_cid": "bafy123",
        "snapshot_ts": "2026-01-01T00:00:00Z",
        "matched_keywords": ["strategic"],
    }
    assert _validate_payload("archive", payload) is None


@pytest.mark.parametrize("note_type", _NOTE_TYPES)
def test_required_fields_defined_for_all_note_types(note_type):
    assert note_type in _REQUIRED_FIELDS
    assert len(_REQUIRED_FIELDS[note_type]) > 0


# ---------------------------------------------------------------------------
# _build_note (pure builder dispatch)
# ---------------------------------------------------------------------------


def test_build_note_archive(nostr_test_key_hex):
    from modules.nostr_relay import _load_private_key, _pubkey_hex

    priv = _load_private_key(nostr_test_key_hex)
    pubkey = _pubkey_hex(priv)
    note = _build_note(
        "archive",
        {
            "agency": "NASA",
            "url": "https://nasa.gov/news/",
            "ipfs_cid": "bafytest",
            "snapshot_ts": "2026-01-01T00:00:00Z",
            "matched_keywords": ["initiative"],
        },
        pubkey,
    )
    assert "[ARCHIVE] NASA" in note.content
    assert any(t == ["ipfs", "bafytest"] for t in note.tags)


def test_build_note_velocity(nostr_test_key_hex):
    from modules.nostr_relay import _load_private_key, _pubkey_hex

    priv = _load_private_key(nostr_test_key_hex)
    pubkey = _pubkey_hex(priv)
    note = _build_note(
        "velocity",
        {
            "ipc_class": "G21B",
            "anomaly_type": "spike",
            "week": "2026-W17",
            "zscore": 3.5,
            "context": "count=10, mean=2.1",
        },
        pubkey,
    )
    assert "[VELOCITY-SPIKE]" in note.content
    assert "G21B" in note.content


def test_build_note_researcher(nostr_test_key_hex):
    from modules.nostr_relay import _load_private_key, _pubkey_hex

    priv = _load_private_key(nostr_test_key_hex)
    pubkey = _pubkey_hex(priv)
    note = _build_note(
        "researcher",
        {
            "name": "Jane Smith",
            "institution": "MIT",
            "last_pub_date": "2025-12-01",
            "silent_days": 90,
        },
        pubkey,
    )
    assert "[RESEARCHER-DARK]" in note.content
    assert "90 days" in note.content


# ---------------------------------------------------------------------------
# query_alerts tool
# ---------------------------------------------------------------------------


async def test_query_alerts_returns_list(tmp_path, monkeypatch):
    from modules import db as _db

    monkeypatch.setattr(_db, "DB_PATH", tmp_path / "state.db")
    _db.init_alerts_table()
    _db.insert_alert("test_source", {"kind": "test_spike", "score": 0.9, "score_label": "high"})

    result = _tool(await mcp.call_tool("query_alerts", {}))
    assert isinstance(result, list)
    assert len(result) == 1
    assert result[0]["kind"] == "test_spike"


async def test_query_alerts_source_filter(tmp_path, monkeypatch):
    from modules import db as _db

    monkeypatch.setattr(_db, "DB_PATH", tmp_path / "state.db")
    _db.init_alerts_table()
    _db.insert_alert("sec_sentinel", {"kind": "filing", "score": 0.8})
    _db.insert_alert("velocity_tracker", {"kind": "spike", "score": 0.7})

    result = _tool(await mcp.call_tool("query_alerts", {"source": "sec_sentinel"}))
    assert isinstance(result, list)
    assert len(result) == 1
    assert result[0]["kind"] == "filing"


async def test_query_alerts_empty_db_returns_empty_list(tmp_path, monkeypatch):
    from modules import db as _db

    monkeypatch.setattr(_db, "DB_PATH", tmp_path / "state.db")
    _db.init_alerts_table()

    result = _tool(await mcp.call_tool("query_alerts", {}))
    assert result == []


# ---------------------------------------------------------------------------
# Correlation read tools
# ---------------------------------------------------------------------------


async def test_query_correlations_returns_list(tmp_path, monkeypatch):
    from modules import db as _db

    monkeypatch.setattr(_db, "DB_PATH", tmp_path / "state.db")
    _db.init_correlation_table()
    _db.insert_correlation(
        event_id="evt-123",
        event_type="sec_flight",
        entity="Lockheed Martin",
        entities=["Lockheed Martin"],
        signals=["sec", "flight"],
        evidence={"sec": {"company": "LOCKHEED MARTIN CORP"}},
        score=6.5,
        sec_filed_at="2026-04-01",
        observed_at=1_777_000_000,
    )

    result = _tool(await mcp.call_tool("query_correlations", {"limit": 5}))
    assert isinstance(result, list)
    assert len(result) == 1
    assert result[0]["event_id"] == "evt-123"
    assert result[0]["entities"] == ["Lockheed Martin"]
    assert result[0]["signals"] == ["flight", "sec"]


async def test_explain_correlation_supports_event_id_and_missing(tmp_path, monkeypatch):
    from modules import db as _db

    monkeypatch.setattr(_db, "DB_PATH", tmp_path / "state.db")
    _db.init_correlation_table()
    _db.insert_correlation(
        event_id="evt-explain",
        event_type="sec_flight",
        entity="Lockheed Martin",
        entities=["Lockheed Martin"],
        signals=["sec", "flight"],
        evidence={"sec": {"company": "LOCKHEED MARTIN CORP"}},
        score=6.5,
    )

    found = _tool(await mcp.call_tool("explain_correlation", {"event_id": "evt-explain"}))
    missing = _tool(await mcp.call_tool("explain_correlation", {"event_id": "evt-missing"}))

    assert found["event_id"] == "evt-explain"
    assert found["event_type"] == "sec_flight"
    assert "error" in missing


# ---------------------------------------------------------------------------
# Graph read tools
# ---------------------------------------------------------------------------


async def test_get_entity_graph_maps_entity_and_returns_result(monkeypatch):
    from modules import db as _db

    expected = {"nodes": [{"node_id": "entity:Tesla"}], "edges": []}
    calls: dict = {}

    def _fake_init():
        calls["init_called"] = True

    def _fake_traverse(node_id, depth, limit):
        calls["node_id"] = node_id
        calls["depth"] = depth
        calls["limit"] = limit
        return expected

    monkeypatch.setattr(_db, "init_graph_tables", _fake_init)
    monkeypatch.setattr(_db, "traverse_graph", _fake_traverse)

    result = _tool(await mcp.call_tool("get_entity_graph", {"entity": "Tesla"}))
    assert result == expected
    assert calls["init_called"] is True
    assert calls["node_id"] == "entity:Tesla"
    assert calls["depth"] == 1
    assert calls["limit"] == 100


async def test_get_entity_graph_clamps_depth_and_limit(monkeypatch):
    from modules import db as _db

    calls: dict = {}

    monkeypatch.setattr(_db, "init_graph_tables", lambda: None)

    def _fake_traverse(node_id, depth, limit):
        calls["node_id"] = node_id
        calls["depth"] = depth
        calls["limit"] = limit
        return {"nodes": [], "edges": []}

    monkeypatch.setattr(_db, "traverse_graph", _fake_traverse)

    _tool(await mcp.call_tool("get_entity_graph", {"entity": "Tesla", "depth": 99, "limit": 999}))
    assert calls["node_id"] == "entity:Tesla"
    assert calls["depth"] == 3
    assert calls["limit"] == 100


async def test_get_graph_neighbors_forwards_node_id_and_limit(monkeypatch):
    from modules import db as _db

    expected = [{"node_id": "event:evt1", "label": "evt1"}]
    calls: dict = {}

    monkeypatch.setattr(_db, "init_graph_tables", lambda: None)

    def _fake_neighbors(node_id, limit):
        calls["node_id"] = node_id
        calls["limit"] = limit
        return expected

    monkeypatch.setattr(_db, "fetch_graph_neighbors_nodes", _fake_neighbors)

    result = _tool(
        await mcp.call_tool("get_graph_neighbors", {"node_id": "entity:Tesla", "limit": 25})
    )
    assert result == expected
    assert calls["node_id"] == "entity:Tesla"
    assert calls["limit"] == 25


async def test_get_graph_neighbors_clamps_limit(monkeypatch):
    from modules import db as _db

    calls: dict = {}

    monkeypatch.setattr(_db, "init_graph_tables", lambda: None)

    def _fake_neighbors(node_id, limit):
        calls["node_id"] = node_id
        calls["limit"] = limit
        return []

    monkeypatch.setattr(_db, "fetch_graph_neighbors_nodes", _fake_neighbors)

    _tool(await mcp.call_tool("get_graph_neighbors", {"node_id": "entity:Tesla", "limit": 1000}))
    assert calls["node_id"] == "entity:Tesla"
    assert calls["limit"] == 100


async def test_get_graph_edges_between_forwards_source_and_target(monkeypatch):
    from modules import db as _db

    expected = [{"source_id": "entity:Tesla", "target_id": "event:evt1"}]
    calls: dict = {}

    monkeypatch.setattr(_db, "init_graph_tables", lambda: None)

    def _fake_edges(source_id, target_id):
        calls["source_id"] = source_id
        calls["target_id"] = target_id
        return expected

    monkeypatch.setattr(_db, "fetch_graph_edges_between", _fake_edges)

    result = _tool(
        await mcp.call_tool(
            "get_graph_edges_between",
            {"source_id": "entity:Tesla", "target_id": "event:evt1"},
        )
    )
    assert result == expected
    assert calls["source_id"] == "entity:Tesla"
    assert calls["target_id"] == "event:evt1"


async def test_graph_tools_do_not_call_write_helpers(monkeypatch):
    from modules import db as _db

    monkeypatch.setattr(_db, "init_graph_tables", lambda: None)
    monkeypatch.setattr(
        _db, "traverse_graph", lambda node_id, depth, limit: {"nodes": [], "edges": []}
    )
    monkeypatch.setattr(_db, "fetch_graph_neighbors_nodes", lambda node_id, limit: [])
    monkeypatch.setattr(_db, "fetch_graph_edges_between", lambda source_id, target_id: [])

    write_calls = {"node": 0, "edge": 0}

    def _fail_node(*_args, **_kwargs):
        write_calls["node"] += 1
        raise AssertionError("upsert_graph_node should not be called by graph read MCP tools")

    def _fail_edge(*_args, **_kwargs):
        write_calls["edge"] += 1
        raise AssertionError("upsert_graph_edge should not be called by graph read MCP tools")

    monkeypatch.setattr(_db, "upsert_graph_node", _fail_node)
    monkeypatch.setattr(_db, "upsert_graph_edge", _fail_edge)

    _tool(await mcp.call_tool("get_entity_graph", {"entity": "Tesla"}))
    _tool(await mcp.call_tool("get_graph_neighbors", {"node_id": "entity:Tesla"}))
    _tool(
        await mcp.call_tool(
            "get_graph_edges_between",
            {"source_id": "entity:Tesla", "target_id": "event:evt1"},
        )
    )

    assert write_calls["node"] == 0
    assert write_calls["edge"] == 0


# ---------------------------------------------------------------------------
# explain_entity_graph tool
# ---------------------------------------------------------------------------


async def test_explain_entity_graph_returns_explanation(monkeypatch):
    from modules import db as _db

    graph = {
        "nodes": [
            {"node_id": "entity:Tesla", "node_type": "entity", "label": "Tesla", "properties": {}},
            {
                "node_id": "event:e1",
                "node_type": "event",
                "label": "sec_adsb",
                "properties": {"score": 8},
            },
            {"node_id": "signal:sec", "node_type": "signal", "label": "sec", "properties": {}},
        ],
        "edges": [
            {
                "edge_id": "entity:Tesla->event:e1",
                "source_id": "entity:Tesla",
                "target_id": "event:e1",
                "relation": "involved_in",
                "weight": 0.9,
                "properties": {},
            },
            {
                "edge_id": "signal:sec->event:e1",
                "source_id": "signal:sec",
                "target_id": "event:e1",
                "relation": "contributed_to",
                "weight": 1.0,
                "properties": {},
            },
        ],
    }

    monkeypatch.setattr(_db, "init_graph_tables", lambda: None)
    monkeypatch.setattr(_db, "traverse_graph", lambda node_id, depth, limit: graph)

    result = _tool(await mcp.call_tool("explain_entity_graph", {"entity": "Tesla"}))

    assert result["entity"] == "Tesla"
    assert result["node_count"] == 3
    assert result["edge_count"] == 2
    assert "Tesla" in result["explanation"]
    assert "SEC filing" in result["explanation"]


async def test_explain_entity_graph_empty_graph(monkeypatch):
    from modules import db as _db

    monkeypatch.setattr(_db, "init_graph_tables", lambda: None)
    monkeypatch.setattr(
        _db, "traverse_graph", lambda node_id, depth, limit: {"nodes": [], "edges": []}
    )

    result = _tool(await mcp.call_tool("explain_entity_graph", {"entity": "Unknown"}))

    assert result["entity"] == "Unknown"
    assert result["node_count"] == 0
    assert result["edge_count"] == 0
    assert "no nodes" in result["explanation"].lower()


async def test_explain_entity_graph_clamps_depth_and_limit(monkeypatch):
    from modules import db as _db

    calls: dict = {}

    monkeypatch.setattr(_db, "init_graph_tables", lambda: None)

    def _fake_traverse(node_id, depth, limit):
        calls["node_id"] = node_id
        calls["depth"] = depth
        calls["limit"] = limit
        return {"nodes": [], "edges": []}

    monkeypatch.setattr(_db, "traverse_graph", _fake_traverse)

    _tool(
        await mcp.call_tool("explain_entity_graph", {"entity": "Tesla", "depth": 99, "limit": 999})
    )

    assert calls["node_id"] == "entity:Tesla"
    assert calls["depth"] == 3
    assert calls["limit"] == 100


async def test_explain_entity_graph_is_read_only(monkeypatch):
    from modules import db as _db

    monkeypatch.setattr(_db, "init_graph_tables", lambda: None)
    monkeypatch.setattr(
        _db, "traverse_graph", lambda node_id, depth, limit: {"nodes": [], "edges": []}
    )

    def _fail(*_args, **_kwargs):
        raise AssertionError("write helper must not be called by explain_entity_graph")

    monkeypatch.setattr(_db, "upsert_graph_node", _fail)
    monkeypatch.setattr(_db, "upsert_graph_edge", _fail)

    _tool(await mcp.call_tool("explain_entity_graph", {"entity": "Tesla"}))


# ---------------------------------------------------------------------------
# ping tool
# ---------------------------------------------------------------------------


async def test_ping_returns_ok():
    result = _tool(await mcp.call_tool("ping", {}))
    assert result["status"] == "ok"
    assert result["server"] == "lighthouse-mcp"


async def test_ping_includes_config_flags():
    result = _tool(await mcp.call_tool("ping", {}))
    assert "nostr_configured" in result
    assert "llm_configured" in result
    assert "db_path" in result
    assert isinstance(result["nostr_configured"], bool)


# ---------------------------------------------------------------------------
# build_and_preview_note tool
# ---------------------------------------------------------------------------


async def test_preview_unknown_note_type():
    result = _tool(
        await mcp.call_tool(
            "build_and_preview_note",
            {
                "note_type": "invalid",
                "payload": {},
            },
        )
    )
    assert "error" in result
    assert "required_fields" in result


async def test_preview_missing_fields():
    result = _tool(
        await mcp.call_tool(
            "build_and_preview_note",
            {
                "note_type": "patent",
                "payload": {"patent_id": "US1"},
            },
        )
    )
    assert "error" in result
    assert "title" in result["error"]


async def test_preview_archive_note_valid():
    result = _tool(
        await mcp.call_tool(
            "build_and_preview_note",
            {
                "note_type": "archive",
                "payload": {
                    "agency": "DoD",
                    "url": "https://defense.gov/news/",
                    "ipfs_cid": "bafytest123",
                    "snapshot_ts": "2026-01-01T00:00:00Z",
                    "matched_keywords": ["personnel", "strategic"],
                },
            },
        )
    )
    assert result["unsigned"] is True
    assert "[ARCHIVE] DoD" in result["content"]
    assert result["note_type"] == "archive"
    assert isinstance(result["tags"], list)


async def test_preview_sar_zone_note():
    result = _tool(
        await mcp.call_tool(
            "build_and_preview_note",
            {
                "note_type": "sar_zone",
                "payload": {
                    "incident_date": "2026-04-01",
                    "agency": "USCG",
                    "bounding_box": {"north": 34.5, "south": 33.8, "east": -117.2, "west": -118.1},
                    "source_url": "https://navcen.uscg.gov/",
                    "description": "Public SAR broadcast",
                },
            },
        )
    )
    assert "[SAR-ZONE]" in result["content"]
    assert result["unsigned"] is True


async def test_preview_dms_broadcast_note():
    result = _tool(
        await mcp.call_tool(
            "build_and_preview_note",
            {
                "note_type": "dms_broadcast",
                "payload": {
                    "display_name": "Test Researcher",
                    "encrypted_message": "encrypted-blob-here",
                    "registration_id": "abc-123",
                },
            },
        )
    )
    assert "[DMS-BROADCAST]" in result["content"]


# ---------------------------------------------------------------------------
# publish_note tool
# ---------------------------------------------------------------------------


async def test_publish_note_no_key_set():
    result = _tool(
        await mcp.call_tool(
            "publish_note",
            {
                "note_type": "archive",
                "payload": {
                    "agency": "DoD",
                    "url": "https://defense.gov/",
                    "ipfs_cid": "bafytest",
                    "snapshot_ts": "2026-01-01T00:00:00Z",
                    "matched_keywords": ["strategic"],
                },
                "dry_run": True,
            },
        )
    )
    assert "error" in result
    assert "NOSTR_PRIVATE_KEY_HEX" in result["error"]


async def test_publish_note_validation_error():
    result = _tool(
        await mcp.call_tool(
            "publish_note",
            {
                "note_type": "bad_type",
                "payload": {},
                "dry_run": True,
            },
        )
    )
    assert "error" in result


async def test_publish_note_dry_run_with_key(monkeypatch):
    monkeypatch.setenv(
        "NOSTR_PRIVATE_KEY_HEX", "0101010101010101010101010101010101010101010101010101010101010101"
    )
    # reload config so the key is picked up
    import importlib

    import config as cfg_module
    import portal.mcp_server as srv_module

    importlib.reload(cfg_module)
    importlib.reload(srv_module)

    from portal.mcp_server import mcp as reloaded_mcp

    result = _tool(
        await reloaded_mcp.call_tool(
            "publish_note",
            {
                "note_type": "velocity",
                "payload": {
                    "ipc_class": "G21B",
                    "anomaly_type": "spike",
                    "week": "2026-W17",
                    "zscore": 3.4,
                    "context": "count=12, mean=3.1",
                },
                "dry_run": True,
            },
        )
    )
    assert result["dry_run"] is True
    assert len(result["event_id"]) == 64
    assert result["note_type"] == "velocity"
    assert isinstance(result["relays"], list)


async def test_publish_note_live_requires_human_approval(monkeypatch):
    monkeypatch.setenv(
        "NOSTR_PRIVATE_KEY_HEX", "0101010101010101010101010101010101010101010101010101010101010101"
    )
    monkeypatch.setenv("LIGHTHOUSE_APPROVAL_SECRET", "top-secret")
    import importlib

    import config as cfg_module
    import portal.mcp_server as srv_module

    importlib.reload(cfg_module)
    importlib.reload(srv_module)

    from portal.mcp_server import mcp as reloaded_mcp

    result = _tool(
        await reloaded_mcp.call_tool(
            "publish_note",
            {
                "note_type": "velocity",
                "payload": {
                    "ipc_class": "G21B",
                    "anomaly_type": "spike",
                    "week": "2026-W17",
                    "zscore": 3.4,
                    "context": "count=12, mean=3.1",
                },
                "dry_run": False,
            },
        )
    )
    assert "error" in result
    assert "Human approval required" in result["error"]


async def test_run_module_live_requires_human_approval(monkeypatch):
    monkeypatch.setenv("LIGHTHOUSE_APPROVAL_SECRET", "top-secret")
    import importlib

    import config as cfg_module
    import portal.mcp_server as srv_module

    importlib.reload(cfg_module)
    importlib.reload(srv_module)

    from portal.mcp_server import mcp as reloaded_mcp

    result = _tool(
        await reloaded_mcp.call_tool(
            "run_module",
            {"module_name": "patents", "dry_run": False, "since_days": 1},
        )
    )
    assert "error" in result
    assert "Human approval required" in result["error"]


# ---------------------------------------------------------------------------
# LHP-SECURITY-01 — fail-closed human-approval gate
#
# Contract: _is_human_approved() returns True ONLY when a non-empty approval
# secret is configured AND the supplied token matches the derived expected
# token via a timing-safe comparison. Any uncertainty MUST deny.
# ---------------------------------------------------------------------------


def _correct_token(secret: str) -> str:
    """Reconstruct the expected token the same way the server derives it."""
    import hashlib

    return hashlib.sha256(secret.encode("utf-8")).hexdigest()[:24]


def test_approval_denied_when_secret_missing(monkeypatch):
    monkeypatch.delenv("LIGHTHOUSE_APPROVAL_SECRET", raising=False)
    from portal.mcp_server import _is_human_approved

    assert _is_human_approved("anything") is False
    assert _is_human_approved(None) is False


def test_approval_denied_when_secret_empty(monkeypatch):
    monkeypatch.setenv("LIGHTHOUSE_APPROVAL_SECRET", "")
    from portal.mcp_server import _is_human_approved

    assert _is_human_approved("anything") is False


def test_approval_denied_when_secret_whitespace(monkeypatch):
    monkeypatch.setenv("LIGHTHOUSE_APPROVAL_SECRET", "   \t\n")
    from portal.mcp_server import _is_human_approved

    assert _is_human_approved("anything") is False


def test_approval_denied_when_token_missing(monkeypatch):
    monkeypatch.setenv("LIGHTHOUSE_APPROVAL_SECRET", "top-secret")
    from portal.mcp_server import _is_human_approved

    assert _is_human_approved(None) is False


def test_approval_denied_when_token_empty(monkeypatch):
    monkeypatch.setenv("LIGHTHOUSE_APPROVAL_SECRET", "top-secret")
    from portal.mcp_server import _is_human_approved

    assert _is_human_approved("") is False


def test_approval_denied_when_token_whitespace(monkeypatch):
    monkeypatch.setenv("LIGHTHOUSE_APPROVAL_SECRET", "top-secret")
    from portal.mcp_server import _is_human_approved

    assert _is_human_approved("   ") is False


def test_approval_denied_when_token_incorrect(monkeypatch):
    monkeypatch.setenv("LIGHTHOUSE_APPROVAL_SECRET", "top-secret")
    from portal.mcp_server import _is_human_approved

    assert _is_human_approved("not-the-right-token") is False


def test_approval_granted_when_token_correct(monkeypatch):
    monkeypatch.setenv("LIGHTHOUSE_APPROVAL_SECRET", "top-secret")
    from portal.mcp_server import _is_human_approved

    assert _is_human_approved(_correct_token("top-secret")) is True


def test_approval_denied_on_config_read_failure(monkeypatch):
    monkeypatch.setenv("LIGHTHOUSE_APPROVAL_SECRET", "top-secret")
    import portal.mcp_server as srv

    def _boom() -> str | None:
        raise OSError("configuration unreadable")

    monkeypatch.setattr(srv, "_expected_approval_token", _boom)
    assert srv._is_human_approved(_correct_token("top-secret")) is False


def test_approval_denied_on_unexpected_exception(monkeypatch):
    monkeypatch.setenv("LIGHTHOUSE_APPROVAL_SECRET", "top-secret")
    import portal.mcp_server as srv

    def _explode(*_args, **_kwargs):
        raise RuntimeError("unexpected")

    monkeypatch.setattr(srv.hmac, "compare_digest", _explode)
    assert srv._is_human_approved(_correct_token("top-secret")) is False


def test_approval_uses_timing_safe_comparison(monkeypatch):
    monkeypatch.setenv("LIGHTHOUSE_APPROVAL_SECRET", "top-secret")
    import portal.mcp_server as srv

    calls: list[tuple] = []
    real = srv.hmac.compare_digest

    def _spy(a, b):
        calls.append((a, b))
        return real(a, b)

    monkeypatch.setattr(srv.hmac, "compare_digest", _spy)
    assert srv._is_human_approved(_correct_token("top-secret")) is True
    assert calls, "expected hmac.compare_digest to be used for token comparison"


def test_approval_never_leaks_secret_or_token_in_logs(monkeypatch, caplog):
    monkeypatch.setenv("LIGHTHOUSE_APPROVAL_SECRET", "top-secret")
    from portal.mcp_server import _is_human_approved

    with caplog.at_level("DEBUG"):
        _is_human_approved("super-sneaky-token-value")
        _is_human_approved(None)

    blob = "\n".join(r.getMessage() for r in caplog.records)
    assert "top-secret" not in blob
    assert "super-sneaky-token-value" not in blob
    assert _correct_token("top-secret") not in blob


# --- Regression: original fail-OPEN behavior (approval granted with no secret) ---


def test_regression_no_secret_must_not_auto_approve(monkeypatch):
    """Before LHP-SECURITY-01, a missing secret made _is_human_approved return
    True, auto-approving every live action. It must now deny."""
    monkeypatch.delenv("LIGHTHOUSE_APPROVAL_SECRET", raising=False)
    from portal.mcp_server import _is_human_approved

    assert _is_human_approved(None) is False


async def test_run_module_live_denied_when_no_secret_configured(monkeypatch):
    monkeypatch.delenv("LIGHTHOUSE_APPROVAL_SECRET", raising=False)
    import importlib

    import config as cfg_module
    import portal.mcp_server as srv_module

    importlib.reload(cfg_module)
    importlib.reload(srv_module)

    from portal.mcp_server import mcp as reloaded_mcp

    result = _tool(
        await reloaded_mcp.call_tool(
            "run_module",
            {"module_name": "patents", "dry_run": False, "since_days": 1},
        )
    )
    assert "error" in result
    assert "Human approval required" in result["error"]


async def test_run_pipeline_live_denied_when_no_secret_configured(monkeypatch):
    monkeypatch.delenv("LIGHTHOUSE_APPROVAL_SECRET", raising=False)
    import importlib

    import config as cfg_module
    import portal.mcp_server as srv_module

    importlib.reload(cfg_module)
    importlib.reload(srv_module)

    from portal.mcp_server import mcp as reloaded_mcp

    result = _tool(
        await reloaded_mcp.call_tool(
            "run_pipeline",
            {"since_days": 1, "dry_run": False},
        )
    )
    assert "error" in result
    assert "Human approval required" in result["error"]


async def test_publish_note_live_denied_when_no_secret_configured(monkeypatch):
    monkeypatch.setenv(
        "NOSTR_PRIVATE_KEY_HEX", "0101010101010101010101010101010101010101010101010101010101010101"
    )
    monkeypatch.delenv("LIGHTHOUSE_APPROVAL_SECRET", raising=False)
    import importlib

    import config as cfg_module
    import portal.mcp_server as srv_module

    importlib.reload(cfg_module)
    importlib.reload(srv_module)

    from portal.mcp_server import mcp as reloaded_mcp

    result = _tool(
        await reloaded_mcp.call_tool(
            "publish_note",
            {
                "note_type": "velocity",
                "payload": {
                    "ipc_class": "G21B",
                    "anomaly_type": "spike",
                    "week": "2026-W17",
                    "zscore": 3.4,
                    "context": "count=12, mean=3.1",
                },
                "dry_run": False,
            },
        )
    )
    assert "error" in result
    assert "Human approval required" in result["error"]


async def test_publish_note_dry_run_unchanged_without_secret_or_token(monkeypatch):
    """Dry-run must never touch the approval gate: no secret, no token, still
    returns the signed event without broadcasting."""
    monkeypatch.setenv(
        "NOSTR_PRIVATE_KEY_HEX", "0101010101010101010101010101010101010101010101010101010101010101"
    )
    monkeypatch.delenv("LIGHTHOUSE_APPROVAL_SECRET", raising=False)
    import importlib

    import config as cfg_module
    import portal.mcp_server as srv_module

    importlib.reload(cfg_module)
    importlib.reload(srv_module)

    from portal.mcp_server import mcp as reloaded_mcp

    result = _tool(
        await reloaded_mcp.call_tool(
            "publish_note",
            {
                "note_type": "velocity",
                "payload": {
                    "ipc_class": "G21B",
                    "anomaly_type": "spike",
                    "week": "2026-W17",
                    "zscore": 3.4,
                    "context": "count=12, mean=3.1",
                },
                "dry_run": True,
            },
        )
    )
    assert result.get("dry_run") is True
    assert "error" not in result
    assert len(result["event_id"]) == 64


# ---------------------------------------------------------------------------
# Read tools — return lists (empty is fine, no network needed)
# ---------------------------------------------------------------------------


async def test_query_patents_returns_list():
    result = _tool(await mcp.call_tool("query_patents", {}))
    assert isinstance(result, list)


async def test_query_patents_flagged_only_filter():
    result = _tool(await mcp.call_tool("query_patents", {"flagged_only": True}))
    assert isinstance(result, list)
    assert all(r.get("_flagged") for r in result)


async def test_query_flights_returns_list():
    result = _tool(await mcp.call_tool("query_flights", {}))
    assert isinstance(result, list)


async def test_query_researchers_returns_list():
    result = _tool(await mcp.call_tool("query_researchers", {}))
    assert isinstance(result, list)


async def test_query_velocity_returns_list():
    result = _tool(await mcp.call_tool("query_velocity", {}))
    assert isinstance(result, list)


async def test_list_archived_pages_returns_list():
    result = _tool(await mcp.call_tool("list_archived_pages", {}))
    assert isinstance(result, list)
    # local_path must be stripped
    for page in result:
        assert "local_path" not in page


async def test_get_dms_registrations_returns_list():
    result = _tool(await mcp.call_tool("get_dms_registrations", {}))
    assert isinstance(result, list)
    # encrypted_message must never appear
    for reg in result:
        assert "encrypted_message" not in reg
        # computed fields must be present if any rows exist
        if result:
            assert "next_deadline" in reg
            assert "overdue" in reg


# ---------------------------------------------------------------------------
# Config resources
# ---------------------------------------------------------------------------


async def test_resource_patent_classes_is_list():
    content = _resource(await mcp.read_resource("config://patent-classes"))
    classes = json.loads(content)
    assert isinstance(classes, list)
    assert all(isinstance(c, str) for c in classes)
    assert "G21B" in classes


async def test_resource_assignees_is_list():
    content = _resource(await mcp.read_resource("config://assignees"))
    assignees = json.loads(content)
    assert isinstance(assignees, list)
    assert any("MIT" in a for a in assignees)


async def test_resource_facilities_has_required_keys():
    content = _resource(await mcp.read_resource("config://facilities"))
    facilities = json.loads(content)
    assert isinstance(facilities, list)
    assert len(facilities) > 0
    for f in facilities:
        for key in ("name", "lat", "lon", "radius_km"):
            assert key in f, f"Missing key '{key}' in facility {f}"


async def test_resource_archive_targets_has_agencies():
    content = _resource(await mcp.read_resource("config://archive-targets"))
    targets = json.loads(content)
    agencies = [t["agency"] for t in targets]
    assert "DoD" in agencies
    assert "NASA" in agencies
    assert "DoE" in agencies


async def test_resource_state_schema_has_all_tables():
    content = _resource(await mcp.read_resource("state://schema"))
    expected_tables = [
        "seen",
        "patent_velocity",
        "researchers",
        "narrative_scores",
        "flight_observations",
        "dms_registrations",
        "dms_broadcasts",
    ]
    for table in expected_tables:
        assert table in content, f"Table '{table}' missing from schema resource"
