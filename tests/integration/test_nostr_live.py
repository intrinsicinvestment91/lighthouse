"""
Integration tests — publish to live Nostr relays.

Run with:  pytest tests/integration -m integration -v
Requires NOSTR_PRIVATE_KEY_HEX to be set.
"""

import os
import time

import pytest

from modules.nostr_relay import (
    NostrEvent,
    _load_private_key,
    _pubkey_hex,
    publish_to_relay,
)


@pytest.mark.integration
@pytest.mark.skipif(not os.getenv("NOSTR_PRIVATE_KEY_HEX"), reason="NOSTR_PRIVATE_KEY_HEX not set")
async def test_publish_to_relay_live():
    """Publish a test kind-1 event to damus.io and verify it is accepted."""
    priv = _load_private_key(os.environ["NOSTR_PRIVATE_KEY_HEX"])
    pub = _pubkey_hex(priv)

    ev = NostrEvent(
        pubkey=pub,
        kind=1,
        content="[lighthouse-sentinel integration test] ignore this event",
        tags=[["t", "test"]],
        created_at=int(time.time()),
    )
    ev.sign(priv)

    result = await publish_to_relay(ev, "wss://relay.damus.io")
    assert result is True
