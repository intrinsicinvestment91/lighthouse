"""Unit tests for modules/narrative_analyzer.py"""

import time
from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock

import pytest

import config
from modules.narrative_analyzer import (
    _extract_text,
    _filter_pages_for_targets,
    _text_matches_targets,
    detect_shifts,
    run_narrative_analyzer,
)


@dataclass
class _FakePage:
    url: str
    agency: str
    local_path: str
    title: str = ""


def test_text_matches_targets_no_filters_returns_true():
    assert _text_matches_targets("any text", None, None) is True
    assert _text_matches_targets("", None, None) is True


def test_text_matches_targets_empty_text_with_filters_returns_false():
    assert _text_matches_targets("", ["DoD"], None) is False
    assert _text_matches_targets(None, None, ["fusion"]) is False


def test_text_matches_targets_entity_case_insensitive():
    assert _text_matches_targets("Lockheed Martin announcement", ["lockheed"], None) is True


def test_text_matches_targets_keyword_case_insensitive():
    assert _text_matches_targets("Plasma confinement study", None, ["PLASMA"]) is True


def test_text_matches_targets_no_match():
    assert _text_matches_targets("routine update", ["Lockheed"], ["plasma"]) is False


def test_filter_pages_for_targets_no_filters_returns_all():
    pages = [
        _FakePage("http://a", "DoD", "/tmp/a.html"),
        _FakePage("http://b", "NASA", "/tmp/b.html"),
    ]
    assert _filter_pages_for_targets(pages, None, None) == pages


def test_filter_pages_for_targets_matches_agency():
    pages = [
        _FakePage("http://a", "DoD", "/tmp/a.html"),
        _FakePage("http://b", "NASA", "/tmp/b.html"),
    ]
    filtered = _filter_pages_for_targets(pages, ["NASA"], None)
    assert len(filtered) == 1
    assert filtered[0].agency == "NASA"


def test_extract_text_strips_tags():
    html = "<h1>Hello</h1><p>World</p>"
    assert _extract_text(html) == "Hello World"


def test_extract_text_truncates():
    html = "x" * 10000
    assert len(_extract_text(html)) <= 4000


def test_extract_text_collapses_whitespace():
    html = "<p>  foo   bar  </p>"
    assert _extract_text(html) == "foo bar"


def test_detect_shifts_too_few_rows(tmp_db):
    tmp_db.execute(
        "INSERT INTO narrative_scores"
        " (url, agency, analyzed_at, urgency, specificity) VALUES (?,?,?,?,?)",
        ("http://example.com/1", "DoD", int(time.time()) - 9999, 5.0, 5.0),
    )
    tmp_db.commit()
    assert detect_shifts(tmp_db, "DoD") == []


def test_detect_shifts_finds_alert(tmp_db, monkeypatch):
    monkeypatch.setattr(config.narrative_cfg, "shift_threshold", 2.0)
    monkeypatch.setattr(config.narrative_cfg, "context_window_days", 1)
    old = int(time.time()) - 3 * 86400  # older than window

    # Historical baseline: urgency 3, specificity 3
    for i in range(5):
        tmp_db.execute(
            "INSERT OR REPLACE INTO narrative_scores"
            " (url, agency, analyzed_at, urgency, specificity, summary)"
            " VALUES (?, 'DoD', ?, 3.0, 3.0, 'routine')",
            (f"http://example.com/old{i}", old + i),
        )
    # Recent spike: urgency 8
    tmp_db.execute(
        "INSERT OR REPLACE INTO narrative_scores"
        " (url, agency, analyzed_at, urgency, specificity, summary)"
        " VALUES (?, 'DoD', ?, 8.0, 3.0, 'urgent')",
        ("http://example.com/new", int(time.time())),
    )
    tmp_db.commit()

    alerts = detect_shifts(tmp_db, "DoD")
    assert len(alerts) == 1
    assert alerts[0]["urgency_delta"] == pytest.approx(5.0)


