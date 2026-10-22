"""Unit tests for modules/validator.py"""

from unittest.mock import AsyncMock, MagicMock, patch

import modules.validator as validator_module
from modules.validator import validate_config


def _ipfs_ok_mock():
    client = MagicMock()
    client.__enter__ = MagicMock(return_value=client)
    client.__exit__ = MagicMock(return_value=False)
    client.version.return_value = {"Version": "0.21.0"}
    return client


async def test_no_nostr_key_warns(monkeypatch):
    monkeypatch.setattr(validator_module.nostr_cfg, "private_key_hex", "")
    monkeypatch.setattr(validator_module.patent_cfg, "wipo_api_key", "testkey")
    with patch("ipfshttpclient.connect", return_value=_ipfs_ok_mock()):
        issues = await validate_config()
    assert any("WARN" in i and "NOSTR_PRIVATE_KEY_HEX" in i for i in issues)


async def test_key_wrong_length_is_fatal(monkeypatch):
    monkeypatch.setattr(validator_module.nostr_cfg, "private_key_hex", "deadbeef")  # too short
    monkeypatch.setattr(validator_module.patent_cfg, "wipo_api_key", "testkey")
    with patch("ipfshttpclient.connect", return_value=_ipfs_ok_mock()):
        issues = await validate_config()
    assert any("FATAL" in i for i in issues)


async def test_non_hex_key_is_fatal(monkeypatch):
    monkeypatch.setattr(validator_module.nostr_cfg, "private_key_hex", "z" * 64)
    monkeypatch.setattr(validator_module.patent_cfg, "wipo_api_key", "testkey")
    with patch("ipfshttpclient.connect", return_value=_ipfs_ok_mock()):
        issues = await validate_config()
    assert any("FATAL" in i for i in issues)


async def test_no_wipo_key_warns(monkeypatch):
    monkeypatch.setattr(validator_module.nostr_cfg, "private_key_hex", "")
    monkeypatch.setattr(validator_module.patent_cfg, "wipo_api_key", "")
    with patch("ipfshttpclient.connect", return_value=_ipfs_ok_mock()):
        issues = await validate_config()
    assert any("WARN" in i and "WIPO" in i for i in issues)


async def test_ipfs_unreachable_warns(monkeypatch):
    monkeypatch.setattr(validator_module.nostr_cfg, "private_key_hex", "")
    monkeypatch.setattr(validator_module.patent_cfg, "wipo_api_key", "testkey")
    with patch("ipfshttpclient.connect", side_effect=Exception("daemon not running")):
        issues = await validate_config()
    assert any("WARN" in i and "IPFS" in i for i in issues)


async def test_relay_unreachable_warns(monkeypatch, nostr_test_key_hex):
    monkeypatch.setattr(validator_module.nostr_cfg, "private_key_hex", nostr_test_key_hex)
    monkeypatch.setattr(validator_module.nostr_cfg, "relays", ["wss://test.relay"])
    monkeypatch.setattr(validator_module.patent_cfg, "wipo_api_key", "testkey")
    with (
        patch("ipfshttpclient.connect", return_value=_ipfs_ok_mock()),
        patch("modules.validator.websockets.connect", side_effect=Exception("refused")),
    ):
        issues = await validate_config()
    assert any("WARN" in i for i in issues)


async def test_valid_config_no_fatals(monkeypatch, nostr_test_key_hex):
    monkeypatch.setattr(validator_module.nostr_cfg, "private_key_hex", nostr_test_key_hex)
    monkeypatch.setattr(validator_module.nostr_cfg, "relays", ["wss://test.relay"])
    monkeypatch.setattr(validator_module.patent_cfg, "wipo_api_key", "testkey")
    with (
        patch("ipfshttpclient.connect", return_value=_ipfs_ok_mock()),
        patch("modules.validator.websockets.connect", return_value=AsyncMock()),
    ):
        issues = await validate_config()
    assert not any("FATAL" in i for i in issues)
