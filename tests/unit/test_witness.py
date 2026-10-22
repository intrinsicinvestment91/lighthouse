"""Unit tests for witness claim hashing and signing."""

from modules.witness import (
    attach_witness,
    build_witness_claim,
    sign_witness_hash_hex,
    witness_hash_hex,
)


def _sample_event() -> dict:
    return {
        "event_id": "evt_1",
        "event_type": "sec_flight",
        "entities": ["Lockheed Martin"],
        "signals": ["sec", "flight"],
        "score": 8.5,
        "verification_status": "corroborated",
        "source_trust_score": 0.8,
        "is_single_source": False,
        "observed_at": 1_777_777_777,
    }


def test_witness_hash_is_deterministic():
    claim = build_witness_claim(_sample_event())
    hash_a = witness_hash_hex(claim)
    hash_b = witness_hash_hex(claim)
    assert hash_a == hash_b
    assert len(hash_a) == 64


def test_witness_hash_changes_when_claim_changes():
    claim_a = build_witness_claim(_sample_event())
    event_b = _sample_event()
    event_b["score"] = 8.6
    claim_b = build_witness_claim(event_b)
    assert witness_hash_hex(claim_a) != witness_hash_hex(claim_b)


def test_sign_witness_hash_with_test_key(nostr_test_key_hex):
    claim = build_witness_claim(_sample_event())
    digest = witness_hash_hex(claim)
    signature = sign_witness_hash_hex(digest, nostr_test_key_hex)
    assert len(signature) == 128


def test_attach_witness_without_key_has_hash_only():
    event = attach_witness(_sample_event())
    assert event["witness_hash"]
    assert event["witness_signature"] is None


def test_attach_witness_with_key_has_signature(nostr_test_key_hex):
    event = attach_witness(_sample_event(), private_key_hex=nostr_test_key_hex)
    assert event["witness_hash"]
    assert event["witness_signature"]
    assert len(event["witness_signature"]) == 128
