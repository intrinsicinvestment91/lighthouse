"""Unit tests for modules/nostr_relay.py"""

import hashlib
import json

import pytest

from modules.nostr_relay import (
    NostrEvent,
    _load_private_key,
    _pubkey_hex,
    _schnorr_sign,
    broadcast_node_heartbeat,
    build_archive_note,
    build_correlation_note,
    build_node_heartbeat_note,
    build_patent_note,
    build_sar_zone_note,
    build_transcript_note,
    enrich_mesh_tags,
)


@pytest.fixture
def priv_key(nostr_test_key_hex):
    return _load_private_key(nostr_test_key_hex)


def test_pubkey_hex_is_64_chars(priv_key):
    pubkey = _pubkey_hex(priv_key)
    assert len(pubkey) == 64
    int(pubkey, 16)  # must be valid hex


def test_schnorr_sign_returns_128_char_hex(priv_key):
    msg = bytes(32)  # 32 zero bytes
    sig = _schnorr_sign(priv_key, msg)
    assert len(sig) == 128
    int(sig, 16)  # must be valid hex


def test_compute_event_id_canonical_format(nostr_test_key_hex):
    priv = _load_private_key(nostr_test_key_hex)
    pub = _pubkey_hex(priv)
    ev = NostrEvent(
        pubkey=pub,
        kind=1,
        content="test content",
        tags=[["t", "test"]],
        created_at=1_700_000_000,
    )
    ev.id = ev.compute_id()

    # Recompute manually to verify canonical serialization order
    canonical = json.dumps(
        [0, pub, 1_700_000_000, 1, [["t", "test"]], "test content"],
        separators=(",", ":"),
        ensure_ascii=False,
    )
    expected = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    assert ev.id == expected


def test_sign_sets_id_and_sig(nostr_test_key_hex):
    priv = _load_private_key(nostr_test_key_hex)
    pub = _pubkey_hex(priv)
    ev = NostrEvent(pubkey=pub, kind=1, content="hello", tags=[], created_at=1_700_000_000)
    ev.sign(priv)
    assert len(ev.id) == 64
    assert len(ev.sig) == 128


def test_to_wire_has_all_nip01_fields(nostr_test_key_hex):
    priv = _load_private_key(nostr_test_key_hex)
    pub = _pubkey_hex(priv)
    ev = NostrEvent(pubkey=pub, kind=1, content="x", tags=[], created_at=1_700_000_000)
    ev.sign(priv)
    wire = ev.to_wire()
    for field in ("id", "pubkey", "created_at", "kind", "tags", "content", "sig"):
        assert field in wire


def test_build_patent_note_tags(nostr_test_key_hex):
    priv = _load_private_key(nostr_test_key_hex)
    pub = _pubkey_hex(priv)
    ev = build_patent_note(
        pubkey=pub,
        patent_id="US123456",
        title="Fusion Device",
        assignee="General Atomics",
        filing_date="2026-01-01",
        ipc_class="G21B",
        source="USPTO",
    )
    tag_types = [t[0] for t in ev.tags]
    assert "t" in tag_types
    tag_values = [t[1] for t in ev.tags if t[0] == "t"]
    assert "patent" in tag_values
    assert "g21b" in tag_values
    assert "uspto" in tag_values


def test_build_patent_note_content_includes_fields(nostr_test_key_hex):
    priv = _load_private_key(nostr_test_key_hex)
    pub = _pubkey_hex(priv)
    ev = build_patent_note(
        pubkey=pub,
        patent_id="US123456",
        title="Fusion Device",
        assignee="General Atomics",
        filing_date="2026-01-01",
        ipc_class="G21B",
        source="USPTO",
    )
    assert "[PATENT]" in ev.content
    assert "General Atomics" in ev.content
    assert "G21B" in ev.content


