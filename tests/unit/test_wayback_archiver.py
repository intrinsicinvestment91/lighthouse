"""Unit tests for modules/wayback_archiver.py"""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from modules.wayback_archiver import (
    ArchivedPage,
    _candidate_urls,
    _discover_article_candidates,
    _filter_sources_for_targets,
    _text_matches_targets,
    crawl_target,
    discover_feed_articles,
    discover_html_article_links,
    extract_keywords,
    pin_to_ipfs,
    run_wayback_archiver,
)

_RSS_SAMPLE = """\
<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <item>
      <title>Strategic Initiative Update</title>
      <link>https://www.defense.gov/News/Release/Article/1/</link>
      <pubDate>Mon, 01 Jan 2026 12:00:00 GMT</pubDate>
      <description>Personnel changes announced.</description>
    </item>
    <item>
      <title>Duplicate Link Item</title>
      <link>https://www.defense.gov/News/Release/Article/1/</link>
      <pubDate>Tue, 02 Jan 2026 12:00:00 GMT</pubDate>
      <description>Duplicate URL should be skipped.</description>
    </item>
    <item>
      <title>Second Release</title>
      <link>https://www.defense.gov/News/Release/Article/2/</link>
      <pubDate>Wed, 03 Jan 2026 12:00:00 GMT</pubDate>
      <description>Another summary.</description>
    </item>
  </channel>
</rss>"""

_ATOM_SAMPLE = """\
<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <title>NASA Initiative Launch</title>
    <link rel="alternate" href="https://www.nasa.gov/news/article/one/"/>
    <published>2026-02-01T10:00:00Z</published>
    <summary>Program office update.</summary>
  </entry>
</feed>"""


def test_extract_keywords_matches():
    keywords = ["personnel", "strategic", "SAP"]
    text = "The strategic initiative involves personnel changes."
    result = extract_keywords(text, keywords)
    assert "personnel" in result
    assert "strategic" in result
    assert "SAP" not in result


def test_extract_keywords_empty_text():
    assert extract_keywords("", ["personnel"]) == []


def test_extract_keywords_empty_keywords():
    assert extract_keywords("some text here", []) == []


def test_extract_keywords_case_insensitive():
    result = extract_keywords("PERSONNEL update", ["personnel"])
    assert "personnel" in result


def test_extract_keywords_no_match():
    result = extract_keywords("routine operations", ["SAP", "special access"])
    assert result == []


def test_pin_to_ipfs_returns_none_on_failure(tmp_path):
    with patch("modules.wayback_archiver.ipfshttpclient.connect") as mock_connect:
        mock_connect.side_effect = ConnectionRefusedError("daemon not running")
        result = pin_to_ipfs("<html></html>", "test.html")
    assert result is None


def test_archived_page_dataclass():
    page = ArchivedPage(
        agency="NASA",
        url="https://www.nasa.gov/example",
        title="Test Article",
        snapshot_ts="20260101T000000Z",
        ipfs_cid="bafytest123",
        matched_keywords=["initiative", "personnel"],
        local_path="data/archives/test.html",
    )
    assert page.agency == "NASA"
    assert page.ipfs_cid == "bafytest123"
    assert "initiative" in page.matched_keywords
    assert page.local_path.endswith(".html")


def test_archived_page_none_cid():
    page = ArchivedPage(
        agency="DoD",
        url="https://www.defense.gov/example",
        title="Article",
        snapshot_ts="20260101T000000Z",
        ipfs_cid=None,
        matched_keywords=["strategic"],
        local_path="data/archives/test.html",
    )
    assert page.ipfs_cid is None


def test_pin_to_ipfs_returns_cid_on_success(tmp_path, monkeypatch):
    monkeypatch.setattr("modules.wayback_archiver.out_dir", tmp_path)

    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.add.return_value = {"Hash": "bafytest123"}

    with patch("modules.wayback_archiver.ipfshttpclient.connect", return_value=mock_client):
        result = pin_to_ipfs("<html>test</html>", "test.html")

    assert result == "bafytest123"