def test_detect_shifts_no_alert_below_threshold(tmp_db, monkeypatch):
    monkeypatch.setattr(config.narrative_cfg, "shift_threshold", 2.0)
    monkeypatch.setattr(config.narrative_cfg, "context_window_days", 1)
    old = int(time.time()) - 3 * 86400

    for i in range(5):
        tmp_db.execute(
            "INSERT OR REPLACE INTO narrative_scores"
            " (url, agency, analyzed_at, urgency, specificity, summary)"
            " VALUES (?, 'DoD', ?, 5.0, 5.0, 'routine')",
            (f"http://example.com/old{i}", old + i),
        )
    # Small change — within threshold
    tmp_db.execute(
        "INSERT OR REPLACE INTO narrative_scores"
        " (url, agency, analyzed_at, urgency, specificity, summary)"
        " VALUES (?, 'DoD', ?, 6.0, 5.0, 'slight')",
        ("http://example.com/new", int(time.time())),
    )
    tmp_db.commit()
    assert detect_shifts(tmp_db, "DoD") == []


async def test_run_narrative_analyzer_skips_without_api_key(tmp_db, monkeypatch):
    monkeypatch.setattr(config.narrative_cfg, "anthropic_api_key", "")
    result = await run_narrative_analyzer(tmp_db, [])
    assert result == []


async def test_run_narrative_analyzer_shift_alert_has_score_and_provenance(
    tmp_db, tmp_path, monkeypatch, tmp_db_path
):
    from modules import db

    monkeypatch.setattr(db, "DB_PATH", tmp_db_path)
    monkeypatch.setattr(config.narrative_cfg, "anthropic_api_key", "test-key")
    monkeypatch.setattr(config.narrative_cfg, "shift_threshold", 2.0)
    monkeypatch.setattr(config.narrative_cfg, "context_window_days", 1)

    # Seed historical baseline so detect_shifts can fire
    old = int(time.time()) - 3 * 86400
    for i in range(5):
        tmp_db.execute(
            "INSERT OR REPLACE INTO narrative_scores"
            " (url, agency, analyzed_at, urgency, specificity, summary)"
            " VALUES (?, 'DoD', ?, 3.0, 3.0, 'baseline')",
            (f"http://example.com/old{i}", old + i),
        )
    tmp_db.commit()

    html_file = tmp_path / "spike.html"
    html_file.write_text("<html><body><p>Emergency announcement.</p></body></html>")
    page = _FakePage(url="http://example.com/spike", agency="DoD", local_path=str(html_file))

    mock_content = MagicMock()
    mock_content.text = '{"urgency": 9, "specificity": 8, "summary": "major shift"}'
    mock_message = MagicMock()
    mock_message.content = [mock_content]
    mock_client = AsyncMock()
    mock_client.messages.create = AsyncMock(return_value=mock_message)
    mock_anthropic = MagicMock()
    mock_anthropic.AsyncAnthropic.return_value = mock_client

    import sys

    sys.modules["anthropic"] = mock_anthropic

    alerts = await run_narrative_analyzer(tmp_db, [page], dry_run=False)

    assert len(alerts) >= 1
    alert = alerts[0]
    assert "score" in alert
    assert alert["score_label"] in ("low", "medium", "high")
    assert alert["provenance"]["source_type"] == "web_archive"

    rows = db.fetch_alerts_by_source("narrative_analyzer")
    assert len(rows) >= 1


async def test_run_narrative_analyzer_dry_run_skips_alert_insert(
    tmp_db, tmp_path, monkeypatch, tmp_db_path
):
    from modules import db

    monkeypatch.setattr(db, "DB_PATH", tmp_db_path)
    db.init_alerts_table()
    monkeypatch.setattr(config.narrative_cfg, "anthropic_api_key", "test-key")
    monkeypatch.setattr(config.narrative_cfg, "shift_threshold", 2.0)
    monkeypatch.setattr(config.narrative_cfg, "context_window_days", 1)

    # Seed baseline + spike directly so detect_shifts fires even in dry_run
    # (dry_run prevents _store_score, so we seed the spike row ourselves)
    old = int(time.time()) - 3 * 86400
    for i in range(5):
        tmp_db.execute(
            "INSERT OR REPLACE INTO narrative_scores"
            " (url, agency, analyzed_at, urgency, specificity, summary)"
            " VALUES (?, 'DoD', ?, 3.0, 3.0, 'baseline')",
            (f"http://example.com/dry{i}", old + i),
        )
    tmp_db.execute(
        "INSERT OR REPLACE INTO narrative_scores"
        " (url, agency, analyzed_at, urgency, specificity, summary)"
        " VALUES (?, 'DoD', ?, 9.0, 8.0, 'spike')",
        ("http://example.com/dry_spike", int(time.time())),
    )
    tmp_db.commit()

    # Use a page whose URL matches the pre-seeded spike so analyze_page contributes
    # to new_scores → agencies set → detect_shifts runs for 'DoD'
    html_file = tmp_path / "dry.html"
    html_file.write_text("<html><body><p>Emergency.</p></body></html>")
    page = _FakePage(url="http://example.com/dry_spike", agency="DoD", local_path=str(html_file))

    mock_content = MagicMock()
    mock_content.text = '{"urgency": 9, "specificity": 8, "summary": "major shift"}'
    mock_message = MagicMock()
    mock_message.content = [mock_content]
    mock_client = AsyncMock()
    mock_client.messages.create = AsyncMock(return_value=mock_message)
    mock_anthropic = MagicMock()
    mock_anthropic.AsyncAnthropic.return_value = mock_client

    import sys

    sys.modules["anthropic"] = mock_anthropic

    alerts = await run_narrative_analyzer(tmp_db, [page], dry_run=True)

    assert len(alerts) >= 1
    assert "score" in alerts[0]
    rows = db.fetch_alerts_by_source("narrative_analyzer")
    assert rows == []


