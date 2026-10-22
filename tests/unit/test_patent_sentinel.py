"""Unit tests for modules/patent_sentinel.py"""

from pytest_httpx import HTTPXMock

import modules.patent_sentinel as patent_sentinel_module
from modules.patent_sentinel import (
    _filter_assignees_for_targets,
    _patent_record_matches_target_keywords,
    _validate_patent_provider,
    check_patent_provider_health,
    fetch_uspto_patents,
    fetch_wipo_patents,
    matches_assignee_filter,
    normalize_record,
)

ASSIGNEES = ["MIT", "Lockheed", "Helion", "DARPA"]


def test_normalize_record_uspto(sample_uspto_raw):
    result = normalize_record(sample_uspto_raw, "USPTO")
    assert result["source"] == "USPTO"
    assert result["id"] == "US123456"
    assert result["title"] == "Advanced Plasma Confinement Device"
    assert result["assignee"] == "MIT"
    assert result["ipc"] == "G21B"
    assert result["abstract"].startswith("A novel approach")


def test_normalize_record_wipo(sample_wipo_raw):
    result = normalize_record(sample_wipo_raw, "WIPO")
    assert result["source"] == "WIPO"
    assert result["id"] == "WO2026123456"
    assert result["title"] == "High-Entropy Alloy for Aerospace Applications"
    assert result["assignee"] == "Lockheed Martin Corp"
    assert result["ipc"] == "C22C 30/00"
    assert result["abstract"] == ""


def test_normalize_record_abstract_truncated():
    raw = {
        "patent_id": "US1",
        "patent_title": "T",
        "patent_date": "2026-01-01",
        "patent_abstract": "x" * 600,
        "assignee_organization": "Acme",
        "ipc_subclass_id": "G21B",
    }
    result = normalize_record(raw, "USPTO")
    assert len(result["abstract"]) == 500


def test_matches_assignee_filter_positive(sample_uspto_raw):
    assert matches_assignee_filter(sample_uspto_raw) is True


def test_matches_assignee_filter_negative():
    assert matches_assignee_filter({"assignee_organization": "Acme Widgets Corp"}) is False


def test_matches_assignee_filter_case_insensitive():
    assert matches_assignee_filter({"assignee_organization": "darpa research labs"}) is True


def test_matches_assignee_filter_wipo_field():
    assert matches_assignee_filter({"PA": "General Atomics"}) is True


def test_matches_assignee_filter_missing_field():
    assert matches_assignee_filter({}) is False


def test_filter_assignees_for_targets_none_keeps_all():
    assert _filter_assignees_for_targets(ASSIGNEES, None, None) == ASSIGNEES


def test_filter_assignees_for_targets_empty_lists_keeps_all():
    assert _filter_assignees_for_targets(ASSIGNEES, [], []) == ASSIGNEES


def test_filter_assignees_for_targets_entity_case_insensitive():
    result = _filter_assignees_for_targets(ASSIGNEES, ["helion energy"], None)
    assert result == ["Helion"]


def test_filter_assignees_for_targets_keyword_case_insensitive():
    result = _filter_assignees_for_targets(ASSIGNEES, None, ["lockheed"])
    assert result == ["Lockheed"]


def test_filter_assignees_for_targets_no_match_returns_empty():
    result = _filter_assignees_for_targets(ASSIGNEES, ["Nonexistent Corp"], ["unrelated"])
    assert result == []


def test_patent_record_matches_target_keywords_no_keywords_returns_true():
    patent = {"title": "Fusion Device"}
    assert _patent_record_matches_target_keywords(patent, None) is True
    assert _patent_record_matches_target_keywords(patent, []) is True


def test_patent_record_matches_target_keywords_title():
    patent = {"title": "Advanced Plasma Confinement"}
    assert _patent_record_matches_target_keywords(patent, ["plasma"]) is True


def test_patent_record_matches_target_keywords_abstract():
    patent = {"abstract": "A novel tokamak design"}
    assert _patent_record_matches_target_keywords(patent, ["tokamak"]) is True


def test_patent_record_matches_target_keywords_assignee():
    patent = {"assignee": "Helion Energy Inc"}
    assert _patent_record_matches_target_keywords(patent, ["helion"]) is True


