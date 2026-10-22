"""Unit tests for modules/arxiv_sentinel.py"""

import json
import time
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from pytest_httpx import HTTPXMock

import config
from config import _ARXIV_RESEARCHERS_FALLBACK, _load_seed_researchers, get_arxiv_cfg
from modules.arxiv_sentinel import (
    _filter_researchers_for_targets,
    _paper_matches_target_keywords,
    fetch_latest_paper,
    run_arxiv_sentinel,
)

_ATOM_WITH_ENTRY = """\
<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>http://arxiv.org/abs/2601.00001v1</id>
    <published>2026-01-15T12:00:00Z</published>
    <title>Plasma Confinement Techniques</title>
  </entry>
</feed>"""

_ATOM_EMPTY = """\
<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
</feed>"""


def test_seed_researchers_json_exists_parses_and_validates_via_loader():
    path = Path("seed/researchers.json")
    assert path.is_file(), "seed/researchers.json must exist in repo checkout"
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw.get("schema_version") == 1
    assert isinstance(raw.get("researchers"), list)

    researchers = _load_seed_researchers(path)
    assert len(researchers) == 10
    assert researchers[0]["name"] == "Synthetic Researcher A"


def test_load_seed_researchers_missing_returns_fallback():
    got = _load_seed_researchers(Path("/nonexistent/path/researchers.json"))
    assert got == list(_ARXIV_RESEARCHERS_FALLBACK)


@pytest.mark.parametrize(
    "payload",
    (
        "{}",
        '{"researchers": "not-a-list"}',
        '{"researchers": [{"institution": "x", "arxiv_query": "y"}]}',
        '{"researchers": [{"name": " ", "institution": "x", "arxiv_query": "y"}]}',
    ),
)
def test_load_seed_researchers_invalid_raises(tmp_path, payload: str):
    bad = tmp_path / "bad.json"
    bad.write_text(payload, encoding="utf-8")
    with pytest.raises(ValueError):
        _load_seed_researchers(bad)


def test_load_seed_researchers_malformed_json_raises(tmp_path):
    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    with pytest.raises(ValueError, match="Invalid JSON"):
        _load_seed_researchers(broken)


def test_arxiv_config_researchers_default_from_seed_matches_loader_when_present():
    assert config.ArxivConfig().researchers == _load_seed_researchers("seed/researchers.json")


def test_get_arxiv_cfg_returns_module_singleton_and_ten_researchers():
    assert get_arxiv_cfg() is config.arxiv_cfg
    assert len(get_arxiv_cfg().researchers) == 10


def _researcher_match_labels(cfg: config.ArxivConfig) -> list[str]:
    return [f"{r['name']} {r.get('institution', '')}".strip() for r in cfg.researchers]


def test_operational_researcher_config_excludes_fixture_name_jones_a():
    labels = _researcher_match_labels(config.arxiv_cfg)
    for label in labels:
        assert "jones a" not in label.lower()


def test_operational_researcher_config_excludes_darpa_label():
    labels = _researcher_match_labels(config.arxiv_cfg)
    for label in labels:
        assert "darpa" not in label.lower()


def test_operational_researcher_config_has_fusion_or_plasma_keyword():
    labels = _researcher_match_labels(config.arxiv_cfg)
    assert any("fusion" in lab.lower() or "plasma" in lab.lower() for lab in labels)


def test_target_filter_selects_fusion_keywords():
    match_labels = _researcher_match_labels(config.arxiv_cfg)
    selected = _filter_researchers_for_targets(
        match_labels,
        target_entities=None,
        target_keywords=["fusion", "Helion", "Commonwealth Fusion Systems"],
    )
    assert len(selected) >= 1


def test_target_filter_selects_synthetic_laboratory_keyword():
    match_labels = _researcher_match_labels(config.arxiv_cfg)
    selected = _filter_researchers_for_targets(
        match_labels,
        target_entities=None,
        target_keywords=["Synthetic Inertial Confinement Laboratory"],
    )
    assert len(selected) >= 1


async def test_fetch_latest_paper_returns_date(httpx_mock: HTTPXMock):
    httpx_mock.add_response(text=_ATOM_WITH_ENTRY, headers={"Content-Type": "application/atom+xml"})
    import httpx

    async with httpx.AsyncClient() as client:
        latest, paper = await fetch_latest_paper({"name": "Smith J", "topics": ["plasma"]}, client)
    assert latest == "2026-01-15"
    assert paper is not None
    assert "Plasma" in paper["title"]


