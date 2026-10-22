"""Witness helpers for signing correlation verification claims."""

import hashlib
import json

from modules._schnorr import schnorr_sign


def build_witness_claim(event: dict) -> dict:
    """Build canonical claim payload from a correlation event."""
    return {
        "event_id": event.get("event_id"),
        "event_type": event.get("event_type"),
        "entities": sorted(set(event.get("entities") or [])),
        "signals": sorted(set(event.get("signals") or [])),
        "score": float(event.get("score", 0.0)),
        "verification_status": event.get("verification_status", "unverified"),
        "source_trust_score": float(event.get("source_trust_score", 0.0)),
        "is_single_source": bool(event.get("is_single_source", False)),
        "observed_at": event.get("observed_at"),
    }


def witness_hash_hex(claim: dict) -> str:
    """Return deterministic SHA-256 hash (hex) for a witness claim."""
    raw = json.dumps(claim, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def sign_witness_hash_hex(hash_hex: str, private_key_hex: str) -> str:
    """Sign witness hash using local BIP-340 helper; returns signature hex."""
    msg = bytes.fromhex(hash_hex)
    seckey = bytes.fromhex(private_key_hex)
    return schnorr_sign(msg=msg, seckey=seckey).hex()


def attach_witness(event: dict, private_key_hex: str | None = None) -> dict:
    """Attach witness hash/signature fields to an event dict."""
    claim = build_witness_claim(event)
    digest = witness_hash_hex(claim)
    signature = None
    if private_key_hex:
        signature = sign_witness_hash_hex(digest, private_key_hex)
    return {
        **event,
        "witness_hash": digest,
        "witness_signature": signature,
    }