def test_patent_record_matches_target_keywords_classification():
    patent = {"ipc": "G21B"}
    assert _patent_record_matches_target_keywords(patent, ["g21b"]) is True


def test_patent_record_matches_target_keywords_no_match():
    patent = {"title": "Widget Manufacturing", "abstract": "Standard process"}
    assert _patent_record_matches_target_keywords(patent, ["plasma"]) is False


async def test_run_patent_sentinel_returns_early_when_no_assignees_remain(
    httpx_mock: HTTPXMock, monkeypatch
):
    import modules.patent_sentinel as ps

    monkeypatch.setattr(ps.patent_cfg, "patent_provider", "legacy_patentsview")
    monkeypatch.setattr(ps.patent_cfg, "assignees_of_interest", ASSIGNEES)

    results = await ps.run_patent_sentinel(
        since_days=7,
        dry_run=True,
        target_entities=["Nonexistent Entity"],
        target_keywords=["unrelated"],
    )

    assert results == []
    assert len(httpx_mock.get_requests()) == 0


async def test_fetch_uspto_patents_success(httpx_mock: HTTPXMock, monkeypatch):
    monkeypatch.setattr(patent_sentinel_module.patent_cfg, "patent_provider", "legacy_patentsview")
    httpx_mock.add_response(
        json={
            "patents": [
                {
                    "patent_id": "US123456",
                    "patent_title": "Fusion Device",
                    "patent_date": "2026-01-15",
                    "patent_abstract": "A test patent.",
                    "assignee_organization": "MIT",
                    "ipc_subclass_id": "G21B",
                }
            ]
        }
    )
    result = await fetch_uspto_patents("G21B", since_days=7)
    assert len(result) == 1
    assert result[0]["patent_id"] == "US123456"


async def test_fetch_uspto_patents_empty_response(httpx_mock: HTTPXMock, monkeypatch):
    monkeypatch.setattr(patent_sentinel_module.patent_cfg, "patent_provider", "legacy_patentsview")
    httpx_mock.add_response(json={"patents": []})
    result = await fetch_uspto_patents("G21B", since_days=7)
    assert result == []


async def test_fetch_uspto_patents_uses_configured_base_url(httpx_mock: HTTPXMock, monkeypatch):
    """fetch_uspto_patents must build its POST URL from patentsview_api config."""
    monkeypatch.setattr(patent_sentinel_module.patent_cfg, "patent_provider", "legacy_patentsview")
    monkeypatch.setattr(
        patent_sentinel_module.patent_cfg, "patentsview_api", "https://example.test/api/v1"
    )
    httpx_mock.add_response(json={"patents": []})
    await fetch_uspto_patents("G21B", since_days=7)
    requests = httpx_mock.get_requests()
    assert len(requests) == 1
    assert str(requests[0].url) == "https://example.test/api/v1/patents/query"


async def test_run_patent_sentinel_flagged_patent_has_score_and_provenance(
    httpx_mock: HTTPXMock, tmp_db, tmp_db_path, monkeypatch
):
    import modules.patent_sentinel as ps
    from modules import db as _db

    monkeypatch.setattr(_db, "DB_PATH", tmp_db_path)
    monkeypatch.setattr(ps, "init_db", lambda path: tmp_db)
    monkeypatch.setattr(ps.patent_cfg, "patent_provider", "legacy_patentsview")
    monkeypatch.setattr(ps.patent_cfg, "wipo_api_key", "")
    monkeypatch.setattr(ps.patent_cfg, "classes", ["G21B"])
    monkeypatch.setattr(ps.patent_cfg, "use_playwright_fallback", False)
    monkeypatch.setattr(ps.patent_cfg, "inter_class_delay", 0)

    httpx_mock.add_response(
        method="POST",
        json={
            "patents": [
                {
                    "patent_id": "US999001",
                    "patent_title": "Plasma Confinement System",
                    "patent_date": "2026-04-01",
                    "patent_abstract": "A fusion device.",
                    "assignee_organization": "MIT",
                    "ipc_subclass_id": "G21B",
                }
            ]
        },
    )

    results = await ps.run_patent_sentinel(since_days=7, dry_run=False)

    flagged = [r for r in results if r.get("_flagged")]
    assert len(flagged) == 1
    p = flagged[0]
    assert "score" in p
    assert p["score_label"] in ("low", "medium", "high")
    assert p["provenance"]["source_type"] == "patent_api"
    assert p["provenance"]["source_url"] == "US999001"

    rows = _db.fetch_alerts_by_source("patent_sentinel")
    assert len(rows) == 1
    assert rows[0]["alert"]["id"] == "US999001"


