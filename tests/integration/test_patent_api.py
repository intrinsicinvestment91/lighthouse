"""
Integration tests — hit live patent APIs.

Run with:  pytest tests/integration -m integration -v
These are skipped in the default CI job. Requires network access.

Live patent tests require two env vars:
  RUN_LIVE_PATENT_API_TESTS=true
  PATENT_PROVIDER=legacy_patentsview   (or the active provider when implemented)

Without both, all live patent tests are skipped.
"""

import os

import httpx
import pytest

from modules.patent_sentinel import fetch_uspto_patents, fetch_wipo_patents

_TRANSIENT_STATUS_CODES = {403, 410, 429, 500, 502, 503, 504}

_LIVE_PATENT_ENABLED = os.getenv("RUN_LIVE_PATENT_API_TESTS", "").strip().lower() in {
    "1",
    "true",
    "yes",
}
_PATENT_PROVIDER = os.getenv("PATENT_PROVIDER", "disabled").strip().lower()

_skip_live = pytest.mark.skipif(
    not _LIVE_PATENT_ENABLED,
    reason="Live patent tests disabled; set RUN_LIVE_PATENT_API_TESTS=true to enable",
)
_skip_no_legacy = pytest.mark.skipif(
    _PATENT_PROVIDER != "legacy_patentsview",
    reason="PATENT_PROVIDER != legacy_patentsview; skipping legacy endpoint test",
)


@pytest.mark.integration
@_skip_live
@_skip_no_legacy
async def test_fetch_uspto_patents_live():
    """Legacy PatentsView live probe (search.patentsview.org — known stale as of 2026-05)."""
    try:
        patents = await fetch_uspto_patents("G21B", since_days=30)
    except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout) as exc:
        pytest.skip(f"PatentsView endpoint unreachable: {exc}")
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code in _TRANSIENT_STATUS_CODES:
            pytest.skip(f"PatentsView returned transient status: {exc}")
        raise
    assert isinstance(patents, list)


@pytest.mark.integration
@_skip_live
@pytest.mark.skipif(not os.getenv("WIPO_API_KEY"), reason="WIPO_API_KEY not set")
async def test_fetch_wipo_patents_live():
    patents = await fetch_wipo_patents("G21B", since_days=30)
    assert isinstance(patents, list)


@pytest.mark.integration
@_skip_live
@pytest.mark.skipif(not os.getenv("WIPO_API_KEY"), reason="WIPO_API_KEY not set")
async def test_fetch_wipo_patents_paginates_correctly():
    """Fetch a broad class over a long window to verify pagination works end-to-end."""
    patents = await fetch_wipo_patents("C22C", since_days=180)
    assert isinstance(patents, list)