def test_pin_to_ipfs_pinata_missing_jwt_returns_none(monkeypatch, tmp_path):
    monkeypatch.setattr("modules.wayback_archiver.out_dir", tmp_path)
    monkeypatch.setattr("modules.wayback_archiver.archive_cfg.ipfs_pinning_service", "pinata")
    monkeypatch.setattr("modules.wayback_archiver.archive_cfg.pinata_jwt", "")
    result = pin_to_ipfs("<html></html>", "test_pinata.html")
    assert result is None


def test_pin_to_ipfs_web3storage_missing_token_returns_none(monkeypatch, tmp_path):
    monkeypatch.setattr("modules.wayback_archiver.out_dir", tmp_path)
    monkeypatch.setattr("modules.wayback_archiver.archive_cfg.ipfs_pinning_service", "web3storage")
    monkeypatch.setattr("modules.wayback_archiver.archive_cfg.web3storage_token", "")
    result = pin_to_ipfs("<html></html>", "test_web3.html")
    assert result is None


def test_pin_to_ipfs_unknown_service_falls_back_to_local(monkeypatch, tmp_path):
    monkeypatch.setattr("modules.wayback_archiver.out_dir", tmp_path)
    monkeypatch.setattr("modules.wayback_archiver.archive_cfg.ipfs_pinning_service", "mystery")
    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.add.return_value = {"Hash": "bafytest123"}

    with patch("modules.wayback_archiver.ipfshttpclient.connect", return_value=mock_client):
        result = pin_to_ipfs("<html>test</html>", "test_unknown.html")

    assert result == "bafytest123"


def test_text_matches_targets_no_filters_returns_true():
    assert _text_matches_targets("any text", None, None) is True
    assert _text_matches_targets("", None, None) is True


def test_text_matches_targets_empty_text_with_filters_returns_false():
    assert _text_matches_targets("", ["NASA"], None) is False
    assert _text_matches_targets(None, None, ["fusion"]) is False


def test_text_matches_targets_entity_case_insensitive():
    assert _text_matches_targets("Lockheed Martin contract", ["lockheed martin"], None) is True


def test_text_matches_targets_keyword_case_insensitive():
    assert _text_matches_targets("Plasma research update", None, ["PLASMA"]) is True


def test_text_matches_targets_no_match():
    assert _text_matches_targets("routine operations", ["Lockheed"], ["plasma"]) is False


def _sample_archive_sources() -> list[dict]:
    return [
        {"agency": "DoD", "url": "https://www.defense.gov/news/", "keywords": ["strategic"]},
        {"agency": "NASA", "url": "https://www.nasa.gov/news/", "keywords": ["initiative"]},
        {"agency": "DoE", "url": "https://www.energy.gov/newsroom", "keywords": ["audit"]},
    ]


def test_filter_sources_for_targets_no_filters_returns_all():
    sources = _sample_archive_sources()
    assert _filter_sources_for_targets(sources, None, None) == sources


def test_filter_sources_for_targets_preserves_order():
    sources = _sample_archive_sources()
    filtered = _filter_sources_for_targets(sources, ["NASA"], None)
    assert [s["agency"] for s in filtered] == ["NASA"]


def test_filter_sources_for_targets_no_match_returns_empty():
    sources = _sample_archive_sources()
    assert _filter_sources_for_targets(sources, ["Nonexistent"], ["unrelated"]) == []