async def test_run_patent_sentinel_dry_run_skips_alert_insert(
    httpx_mock: HTTPXMock, tmp_db, tmp_db_path, monkeypatch
):
    import modules.patent_sentinel as ps
    from modules import db as _db

    monkeypatch.setattr(_db, "DB_PATH", tmp_db_path)
    _db.init_alerts_table()
    monkeypatch.setattr(ps, "init_db", lambda path: tmp_db)
    monkeypatch.setattr(ps.patent_cfg, "patent_provider", "legacy_patentsview")
    monkeypatch.setattr(ps.patent_cfg, "wipo_api_key", "")
    monkeypatch.setattr(ps.patent_cfg, "classes", ["G21B"])
    monkeypatch.setattr(ps.patent_cfg, "use_playwright_fallback", False)
    monkeypatch.setattr(ps.patent_cfg, "inter_class_delay", 0)

    httpx_mock.add_response(
        method="POST",
        json={
            "patents": [
                {
                    "patent_id": "US999002",
                    "patent_title": "Plasma Device",
                    "patent_date": "2026-04-01",
                    "patent_abstract": "Test.",
                    "assignee_organization": "MIT",
                    "ipc_subclass_id": "G21B",
                }
            ]
        },
    )

    results = await ps.run_patent_sentinel(since_days=7, dry_run=True)

    flagged = [r for r in results if r.get("_flagged")]
    assert len(flagged) == 1
    assert "score" in flagged[0]
    rows = _db.fetch_alerts_by_source("patent_sentinel")
    assert rows == []


async def test_fetch_wipo_patents_skipped_without_key(monkeypatch):
    monkeypatch.setattr(patent_sentinel_module.patent_cfg, "wipo_api_key", "")
    result = await fetch_wipo_patents("G21B", since_days=7)
    assert result == []


async def test_fetch_wipo_patents_single_page(httpx_mock: HTTPXMock, monkeypatch):
    monkeypatch.setattr(patent_sentinel_module.patent_cfg, "wipo_api_key", "testkey")
    docs = [
        {"PN": f"WO{i}", "TI": "Title", "PA": "Assignee", "IPC": "G21B", "PD": "20260101"}
        for i in range(3)
    ]
    httpx_mock.add_response(
        method="GET",
        match_headers={"Authorization": "Bearer testkey"},
        json={"docs": docs, "numFound": 3},
    )
    result = await fetch_wipo_patents("G21B", since_days=7)
    assert len(result) == 3


async def test_fetch_wipo_patents_paginates(httpx_mock: HTTPXMock, monkeypatch):
    monkeypatch.setattr(patent_sentinel_module.patent_cfg, "wipo_api_key", "testkey")
    monkeypatch.setattr(patent_sentinel_module.patent_cfg, "wipo_page_size", 2)
    page1 = [{"PN": "WO1"}, {"PN": "WO2"}]
    page2 = [{"PN": "WO3"}]
    httpx_mock.add_response(
        method="GET",
        match_headers={"Authorization": "Bearer testkey"},
        json={"docs": page1, "numFound": 3},
    )
    httpx_mock.add_response(
        method="GET",
        match_headers={"Authorization": "Bearer testkey"},
        json={"docs": page2, "numFound": 3},
    )
    result = await fetch_wipo_patents("G21B", since_days=7)
    assert len(result) == 3
    pns = [d["PN"] for d in result]
    assert "WO1" in pns and "WO3" in pns


# ---------------------------------------------------------------------------
# Provider config tests (new — Packet 17)
# ---------------------------------------------------------------------------


def test_default_patent_provider_is_disabled():
    """PatentConfig default provider is 'disabled' when env var is unset."""
    import os

    if "PATENT_PROVIDER" not in os.environ:
        from config import PatentConfig

        fresh = PatentConfig()
        assert fresh.patent_provider == "disabled"