async def test_fetch_latest_paper_empty_returns_none(httpx_mock: HTTPXMock):
    httpx_mock.add_response(text=_ATOM_EMPTY, headers={"Content-Type": "application/atom+xml"})
    import httpx

    async with httpx.AsyncClient() as client:
        latest, paper = await fetch_latest_paper({"name": "Jones A"}, client)
    assert latest is None
    assert paper is None


async def test_fetch_latest_paper_no_topics(httpx_mock: HTTPXMock):
    httpx_mock.add_response(text=_ATOM_WITH_ENTRY, headers={"Content-Type": "application/atom+xml"})
    import httpx

    async with httpx.AsyncClient() as client:
        latest, _paper = await fetch_latest_paper({"name": "Smith J"}, client)
    assert latest == "2026-01-15"


def test_filter_researchers_for_targets_none_keeps_all():
    researchers = ["Synthetic Researcher A", "Synthetic Researcher E"]
    assert _filter_researchers_for_targets(researchers, None, None) == researchers


def test_filter_researchers_for_targets_empty_lists_keeps_all():
    researchers = ["Synthetic Researcher A", "Synthetic Researcher E"]
    assert _filter_researchers_for_targets(researchers, [], []) == researchers


def test_filter_researchers_for_targets_entity_case_insensitive():
    researchers = ["Synthetic Researcher A", "Synthetic Researcher E"]
    result = _filter_researchers_for_targets(researchers, ["synthetic researcher e"], None)
    assert result == ["Synthetic Researcher E"]


def test_filter_researchers_for_targets_keyword_case_insensitive():
    researchers = ["Synthetic Researcher A ExampleInstitute", "Synthetic Researcher E"]
    result = _filter_researchers_for_targets(researchers, None, ["exampleinstitute"])
    assert result == ["Synthetic Researcher A ExampleInstitute"]


def test_filter_researchers_for_targets_no_match_returns_empty():
    researchers = ["Synthetic Researcher A", "Synthetic Researcher E"]
    assert _filter_researchers_for_targets(researchers, ["Unknown Corp"], ["xyz"]) == []


def test_paper_matches_target_keywords_no_keywords_returns_true():
    assert _paper_matches_target_keywords({"title": "Fusion"}, None) is True
    assert _paper_matches_target_keywords({"title": "Fusion"}, []) is True


def test_paper_matches_target_keywords_title():
    paper = {"title": "Plasma Confinement Techniques", "summary": "", "categories": []}
    assert _paper_matches_target_keywords(paper, ["plasma"]) is True


def test_paper_matches_target_keywords_summary():
    paper = {"title": "Paper", "summary": "Magnetic confinement advances", "categories": []}
    assert _paper_matches_target_keywords(paper, ["confinement"]) is True


def test_paper_matches_target_keywords_no_match():
    paper = {"title": "Unrelated Topic", "summary": "Nothing here", "categories": ["cs.AI"]}
    assert _paper_matches_target_keywords(paper, ["plasma"]) is False


async def test_run_arxiv_sentinel_no_researchers(tmp_db, monkeypatch):
    # Patch the exact config object used by modules.arxiv_sentinel and
    # make any accidental network path fail loudly.
    import modules.arxiv_sentinel as arxiv_sentinel_module

    monkeypatch.setattr(arxiv_sentinel_module.arxiv_cfg, "researchers", [])

    monkeypatch.setattr(
        arxiv_sentinel_module,
        "fetch_latest_paper",
        AsyncMock(side_effect=AssertionError("unit test attempted live arXiv fetch")),
    )
    result = await run_arxiv_sentinel(tmp_db)
    assert result == []


async def test_run_arxiv_sentinel_active_researcher(httpx_mock: HTTPXMock, tmp_db, monkeypatch):
    monkeypatch.setattr(
        config.arxiv_cfg,
        "researchers",
        [{"name": "Smith J", "institution": "MIT PSFC", "topics": ["plasma"]}],
    )
    httpx_mock.add_response(text=_ATOM_WITH_ENTRY, headers={"Content-Type": "application/atom+xml"})
    alerts = await run_arxiv_sentinel(tmp_db)
    assert alerts == []  # no silence — still publishing
    row = tmp_db.execute("SELECT last_pub_date FROM researchers WHERE name = 'Smith J'").fetchone()
    assert row is not None
    assert row[0] == "2026-01-15"