@pytest.mark.asyncio
async def test_run_wayback_archiver_accepts_target_params(monkeypatch):
    captured: dict = {}

    async def track_crawl(*_args, **kwargs):
        captured.update(kwargs)
        return []

    monkeypatch.setattr(
        "modules.wayback_archiver.archive_cfg.targets",
        [{"agency": "NASA", "url": "https://www.nasa.gov/", "keywords": ["initiative"]}],
    )
    monkeypatch.setattr("modules.wayback_archiver.init_db", lambda _: MagicMock())
    monkeypatch.setattr("modules.wayback_archiver.crawl_target", track_crawl)

    mock_pw = MagicMock()
    mock_browser = AsyncMock()
    mock_browser.close = AsyncMock()
    mock_pw.chromium.launch = AsyncMock(return_value=mock_browser)

    mock_context = MagicMock()
    mock_context.__aenter__ = AsyncMock(return_value=mock_pw)
    mock_context.__aexit__ = AsyncMock(return_value=None)
    monkeypatch.setattr(
        "modules.wayback_archiver.async_playwright",
        lambda: mock_context,
    )

    result = await run_wayback_archiver(
        dry_run=True,
        target_entities=["NASA"],
        target_keywords=["initiative"],
    )

    assert result == []
    assert captured["target_entities"] == ["NASA"]
    assert captured["target_keywords"] == ["initiative"]


@pytest.mark.asyncio
async def test_run_wayback_archiver_no_matching_sources_returns_empty(monkeypatch):
    monkeypatch.setattr(
        "modules.wayback_archiver.archive_cfg.targets",
        [{"agency": "DoD", "url": "https://www.defense.gov/", "keywords": ["strategic"]}],
    )

    result = await run_wayback_archiver(
        dry_run=True,
        target_entities=["Nonexistent Entity"],
    )

    assert result == []


def test_discover_feed_articles_rss_parsing():
    articles = discover_feed_articles(_RSS_SAMPLE)
    assert len(articles) == 2
    first = articles[0]
    assert first["title"] == "Strategic Initiative Update"
    assert first["url"] == "https://www.defense.gov/News/Release/Article/1/"
    assert first["published"] == "Mon, 01 Jan 2026 12:00:00 GMT"
    assert "Personnel" in first["summary"]


def test_discover_feed_articles_atom_parsing():
    articles = discover_feed_articles(_ATOM_SAMPLE)
    assert len(articles) == 1
    entry = articles[0]
    assert entry["title"] == "NASA Initiative Launch"
    assert entry["url"] == "https://www.nasa.gov/news/article/one/"
    assert entry["published"] == "2026-02-01T10:00:00Z"
    assert "Program office" in entry["summary"]


def test_discover_feed_articles_respects_limit():
    articles = discover_feed_articles(_RSS_SAMPLE, limit=1)
    assert len(articles) == 1


def test_discover_feed_articles_malformed_xml_returns_empty():
    assert discover_feed_articles("not xml at all <<<") == []


def test_candidate_urls_deduplicates():
    candidates = [
        {"url": "https://example.com/news/one"},
        {"url": "https://example.com/news/one"},
        {"url": "https://example.com/news/two"},
    ]
    assert _candidate_urls(candidates, 10) == [
        "https://example.com/news/one",
        "https://example.com/news/two",
    ]


_HTML_SAMPLE = """
<html><body>
  <a href="/about/">About Us</a>
  <a href="/news/first-story">First Story</a>
  <a href="https://www.example.gov/releases/second">Second Release</a>
  <a href="/news/first-story">Duplicate</a>
  <a href="mailto:press@example.gov">Email</a>
</body></html>
"""


def test_discover_html_article_links_finds_article_paths():
    links = discover_html_article_links(_HTML_SAMPLE, "https://www.example.gov/newsroom/")
    urls = [link["url"] for link in links]
    assert "https://www.example.gov/news/first-story" in urls
    assert "https://www.example.gov/releases/second" in urls
    assert all("/about/" not in url for url in urls)


def test_discover_html_article_links_normalizes_relative_urls():
    links = discover_html_article_links(_HTML_SAMPLE, "https://www.example.gov/newsroom/")
    first = next(link for link in links if "first-story" in link["url"])
    assert first["url"] == "https://www.example.gov/news/first-story"
    assert first["title"] == "First Story"


