"""Unit tests for modules/_schnorr.py — pure-Python BIP-340 Schnorr implementation."""

import pytest

from modules._schnorr import schnorr_sign

_VALID_KEY = bytes.fromhex("0101010101010101010101010101010101010101010101010101010101010101")
_VALID_MSG = bytes(32)  # 32 zero bytes


def test_returns_64_bytes():
    sig = schnorr_sign(_VALID_MSG, _VALID_KEY)
    assert isinstance(sig, bytes) and len(sig) == 64


def test_deterministic():
    assert schnorr_sign(_VALID_MSG, _VALID_KEY) == schnorr_sign(_VALID_MSG, _VALID_KEY)


def test_msg_wrong_length_raises():
    with pytest.raises(ValueError, match="msg must be 32 bytes"):
        schnorr_sign(bytes(16), _VALID_KEY)


def test_seckey_wrong_length_raises():
    with pytest.raises(ValueError, match="seckey must be 32 bytes"):
        schnorr_sign(_VALID_MSG, bytes(16))


def test_zero_scalar_raises():
    with pytest.raises(ValueError, match="out of valid range"):
        schnorr_sign(_VALID_MSG, bytes(32))


def test_different_keys_produce_different_sigs():
    key2 = bytes.fromhex("0202020202020202020202020202020202020202020202020202020202020202")
    assert schnorr_sign(_VALID_MSG, _VALID_KEY) != schnorr_sign(_VALID_MSG, key2)


def test_different_msgs_produce_different_sigs():
    assert schnorr_sign(bytes(32), _VALID_KEY) != schnorr_sign(bytes([1] * 32), _VALID_KEY)
