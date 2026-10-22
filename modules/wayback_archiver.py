"""
Module 2 — Institutional Wayback Archiver.

Crawls public newsroom / press-release pages from DoD, NASA, and DoE.
Filters articles by keyword, archives the full page HTML to IPFS,
and writes a local manifest of (url, ipfs_cid, timestamp, matched_keywords).

Requires:
    pip install playwright ipfshttpclient
    python -m playwright install chromium
    ipfs daemon  (or set IPFS_API_URL to a remote pinning endpoint)
"""

import asyncio
import json
import logging
import time
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlparse

import httpx
import ipfshttpclient
from playwright.async_api import Browser, Page, async_playwright

from config import archive_cfg
from modules.data_paths import data_path
from modules.proxy_rotator import ProxyRotator, make_httpx_client, playwright_proxy_kwargs
from modules.retry import http_retry
from modules.state_db import init_db, is_seen, mark_seen

logger = logging.getLogger(__name__)
out_dir = Path(archive_cfg.output_dir)
out_dir.mkdir(parents=True, exist_ok=True)

_ATOM_NS = "http://www.w3.org/2005/Atom"

# Exact page titles that indicate an access-control error, not real content.
# Checked case-insensitively against the stripped page title before archiving.
_DENY_TITLES: frozenset[str] = frozenset(
    {
        "access denied",
        "403 forbidden",
        "404 not found",
        "page not found",
        "error",
        "unauthorized",
    }
)

_ARTICLE_PATH_SEGMENTS = (
    "/news/",
    "/releases/",
    "/press-release/",
    "/articles/",
    "/story/",
    "/newsroom",
)


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class ArchivedPage:
    agency: str
    url: str
    title: str
    snapshot_ts: str  # ISO-8601 UTC
    ipfs_cid: str | None  # None if IPFS was unreachable
    matched_keywords: list[str]
    local_path: str  # relative path to the saved HTML


# ---------------------------------------------------------------------------
# IPFS helper
# ---------------------------------------------------------------------------


def pin_to_ipfs(html: str, filename: str) -> str | None:
    """Write HTML to IPFS and return the CIDv1 string, or None if unavailable."""
    local_file = out_dir / filename
    local_file.write_text(html, encoding="utf-8")

    service = archive_cfg.ipfs_pinning_service
    if service == "pinata":
        if not archive_cfg.pinata_jwt:
            logger.warning(
                "[IPFS] IPFS_PINNING_SERVICE=pinata but PINATA_JWT is missing — saved locally only."
            )
            return None
        logger.warning(
            "[IPFS] Pinata backend selected but not implemented yet; set "
            "IPFS_PINNING_SERVICE=local for CID pinning via Kubo daemon."
        )
        return None

    if service == "web3storage":
        if not archive_cfg.web3storage_token:
            logger.warning(
                "[IPFS] IPFS_PINNING_SERVICE=web3storage but WEB3STORAGE_TOKEN is missing "
                "— saved locally only."
            )
            return None
        logger.warning(
            "[IPFS] web3.storage backend selected but not implemented yet; set "
            "IPFS_PINNING_SERVICE=local for CID pinning via Kubo daemon."
        )
        return None

    if service != "local":
        logger.warning(
            "[IPFS] Unknown IPFS_PINNING_SERVICE=%r; expected local|pinata|web3storage. "
            "Falling back to local daemon.",
            service,
        )

    try:
        with ipfshttpclient.connect(archive_cfg.ipfs_api_url) as client:
            result = client.add(str(local_file), pin=True)
            cid = result["Hash"]
        logger.info("[IPFS] Pinned %s → %s", filename, cid)
        return cid
    except Exception as exc:
        logger.warning("[IPFS] Pinning failed for %s: %s — saved locally only.", filename, exc)
        return None


# ---------------------------------------------------------------------------
# Keyword filter
# ---------------------------------------------------------------------------


def extract_keywords(text: str, keywords: list[str]) -> list[str]:
    lower = text.lower()
    return [kw for kw in keywords if kw.lower() in lower]


def _text_matches_targets(
    text: str | None,
    target_entities: list[str] | None = None,
    target_keywords: list[str] | None = None,
) -> bool:
    """Return True when text matches any target entity or keyword, or when no filters."""
    if not target_entities and not target_keywords:
        return True
    if not text:
        return False

    lower = text.lower()
    targets: list[str] = []
    if target_entities:
        targets.extend(target_entities)
    if target_keywords:
        targets.extend(target_keywords)
    return any(target.lower() in lower for target in targets)