async def test_run_narrative_analyzer_calls_llm(tmp_db, tmp_path, monkeypatch):
    monkeypatch.setattr(config.narrative_cfg, "anthropic_api_key", "test-key")
    monkeypatch.setattr(config.narrative_cfg, "shift_threshold", 99.0)  # no alerts

    html_file = tmp_path / "page.html"
    html_file.write_text("<html><body><p>Test press release content.</p></body></html>")

    page = _FakePage(
        url="http://example.com/press",
        agency="NASA",
        local_path=str(html_file),
    )

    mock_content = MagicMock()
    mock_content.text = '{"urgency": 5, "specificity": 5, "summary": "routine update"}'
    mock_message = MagicMock()
    mock_message.content = [mock_content]

    mock_client = AsyncMock()
    mock_client.messages.create = AsyncMock(return_value=mock_message)

    mock_anthropic = MagicMock()
    mock_anthropic.AsyncAnthropic.return_value = mock_client

    import sys

    sys.modules["anthropic"] = mock_anthropic

    result = await run_narrative_analyzer(tmp_db, [page], dry_run=False)

    assert isinstance(result, list)
    # Score was stored
    row = tmp_db.execute(
        "SELECT urgency FROM narrative_scores WHERE url = ?",
        ("http://example.com/press",),
    ).fetchone()
    assert row is not None
    assert row[0] == 5.0


async def test_run_narrative_analyzer_skips_nonmatching_with_targets(tmp_db, tmp_path, monkeypatch):
    monkeypatch.setattr(config.narrative_cfg, "anthropic_api_key", "test-key")
    monkeypatch.setattr(config.narrative_cfg, "shift_threshold", 99.0)

    match_html = tmp_path / "match.html"
    match_html.write_text("<html><body><p>Lockheed Martin contract award.</p></body></html>")
    skip_html = tmp_path / "skip.html"
    skip_html.write_text("<html><body><p>Routine cafeteria update.</p></body></html>")

    match_page = _FakePage(
        url="http://example.com/match",
        agency="Lockheed Martin",
        local_path=str(match_html),
        title="Contract award",
    )
    skip_page = _FakePage(
        url="http://example.com/skip",
        agency="DoD",
        local_path=str(skip_html),
        title="Routine",
    )

    mock_content = MagicMock()
    mock_content.text = '{"urgency": 5, "specificity": 5, "summary": "routine"}'
    mock_message = MagicMock()
    mock_message.content = [mock_content]
    mock_client = AsyncMock()
    mock_client.messages.create = AsyncMock(return_value=mock_message)
    mock_anthropic = MagicMock()
    mock_anthropic.AsyncAnthropic.return_value = mock_client

    import sys

    sys.modules["anthropic"] = mock_anthropic

    await run_narrative_analyzer(
        tmp_db,
        [match_page, skip_page],
        dry_run=False,
        target_entities=["Lockheed Martin"],
    )

    assert mock_client.messages.create.await_count == 1
    row = tmp_db.execute(
        "SELECT url FROM narrative_scores WHERE url = ?",
        ("http://example.com/match",),
    ).fetchone()
    assert row is not None
    skip_row = tmp_db.execute(
        "SELECT url FROM narrative_scores WHERE url = ?",
        ("http://example.com/skip",),
    ).fetchone()
    assert skip_row is None
