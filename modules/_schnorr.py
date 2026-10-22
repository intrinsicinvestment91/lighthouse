# Provenance: probably adapted from (not proven to be a copy of) the BIP-340 reference
# implementation, bitcoin/bips bip-0340/reference.py at commit
# 130b8e0fa7f30fa45532f080b87b8db27a79e46e (file last changed in commit f98774a6).
# Upstream licence (bip-0340/LICENSE): BSD-2-Clause OR MIT OR CC0-1.0, at the recipient's
# choice. Used here under the MIT License. Upstream authors (per the BIP-340 header):
# Pieter Wuille, Jonas Nick, Tim Ruffing. The upstream file carries no copyright line of
# its own. The full MIT permission notice is in THIRD_PARTY_NOTICES.md at the repository
# root.
"""
Pure-Python BIP-340 Schnorr signing over secp256k1 -- DEMONSTRATION / TEST HELPER ONLY.

Reference: https://github.com/bitcoin/bips/blob/master/bip-0340.mediawiki

Not for production keys. Like the BIP-340 reference implementation it follows, which
BIP-340 says is "for demonstration purposes only and not to be used in production
environments", this code is naive and non-constant-time. It uses a fixed all-zero
32-byte aux_rand (BIP-340 auxiliary randomness, which BIP-340 permits while recommending
fresh randomness); this is not a zero signing nonce. For any real key use a vetted
library such as `coincurve` (as modules/nostr_relay.py does).
"""

import hashlib

# secp256k1 curve parameters
_P = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEFFFFFC2F
_N = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141
_G = (
    0x79BE667EF9DCBBAC55A06295CE870B07029BFCDB2DCE28D959F2815B16F81798,
    0x483ADA7726A3C4655DA4FBFC0E1108A8FD17B448A68554199C47D08FFB10D4B8,
)


def _point_add(P1, P2):
    if P1 is None:
        return P2
    if P2 is None:
        return P1
    if P1[0] == P2[0]:
        if P1[1] != P2[1]:
            return None
        lam = (3 * P1[0] * P1[0] * pow(2 * P1[1], _P - 2, _P)) % _P
    else:
        lam = ((P2[1] - P1[1]) * pow(P2[0] - P1[0], _P - 2, _P)) % _P
    x3 = (lam * lam - P1[0] - P2[0]) % _P
    return (x3, (lam * (P1[0] - x3) - P1[1]) % _P)


def _point_mul(P, n):
    R = None
    for i in range(256):
        if (n >> i) & 1:
            R = _point_add(R, P)
        P = _point_add(P, P)
    return R


def _tagged_hash(tag: str, msg: bytes) -> bytes:
    tag_hash = hashlib.sha256(tag.encode()).digest()
    return hashlib.sha256(tag_hash + tag_hash + msg).digest()


def schnorr_sign(msg: bytes, seckey: bytes) -> bytes:
    """
    Compute a BIP-340 Schnorr signature.

    Args:
        msg:    32-byte message (e.g. the SHA-256 event ID hash).
        seckey: 32-byte private key scalar (big-endian).

    Returns:
        64-byte signature (r_x || s).
    """
    if len(msg) != 32:
        raise ValueError(f"msg must be 32 bytes, got {len(msg)}")
    if len(seckey) != 32:
        raise ValueError(f"seckey must be 32 bytes, got {len(seckey)}")

    x = int.from_bytes(seckey, "big")
    if x == 0 or x >= _N:
        raise ValueError("private key scalar out of valid range")

    Px, Py = _point_mul(_G, x)
    # BIP-340: negate key if y-coordinate is odd
    if Py % 2 != 0:
        x = _N - x

    # Deterministic nonce derivation (BIP-340 §Signing with aux_rand = 0^32)
    aux_rand = bytes(32)
    t = x ^ int.from_bytes(_tagged_hash("BIP0340/aux", aux_rand), "big")
    rand = _tagged_hash(
        "BIP0340/nonce",
        t.to_bytes(32, "big") + Px.to_bytes(32, "big") + msg,
    )

    k = int.from_bytes(rand, "big") % _N
    if k == 0:
        raise ValueError("nonce k is zero — regenerate key")

    Rx, Ry = _point_mul(_G, k)
    if Ry % 2 != 0:
        k = _N - k

    e = (
        int.from_bytes(
            _tagged_hash(
                "BIP0340/challenge",
                Rx.to_bytes(32, "big") + Px.to_bytes(32, "big") + msg,
            ),
            "big",
        )
        % _N
    )

    s = (k + e * x) % _N
    return Rx.to_bytes(32, "big") + s.to_bytes(32, "big")