def _source_search_text(source: dict) -> str:
    parts: list[str] = []
    for key in ("name", "title", "agency", "url", "description"):
        value = source.get(key)
        if isinstance(value, str) and value:
            parts.append(value)
    keywords = source.get("keywords")
    if isinstance(keywords, list):
        parts.extend(str(kw) for kw in keywords if kw)
    return " ".join(parts).lower()


def _filter_sources_for_targets(
    sources: list,
    target_entities: list[str] | None = None,
    target_keywords: list[str] | None = None,
) -> list:
    """Return sources whose metadata matches target entities or keywords."""
    if not target_entities and not target_keywords:
        return sources

    filtered: list = []
    for source in sources:
        haystack = _source_search_text(source)
        targets: list[str] = []
        if target_entities:
            targets.extend(target_entities)
        if target_keywords:
            targets.extend(target_keywords)
        if any(target.lower() in haystack for target in targets):
            filtered.append(source)
    return filtered


# ---------------------------------------------------------------------------
# Article discovery (feed + HTML fallback)
# ---------------------------------------------------------------------------


def _element_text(element: ET.Element | None) -> str:
    if element is None:
        return ""
    text = element.text or ""
    if not text and len(element):
        text = "".join(element.itertext())
    return text.strip()


def _is_http_url(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


def _looks_like_article_url(url: str) -> bool:
    lower = url.lower()
    return any(seg in lower for seg in _ARTICLE_PATH_SEGMENTS)


def _atom_entry_url(entry: ET.Element) -> str:
    for link in entry.findall(f"{{{_ATOM_NS}}}link"):
        href = link.get("href", "").strip()
        if not href or not _is_http_url(href):
            continue
        rel = (link.get("rel") or "alternate").lower()
        if rel in ("alternate", ""):
            return href
    for link in entry.findall(f"{{{_ATOM_NS}}}link"):
        href = link.get("href", "").strip()
        if href and _is_http_url(href):
            return href
    return ""


def _parse_rss_items(root: ET.Element, limit: int) -> list[dict]:
    articles: list[dict] = []
    seen_urls: set[str] = set()
    for item in root.iter("item"):
        if len(articles) >= limit:
            break
        link_el = item.find("link")
        url = _element_text(link_el)
        if not url or not _is_http_url(url):
            guid_el = item.find("guid")
            guid = _element_text(guid_el)
            if guid and _is_http_url(guid):
                url = guid
            else:
                continue
        if url in seen_urls:
            continue
        seen_urls.add(url)
        articles.append(
            {
                "title": _element_text(item.find("title")),
                "url": url,
                "published": _element_text(item.find("pubDate")),
                "summary": _element_text(item.find("description")),
            }
        )
    return articles


def _parse_atom_entries(root: ET.Element, limit: int) -> list[dict]:
    articles: list[dict] = []
    seen_urls: set[str] = set()
    for entry in root.findall(f"{{{_ATOM_NS}}}entry"):
        if len(articles) >= limit:
            break
        url = _atom_entry_url(entry)
        if not url or url in seen_urls:
            continue
        seen_urls.add(url)
        published_el = entry.find(f"{{{_ATOM_NS}}}published")
        if published_el is None:
            published_el = entry.find(f"{{{_ATOM_NS}}}updated")
        summary_el = entry.find(f"{{{_ATOM_NS}}}summary")
        if summary_el is None:
            summary_el = entry.find(f"{{{_ATOM_NS}}}content")
        articles.append(
            {
                "title": _element_text(entry.find(f"{{{_ATOM_NS}}}title")),
                "url": url,
                "published": _element_text(published_el),
                "summary": _element_text(summary_el),
            }
        )
    return articles


def discover_feed_articles(feed_xml: str, limit: int = 20) -> list[dict]:
    """Parse RSS or Atom XML and return article candidate dicts."""
    try:
        root = ET.fromstring(feed_xml)
    except ET.ParseError as exc:
        logger.warning("[Archive] Feed XML parse error: %s", exc)
        return []

    tag = root.tag.rsplit("}", 1)[-1].lower()
    if tag == "feed":
        return _parse_atom_entries(root, limit)
    if tag == "rss":
        channel = root.find("channel")
        if channel is not None:
            return _parse_rss_items(channel, limit)
        return _parse_rss_items(root, limit)

    atom_articles = _parse_atom_entries(root, limit)
    if atom_articles:
        return atom_articles
    return _parse_rss_items(root, limit)


class _ArticleLinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._current_href: str | None = None
        self._current_text: list[str] = []
        self.links: list[tuple[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        href = ""
        for name, value in attrs:
            if name.lower() == "href" and value:
                href = value.strip()
                break
        if href:
            self._current_href = href
            self._current_text = []

    def handle_data(self, data: str) -> None:
        if self._current_href is not None and data:
            self._current_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() != "a" or self._current_href is None:
            return
        title = " ".join(self._current_text).strip()
        self.links.append((self._current_href, title))
        self._current_href = None
        self._current_text = []


def discover_html_article_links(html: str, base_url: str, limit: int = 20) -> list[dict]:
    """Discover likely article links from static HTML."""
    parser = _ArticleLinkParser()
    try:
        parser.feed(html)
    except Exception as exc:
        logger.warning("[Archive] HTML link parse error: %s", exc)
        return []

    articles: list[dict] = []
    seen_urls: set[str] = set()
    for href, link_text in parser.links:
        if len(articles) >= limit:
            break
        full = urljoin(base_url, href)
        if not _is_http_url(full) or not _looks_like_article_url(full):
            continue
        if full in seen_urls:
            continue
        seen_urls.add(full)
        title = link_text.strip()
        if not title:
            path = urlparse(full).path.rstrip("/")
            title = path.rsplit("/", 1)[-1].replace("-", " ").replace("_", " ")
        articles.append({"title": title, "url": full})
    return articles


def _candidate_urls(candidates: list[dict], limit: int) -> list[str]:
    urls: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        if len(urls) >= limit:
            break
        url = candidate.get("url")
        if not isinstance(url, str) or not url or url in seen:
            continue
        seen.add(url)
        urls.append(url)
    return urls


@http_retry
async def _fetch_url_text(url: str, client: httpx.AsyncClient) -> str | None:
    try:
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.text
    except httpx.HTTPError as exc:
        logger.warning("[Archive] Failed to fetch %s: %s", url, exc)
        return None


async def _collect_selector_links(page: Page, index_url: str, selector: str | None) -> list[str]:
    if selector:
        anchors = await page.query_selector_all(selector)
    else:
        anchors = await page.query_selector_all("a[href]")

    article_links: list[str] = []
    index_prefix = index_url.rsplit("/", 1)[0]
    for anchor in anchors:
        href = await anchor.get_attribute("href")
        if not href:
            continue
        full = urljoin(index_url, href)
        if selector:
            if full.startswith(index_prefix):
                article_links.append(full)
        elif full.startswith(index_prefix) and _looks_like_article_url(full):
            article_links.append(full)
    return article_links


async def _discover_article_candidates(
    target: dict,
    page: Page,
    client: httpx.AsyncClient,
    max_pages: int,
) -> list[str]:
    """Discover article URLs via feed, CSS selector, or HTML link fallback."""
    agency = target["agency"]
    index_url = target["url"]
    feed_url = target.get("feed_url")
    selector = target.get("article_link_selector")

    if feed_url:
        logger.info("[%s] Fetching feed: %s", agency, feed_url)
        feed_xml = await _fetch_url_text(feed_url, client)
        if feed_xml:
            feed_candidates = discover_feed_articles(feed_xml, limit=max_pages)
            feed_urls = _candidate_urls(feed_candidates, max_pages)
            if feed_urls:
                logger.info("[%s] Feed returned %d candidate articles", agency, len(feed_urls))
                return feed_urls
            logger.info("[%s] Feed returned no usable articles — falling back to HTML", agency)
        else:
            logger.info("[%s] Feed fetch failed — falling back to HTML", agency)

    logger.info("[%s] Loading index: %s", agency, index_url)
    await page.goto(
        index_url, wait_until="domcontentloaded", timeout=archive_cfg.playwright_goto_timeout_ms
    )
    await page.wait_for_timeout(archive_cfg.playwright_settle_ms)

    article_links = await _collect_selector_links(page, index_url, selector)
    if not article_links:
        html = await page.content()
        html_candidates = discover_html_article_links(html, index_url, limit=max_pages)
        article_links = _candidate_urls(html_candidates, max_pages)
        if article_links:
            logger.info(
                "[%s] HTML fallback found %d candidate articles",
                agency,
                len(article_links),
            )

    return list(dict.fromkeys(article_links))[:max_pages]


# ---------------------------------------------------------------------------
# Per-target crawler
# ---------------------------------------------------------------------------


async def crawl_target(
    browser: Browser,
    target: dict,
    max_pages: int,
    db,
    client: httpx.AsyncClient,
    dry_run: bool = False,
    rotator: ProxyRotator | None = None,
    target_entities: list[str] | None = None,
    target_keywords: list[str] | None = None,
) -> list[ArchivedPage]:
    """Paginate through a newsroom index, archive keyword-matched articles."""
    agency = target["agency"]
    keywords = target["keywords"]
    archived: list[ArchivedPage] = []

    context = await browser.new_context(
        user_agent=(
            "Mozilla/5.0 (compatible; LighthouseArchiver/1.0; +https://example.invalid/lighthouse)"
        ),
        **playwright_proxy_kwargs(rotator),
    )
    page: Page = await context.new_page()

    article_links = await _discover_article_candidates(target, page, client, max_pages)
    logger.info("[%s] Found %d candidate articles", agency, len(article_links))

    for url in article_links:
        if is_seen(db, f"archive:{url}"):
            logger.debug("[%s] Already archived, skipping: %s", agency, url)
            continue

        try:
            await page.goto(
                url, wait_until="domcontentloaded", timeout=archive_cfg.playwright_goto_timeout_ms
            )
            await page.wait_for_timeout(int(archive_cfg.crawl_delay * 1000))

            title = await page.title() or ""
            if title.strip().lower() in _DENY_TITLES:
                logger.debug("[%s] Skipping deny-page '%s': %s", agency, title, url)
                continue

            body = await page.inner_text("body") or ""
            matched = extract_keywords(body, keywords)

            if not matched:
                continue

            if (target_entities or target_keywords) and not _text_matches_targets(
                f"{title} {body}", target_entities, target_keywords
            ):
                continue

            html = await page.content()
            ts = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
            filename = f"{agency}_{ts}_{abs(hash(url)) % 10_000_000}.html"

            cid = None if dry_run else pin_to_ipfs(html, filename)

            record = ArchivedPage(
                agency=agency,
                url=url,
                title=title,
                snapshot_ts=ts,
                ipfs_cid=cid,
                matched_keywords=matched,
                local_path=str(out_dir / filename),
            )
            archived.append(record)
            if not dry_run:
                mark_seen(db, f"archive:{url}")
            logger.info(
                "[%s] Archived '%s'  keywords=%s  CID=%s",
                agency,
                title[:60],
                matched,
                cid,
            )
        except Exception as exc:
            logger.warning("[%s] Failed to archive %s: %s", agency, url, exc)
            continue

    await context.close()
    return archived


# ---------------------------------------------------------------------------
# Main runner
# ---------------------------------------------------------------------------


async def run_wayback_archiver(
    dry_run: bool = False,
    rotator: ProxyRotator | None = None,
    target_entities: list[str] | None = None,
    target_keywords: list[str] | None = None,
) -> list[ArchivedPage]:
    targets = archive_cfg.targets
    if target_entities or target_keywords:
        logger.info("[Archive] Target filter active")
        targets = _filter_sources_for_targets(targets, target_entities, target_keywords)
        if not targets:
            return []

    db = init_db(data_path("state.db"))
    all_archived: list[ArchivedPage] = []

    async with (
        make_httpx_client(
            rotator,
            timeout=archive_cfg.http_timeout,
            follow_redirects=True,
        ) as client,
        async_playwright() as pw,
    ):
        browser = await pw.chromium.launch(headless=True)

        for target in targets:
            results = await crawl_target(
                browser,
                target,
                max_pages=archive_cfg.max_pages_per_target,
                db=db,
                client=client,
                dry_run=dry_run,
                rotator=rotator,
                target_entities=target_entities,
                target_keywords=target_keywords,
            )
            all_archived.extend(results)

        await browser.close()

    manifest_path = out_dir / f"manifest_{int(time.time())}.json"
    manifest_path.write_text(
        json.dumps([asdict(a) for a in all_archived], indent=2),
        encoding="utf-8",
    )
    logger.info(
        "[Archiver] Done — %d pages archived, manifest: %s",
        len(all_archived),
        manifest_path,
    )
    return all_archived


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run_wayback_archiver())
