"""
Module 6 — Proxy Rotator.

Rotates outbound HTTP and Playwright connections through a pool of residential
or datacenter proxies to avoid fingerprinting by monitored agencies.

Proxy list source (in priority order):
  1. PROXY_LIST env var — comma-separated URLs
  2. proxies.txt in the project root — one URL per line
Format: http://user:pass@host:port  or  socks5://host:port
"""

import itertools
import logging
import random
from pathlib import Path

import httpx

from config import proxy_cfg

logger = logging.getLogger(__name__)


class ProxyRotator:
    def __init__(self, proxies: list[str], strategy: str = "round_robin") -> None:
        self._proxies = proxies
        self._strategy = strategy
        self._cycle = itertools.cycle(proxies) if proxies else iter([])
        if proxies:
            logger.info(
                "[Proxy] Rotator initialized: %d proxies, strategy=%s",
                len(proxies),
                strategy,
            )

    @property
    def enabled(self) -> bool:
        return bool(self._proxies)

    def next(self) -> str | None:
        """Return the next proxy URL, or None if rotation is disabled."""
        if not self._proxies:
            return None
        if self._strategy == "random":
            return random.choice(self._proxies)
        return next(self._cycle)


def load_proxy_rotator() -> ProxyRotator:
    """Build a ProxyRotator from config (env var → proxies.txt → disabled)."""
    proxies = list(proxy_cfg.proxies)

    if not proxies:
        txt = Path("proxies.txt")
        if txt.exists():
            proxies = [ln.strip() for ln in txt.read_text().splitlines() if ln.strip()]

    return ProxyRotator(proxies, proxy_cfg.strategy)


def make_httpx_client(rotator: "ProxyRotator | None", **kwargs) -> httpx.AsyncClient:
    """Return AsyncClient with next proxy, or plain client if rotator is disabled."""
    proxy = rotator.next() if rotator else None
    if proxy:
        kwargs.setdefault("proxy", proxy)
    return httpx.AsyncClient(**kwargs)


def playwright_proxy_kwargs(rotator: "ProxyRotator | None") -> dict:
    """Return kwargs for browser.new_context(proxy=...), or empty dict if disabled."""
    proxy = rotator.next() if rotator else None
    if not proxy:
        return {}
    return {"proxy": {"server": proxy}}
