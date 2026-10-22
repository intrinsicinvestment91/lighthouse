"""
Module 1 — Intellectual Property Sentinel.

Primary path : PatentsView REST API (USPTO) + WIPO PATENTSCOPE REST API.
Playwright fallback: used for flagged patents whose abstract is empty,
fetching the full HTML page from Google Patents and saving it locally.
"""

import asyncio
import json
import logging
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
from playwright.async_api import Page, async_playwright

import config
from modules import db as _db
from modules.alerts import ALERT_SCHEMA_VERSION
from modules.data_paths import data_path
from modules.provenance import ProvenanceRecord
from modules.proxy_rotator import ProxyRotator, make_httpx_client, playwright_proxy_kwargs
from modules.retry import http_retry
from modules.scoring import AlertScore
from modules.state_db import init_db, is_seen, mark_seen

logger = logging.getLogger(__name__)

_ALLOWED_PROVIDERS: frozenset[str] = frozenset({"disabled", "legacy_patentsview", "odp"})


def _validate_patent_provider(provider: str) -> str:
    """Normalize and validate a patent provider string. Raises ValueError on unknown values."""
    p = provider.strip().lower()
    if p not in _ALLOWED_PROVIDERS:
        raise ValueError(
            f"Unknown patent provider {provider!r}; allowed: {sorted(_ALLOWED_PROVIDERS)}"
        )
    return p


async def check_patent_provider_health() -> dict:
    """Return a diagnostic dict for the configured patent provider. Never raises."""
    cfg = config.get_patent_cfg()
    try:
        provider = _validate_patent_provider(cfg.patent_provider)
    except ValueError as exc:
        return {
            "provider": cfg.patent_provider,
            "enabled": False,
            "base_url": "",
            "ok": False,
            "status": "unknown_error",
            "detail": str(exc),
        }

    if provider == "disabled":
        return {
            "provider": "disabled",
            "enabled": False,
            "base_url": "",
            "ok": False,
            "status": "disabled",
            "detail": "Patent provider is disabled",
        }

    if provider == "odp":
        return {
            "provider": "odp",
            "enabled": True,
            "base_url": cfg.uspto_odp_api,
            "ok": False,
            "status": "unsupported",
            "detail": "USPTO ODP patent search adapter is not implemented yet",
        }

    # legacy_patentsview — probe base URL
    base_url = cfg.patentsview_api
    try:
        async with httpx.AsyncClient(timeout=cfg.http_timeout) as client:
            r = await client.get(base_url)
        return {
            "provider": "legacy_patentsview",
            "enabled": True,
            "base_url": base_url,
            "ok": True,
            "status": "ok",
            "detail": f"HTTP {r.status_code}",
        }
    except httpx.ConnectError as exc:
        return {
            "provider": "legacy_patentsview",
            "enabled": True,
            "base_url": base_url,
            "ok": False,
            "status": "dns_error",
            "detail": str(exc),
        }
    except httpx.HTTPStatusError as exc:
        return {
            "provider": "legacy_patentsview",
            "enabled": True,
            "base_url": base_url,
            "ok": False,
            "status": "http_error",
            "detail": str(exc),
        }
    except Exception as exc:
        return {
            "provider": "legacy_patentsview",
            "enabled": True,
            "base_url": base_url,
            "ok": False,
            "status": "unknown_error",
            "detail": str(exc),
        }


class _PatentCfgProxy:
    """Delegate attribute access to the current runtime patent config."""

    def __getattr__(self, name: str):
        return getattr(config.get_patent_cfg(), name)

    def __setattr__(self, name: str, value):
        setattr(config.get_patent_cfg(), name, value)


# Backward-compatible alias for tests patching module-level config directly.
patent_cfg = _PatentCfgProxy()