async def test_run_arxiv_sentinel_silence_alert(httpx_mock: HTTPXMock, tmp_db, monkeypatch):
    monkeypatch.setattr(
        config.arxiv_cfg,
        "researchers",
        [{"name": "Jones A", "institution": "DARPA", "topics": []}],
    )
    monkeypatch.setattr(config.arxiv_cfg, "silence_threshold_days", 0)
    httpx_mock.add_response(text=_ATOM_EMPTY, headers={"Content-Type": "application/atom+xml"})
    alerts = await run_arxiv_sentinel(tmp_db)
    assert len(alerts) == 1
    assert alerts[0]["name"] == "Jones A"
    assert "silent_days" in alerts[0]


async def test_run_arxiv_sentinel_silence_alert_has_score_and_provenance(
    httpx_mock: HTTPXMock, tmp_db, monkeypatch, tmp_db_path
):
    from modules import db

    monkeypatch.setattr(db, "DB_PATH", tmp_db_path)
    monkeypatch.setattr(
        config.arxiv_cfg,
        "researchers",
        [{"name": "Synthetic Researcher E", "institution": "Rochester", "topics": ["fusion"]}],
    )
    monkeypatch.setattr(config.arxiv_cfg, "silence_threshold_days", 0)
    httpx_mock.add_response(text=_ATOM_EMPTY, headers={"Content-Type": "application/atom+xml"})

    alerts = await run_arxiv_sentinel(tmp_db, dry_run=False)

    assert len(alerts) == 1
    assert "score" in alerts[0]
    assert alerts[0]["score_label"] in ("low", "medium", "high")
    assert alerts[0]["provenance"]["source_type"] == "arxiv_api"
    assert "Synthetic+Researcher+E" in alerts[0]["provenance"]["source_url"]

    rows = db.fetch_alerts_by_source("arxiv_sentinel")
    assert len(rows) == 1
    assert rows[0]["alert"]["name"] == "Synthetic Researcher E"


async def test_run_arxiv_sentinel_dry_run_skips_alert_insert(
    httpx_mock: HTTPXMock, tmp_db, monkeypatch, tmp_db_path
):
    from modules import db

    monkeypatch.setattr(db, "DB_PATH", tmp_db_path)
    db.init_alerts_table()
    monkeypatch.setattr(
        config.arxiv_cfg,
        "researchers",
        [{"name": "Synthetic Researcher A", "institution": "Synthetic Fusion Plasma Institute"}],
    )
    monkeypatch.setattr(config.arxiv_cfg, "silence_threshold_days", 0)
    httpx_mock.add_response(text=_ATOM_EMPTY, headers={"Content-Type": "application/atom+xml"})

    alerts = await run_arxiv_sentinel(tmp_db, dry_run=True)

    assert len(alerts) == 1
    assert "score" in alerts[0]
    rows = db.fetch_alerts_by_source("arxiv_sentinel")
    assert rows == []


async def test_run_arxiv_sentinel_notified_not_re_alerted(
    httpx_mock: HTTPXMock, tmp_db, monkeypatch
):
    monkeypatch.setattr(
        config.arxiv_cfg,
        "researchers",
        [{"name": "Jones A", "institution": "DARPA"}],
    )
    monkeypatch.setattr(config.arxiv_cfg, "silence_threshold_days", 0)
    old = int(time.time()) - 3600
    tmp_db.execute(
        "INSERT INTO researchers (name, institution, last_pub_date, first_silence_at, notified)"
        " VALUES (?, ?, ?, ?, ?)",
        ("Jones A", "DARPA", None, old, 1),
    )
    tmp_db.commit()
    httpx_mock.add_response(text=_ATOM_EMPTY, headers={"Content-Type": "application/atom+xml"})
    alerts = await run_arxiv_sentinel(tmp_db)
    assert alerts == []  # already notified


async def test_run_arxiv_sentinel_target_filter_excludes_all_skips_fetch(tmp_db, monkeypatch):
    import modules.arxiv_sentinel as arxiv_sentinel_module

    monkeypatch.setattr(
        arxiv_sentinel_module.arxiv_cfg,
        "researchers",
        [{"name": "Synthetic Researcher A", "institution": "Synthetic Fusion Plasma Institute"}],
    )
    fetch_mock = AsyncMock(side_effect=AssertionError("unit test attempted live arXiv fetch"))
    monkeypatch.setattr(arxiv_sentinel_module, "fetch_latest_paper", fetch_mock)

    result = await run_arxiv_sentinel(
        tmp_db,
        target_entities=["Nonexistent Entity"],
        target_keywords=["unrelated"],
    )

    assert result == []
    fetch_mock.assert_not_called()