def test_build_archive_note_content(nostr_test_key_hex):
    priv = _load_private_key(nostr_test_key_hex)
    pub = _pubkey_hex(priv)
    ev = build_archive_note(
        pubkey=pub,
        agency="DoD",
        url="https://www.defense.gov/example/",
        ipfs_cid="bafy123",
        snapshot_ts="20260101T000000Z",
        matched_keywords=["strategic", "personnel"],
    )
    assert ev.content.startswith("[ARCHIVE] DoD")
    assert "ipfs://bafy123" in ev.content
    assert any(t == ["ipfs", "bafy123"] for t in ev.tags)


def test_build_sar_zone_note_bbox(nostr_test_key_hex):
    priv = _load_private_key(nostr_test_key_hex)
    pub = _pubkey_hex(priv)
    ev = build_sar_zone_note(
        pubkey=pub,
        incident_date="2026-01-15",
        agency="USCG",
        bounding_box={"north": 34.5, "south": 33.8, "east": -117.2, "west": -118.1},
        source_url="https://www.navcen.uscg.gov/",
        description="Test SAR",
    )
    assert "[SAR-ZONE]" in ev.content
    assert "N34.5000" in ev.content


def test_build_transcript_note_tags_and_content(nostr_test_key_hex):
    priv = _load_private_key(nostr_test_key_hex)
    pub = _pubkey_hex(priv)
    ev = build_transcript_note(
        pubkey=pub,
        committee="Senate Armed Services",
        hearing_date="2026-02-01",
        transcript_url="https://www.armed-services.senate.gov/hearings/transcript",
        summary="Classified drone program review",
    )
    assert "[TRANSCRIPT]" in ev.content
    assert "Senate Armed Services" in ev.content
    assert any(t == ["t", "transcript"] for t in ev.tags)
    assert any(t == ["t", "congressional"] for t in ev.tags)
    assert any(t[0] == "r" for t in ev.tags)


def test_build_transcript_note_with_ipfs_cid(nostr_test_key_hex):
    priv = _load_private_key(nostr_test_key_hex)
    pub = _pubkey_hex(priv)
    ev = build_transcript_note(
        pubkey=pub,
        committee="Senate Armed Services",
        hearing_date="2026-02-01",
        transcript_url="https://example.com/transcript",
        summary="summary",
        ipfs_cid="bafytest",
    )
    assert "ipfs://bafytest" in ev.content
    assert any(t == ["ipfs", "bafytest"] for t in ev.tags)


def test_build_patent_note_with_ipfs_cid(nostr_test_key_hex):
    priv = _load_private_key(nostr_test_key_hex)
    pub = _pubkey_hex(priv)
    ev = build_patent_note(
        pubkey=pub,
        patent_id="US123456",
        title="Fusion Device",
        assignee="MIT",
        filing_date="2026-01-01",
        ipc_class="G21B",
        source="USPTO",
        ipfs_cid="bafyipfstest",
    )
    assert "ipfs://bafyipfstest" in ev.content
    assert any(t == ["ipfs", "bafyipfstest"] for t in ev.tags)


def test_enrich_mesh_tags_adds_node_and_region(monkeypatch):
    monkeypatch.setattr("modules.nostr_relay.nostr_cfg.node_region", "us-east")
    ev = NostrEvent(pubkey="", kind=1, content="x", tags=[["t", "archive"]], created_at=1)
    enrich_mesh_tags(ev, "abc123")
    assert ["node", "abc123"] in ev.tags
    assert ["region", "us-east"] in ev.tags


def test_enrich_mesh_tags_skips_empty_region(monkeypatch):
    monkeypatch.setattr("modules.nostr_relay.nostr_cfg.node_region", "   ")
    ev = NostrEvent(pubkey="", kind=1, content="x", tags=[["t", "archive"]], created_at=1)
    enrich_mesh_tags(ev, "abc123")
    assert ["node", "abc123"] in ev.tags
    assert all(tag[0] != "region" for tag in ev.tags)