def test_discover_html_article_links_filters_irrelevant_links():
    links = discover_html_article_links(_HTML_SAMPLE, "https://www.example.gov/")
    assert all("mailto:" not in link["url"] for link in links)
    assert all("/about/" not in link["url"] for link in links)


def test_discover_html_article_links_deduplicates_and_respects_limit():
    links = discover_html_article_links(_HTML_SAMPLE, "https://www.example.gov/", limit=1)
    assert len(links) == 1


def _mock_playwright_page(
    *,
    goto_calls: list | None = None,
    selector_hrefs: list[str] | None = None,
    page_html: str = "",
    title: str = "Article",
    body: str = "strategic initiative personnel update",
):
    page = AsyncMock()

    async def _record_goto(url, **kwargs):
        if goto_calls is not None:
            goto_calls.append(url)

    page.goto = AsyncMock(side_effect=_record_goto)
    page.wait_for_timeout = AsyncMock()
    page.content = AsyncMock(return_value=page_html)
    page.title = AsyncMock(return_value=title)
    page.inner_text = AsyncMock(return_value=body)

    if selector_hrefs is not None:
        anchors = []
        for href in selector_hrefs:
            anchor = AsyncMock()
            anchor.get_attribute = AsyncMock(return_value=href)
            anchors.append(anchor)
        page.query_selector_all = AsyncMock(return_value=anchors)
    return page


@pytest.mark.asyncio
async def test_discover_article_candidates_uses_feed_when_configured():
    target = {
        "agency": "DoD",
        "url": "https://www.defense.gov/News/",
        "feed_url": "https://www.defense.gov/feed.xml",
        "keywords": ["strategic"],
    }
    page = _mock_playwright_page(goto_calls=[])

    async def fake_fetch(url, client):
        assert url == target["feed_url"]
        return _RSS_SAMPLE

    async with httpx.AsyncClient() as client:
        with patch("modules.wayback_archiver._fetch_url_text", side_effect=fake_fetch):
            urls = await _discover_article_candidates(target, page, client, max_pages=10)

    assert len(urls) == 2
    assert page.goto.await_count == 0


@pytest.mark.asyncio
async def test_discover_article_candidates_feed_failure_falls_back_to_html():
    target = {
        "agency": "DoE",
        "url": "https://www.energy.gov/newsroom",
        "feed_url": "https://www.energy.gov/bad-feed",
        "keywords": ["audit"],
    }
    goto_calls: list[str] = []
    page = _mock_playwright_page(
        goto_calls=goto_calls,
        selector_hrefs=[],
        page_html=_HTML_SAMPLE,
    )

    async def fake_fetch(url, client):
        return None

    async with httpx.AsyncClient() as client:
        with patch("modules.wayback_archiver._fetch_url_text", side_effect=fake_fetch):
            urls = await _discover_article_candidates(target, page, client, max_pages=10)

    assert goto_calls[0] == target["url"]
    assert len(urls) >= 1


@pytest.mark.asyncio
async def test_discover_article_candidates_empty_selector_uses_html_fallback():
    target = {
        "agency": "NASA",
        "url": "https://www.nasa.gov/news/",
        "keywords": ["initiative"],
        "article_link_selector": "article.missing a[href]",
    }
    page = _mock_playwright_page(selector_hrefs=[], page_html=_HTML_SAMPLE)

    async with httpx.AsyncClient() as client:
        urls = await _discover_article_candidates(target, page, client, max_pages=10)

    assert len(urls) >= 1


