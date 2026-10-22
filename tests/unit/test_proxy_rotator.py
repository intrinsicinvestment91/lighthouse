"""Unit tests for modules/proxy_rotator.py"""

import httpx

from modules.proxy_rotator import ProxyRotator, make_httpx_client, playwright_proxy_kwargs

_PROXIES = [
    "http://user:pass@proxy1.example.com:8080",
    "http://user:pass@proxy2.example.com:8080",
    "http://user:pass@proxy3.example.com:8080",
]


def test_round_robin_cycles():
    rotator = ProxyRotator(_PROXIES, strategy="round_robin")
    seen = [rotator.next() for _ in range(6)]
    assert seen[:3] == _PROXIES
    assert seen[3:] == _PROXIES


def test_random_returns_from_pool():
    rotator = ProxyRotator(_PROXIES, strategy="random")
    for _ in range(20):
        assert rotator.next() in _PROXIES


def test_empty_proxy_list_returns_none():
    rotator = ProxyRotator([])
    assert rotator.next() is None
    assert not rotator.enabled


def test_enabled_true_when_proxies_provided():
    rotator = ProxyRotator(_PROXIES)
    assert rotator.enabled is True


def test_make_httpx_client_no_proxy():
    rotator = ProxyRotator([])
    client = make_httpx_client(rotator)
    assert isinstance(client, httpx.AsyncClient)


def test_make_httpx_client_with_proxy():
    rotator = ProxyRotator([_PROXIES[0]])
    client = make_httpx_client(rotator)
    assert isinstance(client, httpx.AsyncClient)


def test_playwright_proxy_kwargs_empty():
    rotator = ProxyRotator([])
    assert playwright_proxy_kwargs(rotator) == {}


def test_playwright_proxy_kwargs_with_proxy():
    rotator = ProxyRotator([_PROXIES[0]])
    result = playwright_proxy_kwargs(rotator)
    assert result == {"proxy": {"server": _PROXIES[0]}}


def test_make_httpx_client_passes_kwargs():
    rotator = ProxyRotator([])
    client = make_httpx_client(rotator, timeout=42.0)
    assert client.timeout.read == 42.0