def test_enrich_mesh_tags_is_idempotent(monkeypatch):
    monkeypatch.setattr("modules.nostr_relay.nostr_cfg.node_region", "us-east")
    ev = NostrEvent(
        pubkey="",
        kind=1,
        content="x",
        tags=[["node", "old"], ["region", "old"], ["t", "archive"], ["node", "old2"]],
        created_at=1,
    )
    enrich_mesh_tags(ev, "abc123")
    assert ev.tags.count(["node", "abc123"]) == 1
    assert ev.tags.count(["region", "us-east"]) == 1


def test_build_correlation_note_tags_and_content(nostr_test_key_hex):
    priv = _load_private_key(nostr_test_key_hex)
    pub = _pubkey_hex(priv)
    ev = build_correlation_note(
        pubkey=pub,
        event_type="sec_adsb_flight",
        entities=["Lockheed Martin"],
        score=8.2,
        signals=["sec", "adsb", "flight"],
        summary="Lockheed Martin correlated across sec, adsb, flight (score 8.2/10).",
    )
    assert "Lockheed Martin" in ev.content
    assert "8.2/10" in ev.content
    assert "high confidence" in ev.content
    assert "SEC 8-K filing" in ev.content
    assert "score 8.2/10" in ev.content
    assert any(t == ["t", "correlation"] for t in ev.tags)
    assert any(t == ["t", "lighthouse"] for t in ev.tags)
    assert any(t == ["score", "high"] for t in ev.tags)
    assert any(t == ["signal", "adsb"] for t in ev.tags)
    assert any(t == ["entity", "Lockheed Martin"] for t in ev.tags)


def test_build_node_heartbeat_note_kind_and_tags(nostr_test_key_hex):
    priv = _load_private_key(nostr_test_key_hex)
    pub = _pubkey_hex(priv)
    ev = build_node_heartbeat_note(pubkey=pub, region="us-east", modules_active=["sec", "adsb"])
    assert ev.kind == 30078
    assert any(t == ["t", "lighthouse-node"] for t in ev.tags)
    assert any(t[0] == "d" and t[1] == pub for t in ev.tags)
    assert any(t[0] == "node" and t[1] == pub for t in ev.tags)
    assert any(t[0] == "timestamp" for t in ev.tags)


def test_build_node_heartbeat_note_content_is_human_readable(nostr_test_key_hex):
    priv = _load_private_key(nostr_test_key_hex)
    pub = _pubkey_hex(priv)
    ev = build_node_heartbeat_note(pubkey=pub, region="", modules_active=["patents", "flight"])
    assert "Lighthouse node online" in ev.content
    assert "patents" in ev.content
    assert "flight" in ev.content


def test_build_node_heartbeat_note_region_tag_present_when_set(nostr_test_key_hex, monkeypatch):
    monkeypatch.setattr("modules.nostr_relay.nostr_cfg.node_region", "eu-west")
    priv = _load_private_key(nostr_test_key_hex)
    pub = _pubkey_hex(priv)
    ev = build_node_heartbeat_note(pubkey=pub, region="eu-west", modules_active=[])
    assert any(t == ["region", "eu-west"] for t in ev.tags)


async def test_broadcast_node_heartbeat_returns_empty_when_no_key(monkeypatch):
    monkeypatch.setattr("modules.nostr_relay.nostr_cfg.private_key_hex", "")
    result = await broadcast_node_heartbeat(pubkey="abc", region="", modules_active=["sec"])
    assert result == {}


async def test_broadcast_node_heartbeat_calls_broadcast_when_key_set(
    monkeypatch, nostr_test_key_hex
):
    priv = _load_private_key(nostr_test_key_hex)
    pub = _pubkey_hex(priv)
    monkeypatch.setattr("modules.nostr_relay.nostr_cfg.private_key_hex", nostr_test_key_hex)

    broadcast_calls: list = []

    async def _fake_broadcast(event):
        broadcast_calls.append(event)
        return {"wss://relay.test": True}

    monkeypatch.setattr("modules.nostr_relay.broadcast", _fake_broadcast)

    result = await broadcast_node_heartbeat(pubkey=pub, region="", modules_active=["sec"])
    assert result == {"wss://relay.test": True}
    assert len(broadcast_calls) == 1
    assert broadcast_calls[0].kind == 30078