@pytest.mark.asyncio
async def test_crawl_target_targeted_filter_skips_non_matching_articles(monkeypatch):
    monkeypatch.setattr("modules.wayback_archiver.playwright_proxy_kwargs", lambda _r: {})
    browser = AsyncMock()
    context = AsyncMock()
    browser.new_context = AsyncMock(return_value=context)
    context.new_page = AsyncMock()
    context.close = AsyncMock()

    page = _mock_playwright_page(
        selector_hrefs=["https://www.nasa.gov/news/article/one/"],
        body="strategic initiative personnel update",
        title="NASA Update",
    )
    context.new_page.return_value = page

    db = MagicMock()
    monkeypatch.setattr("modules.wayback_archiver.is_seen", lambda _db, _key: False)

    target = {
        "agency": "NASA",
        "url": "https://www.nasa.gov/news/",
        "keywords": ["initiative"],
    }

    async with httpx.AsyncClient() as client:
        with patch(
            "modules.wayback_archiver._discover_article_candidates",
            AsyncMock(return_value=["https://www.nasa.gov/news/article/one/"]),
        ):
            archived = await crawl_target(
                browser,
                target,
                max_pages=5,
                db=db,
                client=client,
                dry_run=True,
                target_entities=["Lockheed Martin"],
                target_keywords=["fusion"],
            )

    assert archived == []


@pytest.mark.asyncio
async def test_crawl_target_archives_without_target_filters(monkeypatch, tmp_path):
    monkeypatch.setattr("modules.wayback_archiver.playwright_proxy_kwargs", lambda _r: {})
    browser = AsyncMock()
    context = AsyncMock()
    browser.new_context = AsyncMock(return_value=context)
    context.new_page = AsyncMock()
    context.close = AsyncMock()

    page = _mock_playwright_page(
        selector_hrefs=["https://www.nasa.gov/news/article/one/"],
        body="strategic initiative personnel update",
        title="NASA Initiative",
    )
    context.new_page.return_value = page

    db = MagicMock()
    monkeypatch.setattr("modules.wayback_archiver.is_seen", lambda _db, _key: False)
    monkeypatch.setattr("modules.wayback_archiver.out_dir", tmp_path)
    monkeypatch.setattr("modules.wayback_archiver.pin_to_ipfs", lambda html, fn: None)

    target = {
        "agency": "NASA",
        "url": "https://www.nasa.gov/news/",
        "keywords": ["initiative", "strategic"],
    }

    async with httpx.AsyncClient() as client:
        with patch(
            "modules.wayback_archiver._discover_article_candidates",
            AsyncMock(return_value=["https://www.nasa.gov/news/article/one/"]),
        ):
            archived = await crawl_target(
                browser,
                target,
                max_pages=5,
                db=db,
                client=client,
                dry_run=True,
            )

    assert len(archived) == 1
    assert archived[0].agency == "NASA"
    assert "initiative" in archived[0].matched_keywords


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "deny_title",
    ["Access Denied", "access denied", "403 Forbidden", "Page Not Found"],
)
async def test_crawl_target_skips_deny_title_pages(monkeypatch, deny_title):
    monkeypatch.setattr("modules.wayback_archiver.playwright_proxy_kwargs", lambda _r: {})
    browser = AsyncMock()
    context = AsyncMock()
    browser.new_context = AsyncMock(return_value=context)
    context.new_page = AsyncMock()
    context.close = AsyncMock()

    page = _mock_playwright_page(
        selector_hrefs=["https://www.defense.gov/news/article/one/"],
        body="strategic initiative personnel update",
        title=deny_title,
    )
    context.new_page.return_value = page

    db = MagicMock()
    monkeypatch.setattr("modules.wayback_archiver.is_seen", lambda _db, _key: False)

    target = {
        "agency": "DoD",
        "url": "https://www.defense.gov/News/",
        "keywords": ["initiative", "strategic", "personnel"],
    }

    async with httpx.AsyncClient() as client:
        with patch(
            "modules.wayback_archiver._discover_article_candidates",
            AsyncMock(return_value=["https://www.defense.gov/news/article/one/"]),
        ):
            archived = await crawl_target(
                browser,
                target,
                max_pages=5,
                db=db,
                client=client,
                dry_run=True,
            )

    assert archived == []