def test_validate_patent_provider_normalizes_case():
    assert _validate_patent_provider("disabled") == "disabled"
    assert _validate_patent_provider("DISABLED") == "disabled"
    assert _validate_patent_provider("Legacy_Patentsview") == "legacy_patentsview"
    assert _validate_patent_provider("ODP") == "odp"


def test_validate_patent_provider_rejects_unknown():
    import pytest

    with pytest.raises(ValueError, match="Unknown patent provider"):
        _validate_patent_provider("patentsview_v2")


async def test_fetch_uspto_patents_disabled_returns_empty_no_network(
    httpx_mock: HTTPXMock, monkeypatch
):
    """Disabled provider must return [] without touching the network."""
    monkeypatch.setattr(patent_sentinel_module.patent_cfg, "patent_provider", "disabled")
    result = await fetch_uspto_patents("G21B", since_days=7)
    assert result == []
    assert len(httpx_mock.get_requests()) == 0


async def test_fetch_legacy_patentsview_payload_shape(httpx_mock: HTTPXMock, monkeypatch):
    """Legacy provider must send the original PatentsView JSON payload shape."""
    import json

    monkeypatch.setattr(patent_sentinel_module.patent_cfg, "patent_provider", "legacy_patentsview")
    httpx_mock.add_response(json={"patents": []})
    await fetch_uspto_patents("G21B", since_days=7)
    req = httpx_mock.get_requests()[0]
    body = json.loads(req.content)
    assert "q" in body
    assert "f" in body
    assert "o" in body
    assert any("patent_id" in str(f) for f in body["f"])


async def test_run_patent_sentinel_odp_provider_returns_empty_no_network(
    httpx_mock: HTTPXMock, monkeypatch
):
    """ODP provider is recognized but not implemented — must return [] without network calls."""
    import modules.patent_sentinel as ps

    monkeypatch.setattr(ps.patent_cfg, "patent_provider", "odp")
    results = await ps.run_patent_sentinel(since_days=7, dry_run=True)
    assert results == []
    assert len(httpx_mock.get_requests()) == 0


async def test_run_patent_sentinel_disabled_returns_empty_no_exception(monkeypatch):
    """Disabled provider exits cleanly without raising, allowing pipeline to continue."""
    import modules.patent_sentinel as ps

    monkeypatch.setattr(ps.patent_cfg, "patent_provider", "disabled")
    results = await ps.run_patent_sentinel(since_days=7, dry_run=True)
    assert results == []


async def test_target_filtering_applies_before_legacy_fetch(httpx_mock: HTTPXMock, monkeypatch):
    """When target filter excludes all assignees, no network call should be made."""
    import modules.patent_sentinel as ps

    monkeypatch.setattr(ps.patent_cfg, "patent_provider", "legacy_patentsview")
    monkeypatch.setattr(ps.patent_cfg, "assignees_of_interest", ASSIGNEES)
    results = await ps.run_patent_sentinel(
        since_days=7,
        dry_run=True,
        target_entities=["Nonexistent Corp"],
        target_keywords=["unrelated_keyword"],
    )
    assert results == []
    assert len(httpx_mock.get_requests()) == 0


async def test_check_patent_provider_health_disabled(monkeypatch):
    """Health check for disabled provider returns expected shape without network calls."""
    monkeypatch.setattr(patent_sentinel_module.patent_cfg, "patent_provider", "disabled")
    result = await check_patent_provider_health()
    assert result["provider"] == "disabled"
    assert result["ok"] is False
    assert result["enabled"] is False
    assert result["status"] == "disabled"
    assert result["base_url"] == ""


async def test_check_patent_provider_health_does_not_expose_api_key(
    httpx_mock: HTTPXMock, monkeypatch
):
    """Health check output must not contain the configured API key."""
    monkeypatch.setattr(patent_sentinel_module.patent_cfg, "patent_provider", "legacy_patentsview")
    monkeypatch.setattr(patent_sentinel_module.patent_cfg, "patentsview_api_key", "supersecret123")
    httpx_mock.add_response(status_code=200)
    result = await check_patent_provider_health()
    assert "supersecret123" not in str(result)