def _get_out_dir() -> Path:
    out_dir = Path(config.get_patent_cfg().output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir


# ---------------------------------------------------------------------------
# USPTO — PatentsView API (provider-dispatched)
# ---------------------------------------------------------------------------


async def fetch_uspto_patents(
    ipc_class: str,
    since_days: int = 7,
    client: httpx.AsyncClient | None = None,
) -> list[dict[str, Any]]:
    """Dispatch to the configured patent provider and return grants for an IPC class."""
    provider = _validate_patent_provider(config.get_patent_cfg().patent_provider)
    if provider == "disabled":
        return []
    if provider == "legacy_patentsview":
        return await _fetch_legacy_patentsview_patents(ipc_class, since_days, client)
    if provider == "odp":
        return await _fetch_odp_patents(ipc_class, since_days, client)
    return []


@http_retry
async def _fetch_legacy_patentsview_patents(
    ipc_class: str,
    since_days: int = 7,
    client: httpx.AsyncClient | None = None,
) -> list[dict[str, Any]]:
    """Query legacy PatentsView Search API (search.patentsview.org) for recent grants."""
    since = (datetime.now(UTC) - timedelta(days=since_days)).strftime("%Y-%m-%d")
    payload = {
        "q": {
            "_and": [
                {"_gte": {"patent_date": since}},
                {"_contains": {"ipc_section_id": ipc_class[0]}},
            ]
        },
        "f": [
            "patent_id",
            "patent_title",
            "patent_date",
            "patent_abstract",
            "assignee_organization",
            "assignee_type",
            "ipc_section_id",
            "ipc_class_id",
            "ipc_subclass_id",
        ],
        "o": {"per_page": 100},
    }
    url = f"{config.get_patent_cfg().patentsview_api}/patents/query"
    owns_client = client is None
    if owns_client:
        client = httpx.AsyncClient(timeout=config.get_patent_cfg().http_timeout)
    try:
        resp = await client.post(url, json=payload)
        resp.raise_for_status()
        data = resp.json()
        patents = data.get("patents") or []
        logger.info("[USPTO] class=%s  since=%s  found=%d", ipc_class, since, len(patents))
        return patents
    finally:
        if owns_client:
            await client.aclose()


async def _fetch_odp_patents(
    ipc_class: str,
    since_days: int = 7,
    client: httpx.AsyncClient | None = None,
) -> list[dict[str, Any]]:
    raise NotImplementedError("USPTO ODP patent search adapter is not implemented yet")


# ---------------------------------------------------------------------------
# WIPO — PATENTSCOPE REST API  (requires free API key)
# ---------------------------------------------------------------------------


@http_retry
async def fetch_wipo_patents(
    ipc_class: str,
    since_days: int = 7,
    client: httpx.AsyncClient | None = None,
) -> list[dict[str, Any]]:
    """Query WIPO PATENTSCOPE for recent PCT applications, paginating fully."""
    if not config.get_patent_cfg().wipo_api_key:
        logger.warning("[WIPO] WIPO_API_KEY not set — skipping WIPO query")
        return []

    since = (datetime.now(UTC) - timedelta(days=since_days)).strftime("%Y%m%d")
    headers = {"Authorization": f"Bearer {config.get_patent_cfg().wipo_api_key}"}
    url = f"{config.get_patent_cfg().wipo_api}/search"
    page_size = config.get_patent_cfg().wipo_page_size

    owns_client = client is None
    if owns_client:
        client = httpx.AsyncClient(timeout=config.get_patent_cfg().http_timeout)

    all_docs: list[dict[str, Any]] = []
    offset = 0
    try:
        while True:
            params = {
                "q": f"IC:{ipc_class} AND PD:[{since} TO 99991231]",
                "fl": "PN,TI,PA,IPC,PD",
                "rows": page_size,
                "start": offset,
            }
            resp = await client.get(url, params=params, headers=headers)
            resp.raise_for_status()
            body = resp.json()
            docs = body.get("docs", [])
            all_docs.extend(docs)

            num_found = body.get("numFound")
            if num_found is not None and offset + page_size >= num_found:
                break
            if len(docs) < page_size:
                break
            offset += page_size

        logger.info("[WIPO] class=%s  since=%s  found=%d", ipc_class, since, len(all_docs))
        return all_docs
    finally:
        if owns_client:
            await client.aclose()


# ---------------------------------------------------------------------------
# Playwright fallback — full-text / drawing pages
# ---------------------------------------------------------------------------


async def playwright_fetch_patent_page(patent_id: str, page: Page) -> str | None:
    """
    Fetch the full HTML of a USPTO patent page via Google Patents using a
    shared Playwright page object. Returns raw HTML or None on failure.
    """
    url = f"https://patents.google.com/patent/US{patent_id}/en"
    try:
        await page.goto(
            url,
            wait_until="domcontentloaded",
            timeout=config.get_patent_cfg().playwright_timeout_ms,
        )
        await page.wait_for_timeout(config.get_patent_cfg().playwright_settle_ms)
        return await page.content()
    except Exception as exc:
        logger.warning("[Playwright] Failed to fetch patent %s: %s", patent_id, exc)
        return None


# ---------------------------------------------------------------------------
# Filtering helpers
# ---------------------------------------------------------------------------


def _filter_assignees_for_targets(
    assignees: list[str],
    target_entities: list[str] | None = None,
    target_keywords: list[str] | None = None,
) -> list[str]:
    """Return assignees whose label matches target entities or keywords."""
    has_entity_filter = bool(target_entities)
    has_keyword_filter = bool(target_keywords)
    if not has_entity_filter and not has_keyword_filter:
        return assignees

    targets: list[str] = []
    if target_entities:
        targets.extend(target_entities)
    if target_keywords:
        targets.extend(target_keywords)

    filtered: list[str] = []
    for assignee in assignees:
        assignee_lower = assignee.lower()
        if any(
            target.lower() in assignee_lower or assignee_lower in target.lower()
            for target in targets
        ):
            filtered.append(assignee)
    return filtered


def _patent_record_matches_target_keywords(
    patent: dict[str, Any] | None,
    target_keywords: list[str] | None = None,
) -> bool:
    """Return True when patent text matches any target keyword, or when no keywords."""
    if not target_keywords:
        return True
    if not patent:
        return False

    parts: list[str] = []
    for key in (
        "title",
        "patent_title",
        "TI",
        "abstract",
        "patent_abstract",
        "claims",
        "assignee",
        "assignee_organization",
        "PA",
        "applicant",
        "ipc",
        "ipc_subclass_id",
        "IPC",
        "ipc_class_id",
    ):
        value = patent.get(key)
        if isinstance(value, str) and value:
            parts.append(value)

    haystack = " ".join(parts).lower()
    return any(keyword.lower() in haystack for keyword in target_keywords)


def matches_assignee_filter(
    record: dict[str, Any],
    assignees: list[str] | None = None,
) -> bool:
    """Return True if the patent's assignee appears in the watchlist."""
    watchlist = (
        assignees if assignees is not None else config.get_patent_cfg().assignees_of_interest
    )
    raw = record.get("assignee_organization") or record.get("PA") or ""
    assignee = str(raw).lower()
    return any(kw.lower() in assignee for kw in watchlist)


def normalize_record(raw: dict[str, Any], source: str) -> dict[str, Any]:
    """Flatten USPTO / WIPO records into a common schema."""
    if source == "USPTO":
        return {
            "source": "USPTO",
            "id": raw.get("patent_id"),
            "title": raw.get("patent_title"),
            "date": raw.get("patent_date"),
            "assignee": raw.get("assignee_organization"),
            "ipc": raw.get("ipc_subclass_id"),
            "abstract": (raw.get("patent_abstract") or "")[:500],
        }
    else:  # WIPO
        return {
            "source": "WIPO",
            "id": raw.get("PN"),
            "title": raw.get("TI"),
            "date": raw.get("PD"),
            "assignee": raw.get("PA"),
            "ipc": raw.get("IPC"),
            "abstract": "",
        }


# ---------------------------------------------------------------------------
# Scoring + provenance helpers
# ---------------------------------------------------------------------------


def _attach_alert_score(patent: dict) -> None:
    source = patent.get("source", "")
    reliability = 0.9 if "uspto" in source.lower() else 0.8
    recency = 0.5
    try:
        filed = datetime.strptime(patent["date"], "%Y-%m-%d").replace(tzinfo=UTC)
        days_old = (datetime.now(UTC) - filed).days
        recency = max(0.0, 1.0 - days_old / 90)
    except (ValueError, KeyError, TypeError):
        pass
    score = AlertScore(
        source_reliability=reliability,
        keyword_strength=1.0,
        recency=recency,
        uniqueness=0.5,
        watchlist_match=1.0,
    )
    patent["score"] = round(score.total(), 3)
    patent["score_label"] = score.label()
    patent["alert_type"] = "flagged_patent"
    patent["schema_version"] = ALERT_SCHEMA_VERSION


def _attach_alert_provenance(patent: dict) -> None:
    provenance = ProvenanceRecord(
        source_type="patent_api",
        source_url=str(patent.get("id", "")),
        retrieved_at=datetime.now(UTC).isoformat(),
        metadata={"source": patent.get("source", ""), "ipc": patent.get("ipc", "")},
    )
    patent["provenance"] = provenance.to_dict()


# ---------------------------------------------------------------------------
# Main runner
# ---------------------------------------------------------------------------


async def run_patent_sentinel(
    since_days: int = 7,
    dry_run: bool = False,
    rotator: ProxyRotator | None = None,
    target_entities: list[str] | None = None,
    target_keywords: list[str] | None = None,
) -> list[dict[str, Any]]:
    try:
        provider = _validate_patent_provider(config.get_patent_cfg().patent_provider)
    except ValueError as exc:
        logger.error("[Patent] Invalid patent provider configuration: %s", exc)
        return []

    if provider == "disabled":
        logger.info("[Patent] Patent provider disabled; skipping Patent Sentinel")
        return []

    if provider == "odp":
        logger.warning("[Patent] Patent provider odp is configured but adapter is not implemented")
        return []

    all_assignees = list(config.get_patent_cfg().assignees_of_interest)
    if target_entities or target_keywords:
        effective_assignees = _filter_assignees_for_targets(
            all_assignees, target_entities, target_keywords
        )
        logger.info(
            "[Patent] Target filter active: %d of %d assignees selected",
            len(effective_assignees),
            len(all_assignees),
        )
        if not effective_assignees:
            logger.info("[Patent] Target filter excluded all assignees — skipping")
            return []
    else:
        effective_assignees = all_assignees

    conn = init_db(data_path("state.db"))
    results: list[dict[str, Any]] = []

    async with make_httpx_client(rotator, timeout=config.get_patent_cfg().http_timeout) as client:
        for cls in config.get_patent_cfg().classes:
            uspto_raw = await fetch_uspto_patents(cls, since_days, client)
            wipo_raw = await fetch_wipo_patents(cls, since_days, client)

            for rec in uspto_raw:
                normalized = normalize_record(rec, "USPTO")
                patent_id = normalized.get("id") or ""
                if not patent_id or is_seen(conn, f"patent:{patent_id}"):
                    continue
                if target_keywords and not _patent_record_matches_target_keywords(
                    normalized, target_keywords
                ):
                    continue
                if matches_assignee_filter(rec, assignees=effective_assignees):
                    normalized["_flagged"] = True
                results.append(normalized)
                if not dry_run:
                    mark_seen(conn, f"patent:{patent_id}")

            for rec in wipo_raw:
                normalized = normalize_record(rec, "WIPO")
                patent_id = normalized.get("id") or ""
                if not patent_id or is_seen(conn, f"patent:{patent_id}"):
                    continue
                if target_keywords and not _patent_record_matches_target_keywords(
                    normalized, target_keywords
                ):
                    continue
                if matches_assignee_filter(rec, assignees=effective_assignees):
                    normalized["_flagged"] = True
                results.append(normalized)
                if not dry_run:
                    mark_seen(conn, f"patent:{patent_id}")

            await asyncio.sleep(config.get_patent_cfg().inter_class_delay)

    # Playwright enrichment for flagged patents with empty abstracts
    if config.get_patent_cfg().use_playwright_fallback and not dry_run:
        to_enrich = [p for p in results if p.get("_flagged") and not p.get("abstract")]
        if to_enrich:
            try:
                async with async_playwright() as pw:
                    browser = await pw.chromium.launch(headless=True)
                    ctx = await browser.new_context(**playwright_proxy_kwargs(rotator))
                    enrichment_page: Page = await ctx.new_page()
                    for patent in to_enrich:
                        pid = patent.get("id") or ""
                        if not pid:
                            continue
                        html = await playwright_fetch_patent_page(pid, enrichment_page)
                        if html:
                            out_dir = _get_out_dir()
                            html_path = out_dir / f"{pid}.html"
                            html_path.write_text(html, encoding="utf-8")
                            logger.info("[Playwright] Saved full-text for %s → %s", pid, html_path)
                    await browser.close()
            except Exception as exc:
                logger.error("[Playwright] Enrichment batch failed: %s", exc, exc_info=True)

    # Score and persist flagged patents
    flagged = [p for p in results if p.get("_flagged")]
    for p in flagged:
        _attach_alert_score(p)
        _attach_alert_provenance(p)
    if not dry_run:
        _db.init_alerts_table()
        for p in flagged:
            _db.insert_alert("patent_sentinel", p)

    # Persist
    out_dir = _get_out_dir()
    ts = int(time.time())
    out_file = out_dir / f"patents_{ts}.json"
    out_file.write_text(json.dumps(results, indent=2))
    logger.info("[Sentinel] Wrote %d records to %s", len(results), out_file)
    return results


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run_patent_sentinel(since_days=7))
