"""
Startup configuration validator for Project Lighthouse.

Runs before any module and emits warnings/errors for misconfigured
environment variables and unreachable external services.
"""

import logging

import websockets

import config

logger = logging.getLogger(__name__)

_SECP256K1_ORDER = 0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141


class _ConfigProxy:
    """Delegate attribute access to the current runtime config object."""

    def __init__(self, getter):
        self._getter = getter

    def __getattr__(self, name: str):
        return getattr(self._getter(), name)

    def __setattr__(self, name: str, value):
        if name == "_getter":
            super().__setattr__(name, value)
            return
        setattr(self._getter(), name, value)


# Backward-compatible aliases for existing test monkeypatch targets.
nostr_cfg = _ConfigProxy(config.get_nostr_cfg)
patent_cfg = _ConfigProxy(config.get_patent_cfg)


async def validate_config() -> list[str]:
    """Return a list of warning/error strings. Fatal issues are logged at ERROR."""
    issues: list[str] = []

    # --- NOSTR_PRIVATE_KEY_HEX ---
    key_hex = config.get_nostr_cfg().private_key_hex
    if key_hex:
        if len(key_hex) != 64:
            msg = f"NOSTR_PRIVATE_KEY_HEX is {len(key_hex)} chars; expected 64."
            logger.error(msg)
            issues.append(f"FATAL: {msg}")
        else:
            try:
                scalar = int(key_hex, 16)
                if scalar == 0 or scalar >= _SECP256K1_ORDER:
                    msg = "NOSTR_PRIVATE_KEY_HEX scalar is out of valid secp256k1 range."
                    logger.error(msg)
                    issues.append(f"FATAL: {msg}")
            except ValueError:
                msg = "NOSTR_PRIVATE_KEY_HEX contains non-hex characters."
                logger.error(msg)
                issues.append(f"FATAL: {msg}")
    else:
        msg = "NOSTR_PRIVATE_KEY_HEX not set — Module 3 (Nostr) will be skipped."
        logger.warning(msg)
        issues.append(f"WARN: {msg}")

    # --- WIPO_API_KEY ---
    if not config.get_patent_cfg().wipo_api_key:
        msg = "WIPO_API_KEY not set — WIPO PATENTSCOPE queries will be skipped."
        logger.warning(msg)
        issues.append(f"WARN: {msg}")

    # --- IPFS reachability ---
    try:
        import ipfshttpclient

        with ipfshttpclient.connect(config.archive_cfg.ipfs_api_url) as client:
            client.version()
    except Exception as exc:
        msg = f"IPFS daemon unreachable at {config.archive_cfg.ipfs_api_url}: {exc}"
        logger.warning("%s — pages will be saved locally but not pinned.", msg)
        issues.append(f"WARN: {msg}")

    # --- Nostr relay reachability ---
    if key_hex:
        for relay in config.get_nostr_cfg().relays:
            try:
                async with websockets.connect(relay, open_timeout=3):
                    pass
            except Exception as exc:
                msg = f"Nostr relay unreachable: {relay} ({exc})"
                logger.warning(msg)
                issues.append(f"WARN: {msg}")

    return issues
