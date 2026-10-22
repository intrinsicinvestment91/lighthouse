"""Unit tests for modules/sec_sentinel.py"""

import sqlite3
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest

import modules.db as db
import modules.sec_sentinel as sec_sentinel_module
from modules.alerts import AlertRecord
from modules.sec_sentinel import (
    _accession_to_cik,
    _fetch_company_name,
    _filing_url,
    _filter_sec_entities_for_targets,
    _name_matches_patterns,
    build_sec_alert_dedup_key,
    fetch_edgar_filings,
    fetch_filings_by_cik,
    is_material,
    run_sec_sentinel,
)

try:
    from pytest_httpx import HTTPXMock
except ImportError:
    HTTPXMock = None


@pytest.fixture(autouse=True)
def _declared_sec_user_agent(monkeypatch):
    monkeypatch.setattr(
        sec_sentinel_module.sec_cfg, "user_agent", "Lighthouse Test admin@example.invalid"
    )


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def test_accession_to_cik_strips_padding():
    assert _accession_to_cik("0001234567-26-000123") == "1234567"


def test_accession_to_cik_small_cik():
    assert _accession_to_cik("0000936340-26-000001") == "936340"


def test_filing_url_format():
    url = _filing_url("0001234567-26-000123", "1234567")
    assert "1234567" in url
    assert "000123456726000123" in url
    assert url.endswith("0001234567-26-000123-index.htm")
    assert url.startswith("https://www.sec.gov/Archives/edgar/data/")


def test_is_material_flags_known_items():
    assert is_material("1.01,7.01", ["1.01", "5.02"]) is True
    assert is_material("5.02", ["1.01", "5.02"]) is True


def test_is_material_ignores_non_material():
    assert is_material("7.01,9.01", ["1.01", "5.02"]) is False


def test_is_material_empty_and_none():
    assert is_material("", ["1.01"]) is False
    assert is_material(None, ["1.01"]) is False


def test_is_material_accepts_list_items():
    assert is_material(["1.01", "7.01"], ["1.01", "5.02"]) is True
    assert is_material(["7.01", "9.01"], ["1.01", "5.02"]) is False


def test_is_material_accepts_none():
    assert is_material(None, ["1.01", "5.02"]) is False


def test_is_material_accepts_unexpected_scalar():
    assert is_material(1.01, ["1.01", "5.02"]) is True
    assert is_material(7, ["1.01", "5.02"]) is False


def test_name_matches_patterns_case_insensitive():
    assert _name_matches_patterns("LOCKHEED MARTIN CORP", ["Lockheed"]) is True
    assert _name_matches_patterns("LOCKHEED MARTIN CORP", ["lockheed martin"]) is True
    assert _name_matches_patterns("MCKESSON CORP", ["MIT"]) is False


def test_name_matches_patterns_multiple_patterns():
    assert _name_matches_patterns("MIT LINCOLN LAB", ["MIT", "Massachusetts"]) is True
    assert _name_matches_patterns("GENERAL MOTORS", ["MIT", "Massachusetts"]) is False


def test_build_sec_alert_dedup_key_same_inputs_same_key():
    key_a = build_sec_alert_dedup_key(
        "0000936395-26-000042",
        "LOCKHEED MARTIN CORP",
        "sec_8k",
    )
    key_b = build_sec_alert_dedup_key(
        "0000936395-26-000042",
        "LOCKHEED MARTIN CORP",
        "sec_8k",
    )
    assert key_a == key_b


def test_build_sec_alert_dedup_key_ignores_retrieved_at_in_alert_body():
    key = build_sec_alert_dedup_key("0000936395-26-000042", "Lockheed", "sec_8k")
    assert key == build_sec_alert_dedup_key("0000936395-26-000042", "Lockheed", "sec_8k")
    assert "retrieved_at" not in key


def test_build_sec_alert_dedup_key_normalizes_case_and_whitespace():
    key = build_sec_alert_dedup_key(
        "  0000936395-26-000042  ",
        "  LOCKHEED MARTIN CORP  ",
        "  SEC_8K  ",
    )
    assert key == "sec:0000936395-26-000042:lockheed martin corp:sec_8k"


def test_build_sec_alert_dedup_key_different_accession_different_key():
    key_a = build_sec_alert_dedup_key("0000936395-26-000042", "Lockheed", "sec_8k")
    key_b = build_sec_alert_dedup_key("0000936395-26-000043", "Lockheed", "sec_8k")
    assert key_a != key_b


# ---------------------------------------------------------------------------
# fetch_edgar_filings
# ---------------------------------------------------------------------------


async def test_fetch_edgar_filings_returns_hits(httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        json={
            "hits": {
                "total": {"value": 1},
                "hits": [
                    {
                        "_id": "0001234567-26-000123",
                        "_source": {
                            "entity_name": "LOCKHEED MARTIN CORP",
                            "form_type": "8-K",
                            "file_date": "2026-04-15",
                            "items": "1.01,7.01",
                        },
                    }
                ],
            }
        }
    )
    import httpx as _httpx

    async with _httpx.AsyncClient() as client:
        hits = await fetch_edgar_filings("Lockheed", "2026-04-01", "2026-04-26", client)

    assert len(hits) == 1
    assert hits[0]["_id"] == "0001234567-26-000123"
    assert hits[0]["_source"]["entity_name"] == "LOCKHEED MARTIN CORP"


async def test_fetch_edgar_filings_empty_response(httpx_mock: HTTPXMock):
    httpx_mock.add_response(json={"hits": {"total": {"value": 0}, "hits": []}})
    import httpx as _httpx

    async with _httpx.AsyncClient() as client:
        hits = await fetch_edgar_filings("UnknownCorp", "2026-04-01", "2026-04-26", client)

    assert hits == []


# ---------------------------------------------------------------------------
# fetch_filings_by_cik
# ---------------------------------------------------------------------------


def _submissions_response(
    *,
    recent_date: str | None = None,
    older_date: str = "2026-03-01",
) -> dict:
    """Build SEC submissions JSON; default recent date stays inside rolling `since_days` windows."""
    if recent_date is None:
        recent_date = (datetime.now(UTC).date() - timedelta(days=5)).isoformat()
    return {
        "cik": "0000936395",
        "name": "LOCKHEED MARTIN CORP",
        "filings": {
            "recent": {
                "accessionNumber": ["0000936395-26-000042", "0000936395-26-000010"],
                "filingDate": [recent_date, older_date],
                "form": ["8-K", "8-K"],
                "items": ["1.01,7.01", "5.02"],
            }
        },
    }


async def test_fetch_filings_by_cik_returns_matching_filings(httpx_mock: HTTPXMock):
    httpx_mock.add_response(json=_submissions_response(recent_date="2026-04-20"))
    import httpx as _httpx

    async with _httpx.AsyncClient() as client:
        hits = await fetch_filings_by_cik(
            "936395", "Lockheed Martin", "2026-04-01", "2026-04-30", client
        )

    assert len(hits) == 1
    assert hits[0]["_id"] == "0000936395-26-000042"
    assert hits[0]["_source"]["entity_name"] == "LOCKHEED MARTIN CORP"
    assert hits[0]["_source"]["items"] == "1.01,7.01"


async def test_fetch_filings_by_cik_filters_by_date(httpx_mock: HTTPXMock):
    httpx_mock.add_response(
        json=_submissions_response(recent_date="2026-04-15", older_date="2026-03-10")
    )
    import httpx as _httpx

    async with _httpx.AsyncClient() as client:
        hits = await fetch_filings_by_cik(
            "936395", "Lockheed Martin", "2026-03-01", "2026-03-31", client
        )

    assert len(hits) == 1
    assert hits[0]["_id"] == "0000936395-26-000010"


async def test_fetch_filings_by_cik_uses_canonical_name(httpx_mock: HTTPXMock):
    httpx_mock.add_response(json=_submissions_response(recent_date="2026-04-20"))
    import httpx as _httpx

    async with _httpx.AsyncClient() as client:
        hits = await fetch_filings_by_cik(
            "936395", "Lockheed Martin", "2026-04-01", "2026-04-30", client
        )

    # Uses SEC-registered name, not the search term passed in
    assert hits[0]["_source"]["entity_name"] == "LOCKHEED MARTIN CORP"


async def test_fetch_filings_by_cik_filters_non_8k_forms(httpx_mock: HTTPXMock):
    response = {
        "cik": "0000936395",
        "name": "LOCKHEED MARTIN CORP",
        "filings": {
            "recent": {
                "accessionNumber": ["0000936395-26-000042", "0000936395-26-000011"],
                "filingDate": ["2026-04-15", "2026-04-10"],
                "form": ["8-K", "10-Q"],
                "items": ["1.01", ""],
            }
        },
    }
    httpx_mock.add_response(json=response)
    import httpx as _httpx

    async with _httpx.AsyncClient() as client:
        hits = await fetch_filings_by_cik(
            "936395", "Lockheed Martin", "2026-04-01", "2026-04-30", client
        )

    assert len(hits) == 1
    assert hits[0]["_id"] == "0000936395-26-000042"


# ---------------------------------------------------------------------------
# _fetch_company_name
# ---------------------------------------------------------------------------


async def test_fetch_company_name_returns_name(httpx_mock: HTTPXMock):
    httpx_mock.add_response(json={"cik": "0000936395", "name": "LOCKHEED MARTIN CORP"})
    import httpx as _httpx

    async with _httpx.AsyncClient() as client:
        name = await _fetch_company_name("936395", client)

    assert name == "LOCKHEED MARTIN CORP"


async def test_fetch_company_name_returns_none_on_error(httpx_mock: HTTPXMock):
    httpx_mock.add_response(status_code=404)
    import httpx as _httpx

    async with _httpx.AsyncClient() as client:
        name = await _fetch_company_name("999999", client)

    assert name is None


# ---------------------------------------------------------------------------
# run_sec_sentinel — shared test fixtures
# ---------------------------------------------------------------------------

_LOCKHEED_ENTITY = {
    "name": "Lockheed Martin",
    "sec_cik": "0000936395",
    "assignee_patterns": ["Lockheed Martin", "Lockheed"],
    "facilities": ["DARPA HQ"],
}

_HELION_ENTITY = {
    "name": "Helion Energy",
    "sec_cik": "0001615940",
    "assignee_patterns": ["Helion"],
    "facilities": ["Helion Everett"],
}


def test_filter_sec_entities_for_targets_none_keeps_all():
    entities = [_LOCKHEED_ENTITY, _HELION_ENTITY]
    assert _filter_sec_entities_for_targets(entities, None) == entities
    assert _filter_sec_entities_for_targets(entities, []) == entities


def test_filter_sec_entities_for_targets_matches_name_case_insensitive():
    filtered = _filter_sec_entities_for_targets(
        [_LOCKHEED_ENTITY, _HELION_ENTITY],
        ["lockheed martin"],
    )
    assert filtered == [_LOCKHEED_ENTITY]


def test_filter_sec_entities_for_targets_matches_alias_or_pattern():
    filtered = _filter_sec_entities_for_targets(
        [_LOCKHEED_ENTITY, _HELION_ENTITY],
        ["Lockheed"],
    )
    assert filtered == [_LOCKHEED_ENTITY]


def test_filter_sec_entities_for_targets_unknown_returns_empty():
    filtered = _filter_sec_entities_for_targets(
        [_LOCKHEED_ENTITY, _HELION_ENTITY],
        ["Unknown Corp"],
    )
    assert filtered == []


def _alerts_row_count_or_zero() -> int:
    try:
        return len(db.fetchall("SELECT * FROM alerts"))
    except sqlite3.OperationalError as exc:
        if "no such table: alerts" in str(exc):
            return 0
        raise


# ---------------------------------------------------------------------------
# run_sec_sentinel — CIK path
# ---------------------------------------------------------------------------


async def test_run_sec_sentinel_flags_material_filing(httpx_mock: HTTPXMock, tmp_db, monkeypatch):
    monkeypatch.setattr(sec_sentinel_module, "_load_sec_entities", lambda: [_LOCKHEED_ENTITY])
    monkeypatch.setattr(sec_sentinel_module.patent_cfg, "assignees_of_interest", [])
    monkeypatch.setattr(sec_sentinel_module.sec_cfg, "material_items", ["1.01"])
    httpx_mock.add_response(json=_submissions_response())

    alerts = await run_sec_sentinel(tmp_db, since_days=30, dry_run=False)

    assert len(alerts) == 1
    assert alerts[0]["company"] == "LOCKHEED MARTIN CORP"
    assert alerts[0]["items"] == "1.01,7.01"
    assert "score" in alerts[0]
    assert alerts[0]["score_label"] in {"low", "medium", "high"}
    assert "provenance" in alerts[0]
    assert alerts[0]["provenance"]["source_type"] == "sec_sentinel"
    record = AlertRecord(
        source="sec_sentinel",
        alert_type=alerts[0].get("form_type") or "material_filing",
        entity=alerts[0].get("company"),
        score=alerts[0].get("score"),
        score_label=alerts[0].get("score_label"),
        provenance=alerts[0].get("provenance"),
        payload=alerts[0],
    )
    record.validate()

    row = tmp_db.execute(
        "SELECT company FROM sec_filings WHERE accession_no = '0000936395-26-000042'"
    ).fetchone()
    assert row is not None
    assert row[0] == "LOCKHEED MARTIN CORP"


async def test_run_sec_sentinel_non_material_not_alerted(
    httpx_mock: HTTPXMock, tmp_db, monkeypatch
):
    monkeypatch.setattr(sec_sentinel_module, "_load_sec_entities", lambda: [_LOCKHEED_ENTITY])
    monkeypatch.setattr(sec_sentinel_module.patent_cfg, "assignees_of_interest", [])
    monkeypatch.setattr(sec_sentinel_module.sec_cfg, "material_items", ["2.01"])
    httpx_mock.add_response(json=_submissions_response())

    alerts = await run_sec_sentinel(tmp_db, since_days=30, dry_run=False)
    assert len(alerts) == 0

    # Still written to DB even if not alerted
    row = tmp_db.execute(
        "SELECT company FROM sec_filings WHERE accession_no = '0000936395-26-000042'"
    ).fetchone()
    assert row is not None


async def test_run_sec_sentinel_deduplicates(httpx_mock: HTTPXMock, tmp_db, monkeypatch):
    monkeypatch.setattr(sec_sentinel_module, "_load_sec_entities", lambda: [_LOCKHEED_ENTITY])
    monkeypatch.setattr(sec_sentinel_module.patent_cfg, "assignees_of_interest", [])
    httpx_mock.add_response(json=_submissions_response())
    httpx_mock.add_response(json=_submissions_response())

    await run_sec_sentinel(tmp_db, since_days=30, dry_run=False)
    alerts2 = await run_sec_sentinel(tmp_db, since_days=30, dry_run=False)

    assert len(alerts2) == 0


async def test_run_sec_sentinel_accepts_target_entities(httpx_mock: HTTPXMock, tmp_db, monkeypatch):
    queried: list[str] = []

    async def track_cik(cik, entity_name, since, until, client):
        queried.append(entity_name)
        return []

    monkeypatch.setattr(
        sec_sentinel_module,
        "_load_sec_entities",
        lambda: [_LOCKHEED_ENTITY, _HELION_ENTITY],
    )
    monkeypatch.setattr(sec_sentinel_module.patent_cfg, "assignees_of_interest", [])
    monkeypatch.setattr(sec_sentinel_module, "fetch_filings_by_cik", track_cik)

    await run_sec_sentinel(
        tmp_db,
        since_days=30,
        dry_run=True,
        target_entities=["Helion Energy"],
    )

    assert queried == ["Helion Energy"]


async def test_run_sec_sentinel_dry_run_skips_db(httpx_mock: HTTPXMock, tmp_db, monkeypatch):
    monkeypatch.setattr(sec_sentinel_module, "_load_sec_entities", lambda: [_LOCKHEED_ENTITY])
    monkeypatch.setattr(sec_sentinel_module.patent_cfg, "assignees_of_interest", [])
    httpx_mock.add_response(json=_submissions_response())

    await run_sec_sentinel(tmp_db, since_days=30, dry_run=True)

    count = tmp_db.execute("SELECT COUNT(*) FROM sec_filings").fetchone()[0]
    assert count == 0


async def test_run_sec_sentinel_persists_alerts_table(
    httpx_mock: HTTPXMock, tmp_db, tmp_db_path, monkeypatch
):
    monkeypatch.setattr(sec_sentinel_module, "_load_sec_entities", lambda: [_LOCKHEED_ENTITY])
    monkeypatch.setattr(sec_sentinel_module.patent_cfg, "assignees_of_interest", [])
    monkeypatch.setattr(sec_sentinel_module.sec_cfg, "material_items", ["1.01"])
    monkeypatch.setattr(db, "DB_PATH", tmp_db_path)
    httpx_mock.add_response(json=_submissions_response())

    alerts = await run_sec_sentinel(tmp_db, since_days=30, dry_run=False)

    rows = db.fetchall("SELECT source, alert_json FROM alerts")
    assert alerts
    assert len(rows) >= 1
    assert rows[0]["source"] == "sec_sentinel"


async def test_run_sec_sentinel_dry_run_does_not_persist_alerts(
    httpx_mock: HTTPXMock, tmp_db, tmp_db_path, monkeypatch
):
    monkeypatch.setattr(sec_sentinel_module, "_load_sec_entities", lambda: [_LOCKHEED_ENTITY])
    monkeypatch.setattr(sec_sentinel_module.patent_cfg, "assignees_of_interest", [])
    monkeypatch.setattr(sec_sentinel_module.sec_cfg, "material_items", ["1.01"])
    monkeypatch.setattr(db, "DB_PATH", tmp_db_path)
    httpx_mock.add_response(json=_submissions_response())

    alerts = await run_sec_sentinel(tmp_db, since_days=30, dry_run=True)

    assert alerts
    assert _alerts_row_count_or_zero() == 0


async def test_run_sec_sentinel_cik_query_failure_continues(tmp_db, monkeypatch):
    monkeypatch.setattr(sec_sentinel_module, "_load_sec_entities", lambda: [_LOCKHEED_ENTITY])
    monkeypatch.setattr(sec_sentinel_module.patent_cfg, "assignees_of_interest", [])

    with patch(
        "modules.sec_sentinel.fetch_filings_by_cik",
        new=AsyncMock(side_effect=Exception("connection refused")),
    ):
        result = await run_sec_sentinel(tmp_db, dry_run=True)

    assert isinstance(result, list)
    assert result == []


# ---------------------------------------------------------------------------
# run_sec_sentinel — EFTS fallback path (entities without CIK)
# ---------------------------------------------------------------------------

_NO_CIK_ENTITY_HIT = {
    "_id": "0000936340-26-000042",
    "_source": {
        "entity_name": "LOCKHEED MARTIN CORP",
        "form_type": "8-K",
        "file_date": "2026-04-15",
        "items": "1.01,7.01",
    },
}


async def test_run_sec_sentinel_efts_fallback_accepts_matching_filer(
    httpx_mock: HTTPXMock, tmp_db, monkeypatch
):
    """EFTS fallback keeps hits where entity_name matches the searched company."""
    monkeypatch.setattr(sec_sentinel_module, "_load_sec_entities", lambda: [])
    monkeypatch.setattr(sec_sentinel_module.patent_cfg, "assignees_of_interest", ["Lockheed"])
    monkeypatch.setattr(sec_sentinel_module.sec_cfg, "material_items", ["1.01"])
    httpx_mock.add_response(json={"hits": {"hits": [_NO_CIK_ENTITY_HIT]}})

    alerts = await run_sec_sentinel(tmp_db, since_days=7, dry_run=False)

    assert len(alerts) == 1
    assert alerts[0]["company"] == "LOCKHEED MARTIN CORP"


async def test_run_sec_sentinel_efts_fallback_rejects_mismatched_filer(
    httpx_mock: HTTPXMock, tmp_db, monkeypatch
):
    """EFTS fallback drops hits where entity_name doesn't match the searched company."""
    monkeypatch.setattr(sec_sentinel_module, "_load_sec_entities", lambda: [])
    monkeypatch.setattr(sec_sentinel_module.patent_cfg, "assignees_of_interest", ["MIT"])
    monkeypatch.setattr(sec_sentinel_module.sec_cfg, "material_items", ["1.01"])
    # Simulate a McKesson filing coming back for "MIT" text search
    mckesson_hit = {
        "_id": "0000927653-26-000053",
        "_source": {
            "entity_name": "MCKESSON CORP",
            "form_type": "8-K",
            "file_date": "2026-04-28",
            "items": "1.01,1.02,2.03,9.01",
        },
    }
    httpx_mock.add_response(json={"hits": {"hits": [mckesson_hit]}})

    alerts = await run_sec_sentinel(tmp_db, since_days=7, dry_run=False)

    assert len(alerts) == 0


async def test_run_sec_sentinel_efts_fallback_fetches_name_when_missing(
    httpx_mock: HTTPXMock, tmp_db, monkeypatch
):
    """When entity_name is absent from EFTS hit, fetch it from submissions API."""
    monkeypatch.setattr(sec_sentinel_module, "_load_sec_entities", lambda: [])
    monkeypatch.setattr(sec_sentinel_module.patent_cfg, "assignees_of_interest", ["Lockheed"])
    monkeypatch.setattr(sec_sentinel_module.sec_cfg, "material_items", ["1.01"])
    hit_no_name = {
        "_id": "0000936340-26-000042",
        "_source": {"form_type": "8-K", "file_date": "2026-04-15", "items": "1.01,7.01"},
    }
    # EFTS response (no entity_name), then submissions API lookup
    httpx_mock.add_response(json={"hits": {"hits": [hit_no_name]}})
    httpx_mock.add_response(json={"name": "LOCKHEED MARTIN CORP"})

    alerts = await run_sec_sentinel(tmp_db, since_days=7, dry_run=False)

    assert len(alerts) == 1
    assert alerts[0]["company"] == "LOCKHEED MARTIN CORP"


async def test_run_sec_sentinel_efts_fallback_skips_when_name_unresolvable(
    httpx_mock: HTTPXMock, tmp_db, monkeypatch
):
    """Hits with no entity_name and failed company lookup are dropped."""
    monkeypatch.setattr(sec_sentinel_module, "_load_sec_entities", lambda: [])
    monkeypatch.setattr(sec_sentinel_module.patent_cfg, "assignees_of_interest", ["MIT"])
    hit_no_name = {
        "_id": "0000927653-26-000053",
        "_source": {"form_type": "8-K", "file_date": "2026-04-28", "items": "1.01"},
    }
    httpx_mock.add_response(json={"hits": {"hits": [hit_no_name]}})
    httpx_mock.add_response(status_code=404)  # submissions API fails

    alerts = await run_sec_sentinel(tmp_db, since_days=7, dry_run=False)

    assert len(alerts) == 0


async def test_run_sec_sentinel_api_failure_continues(tmp_db, monkeypatch):
    monkeypatch.setattr(sec_sentinel_module, "_load_sec_entities", lambda: [])
    monkeypatch.setattr(sec_sentinel_module.patent_cfg, "assignees_of_interest", ["Lockheed"])

    with patch(
        "modules.sec_sentinel.fetch_edgar_filings",
        new=AsyncMock(side_effect=Exception("connection refused")),
    ):
        result = await run_sec_sentinel(tmp_db, dry_run=True)

    assert isinstance(result, list)
    assert result == []


def test_sec_user_agent_reads_env(monkeypatch):
    monkeypatch.setenv("SEC_USER_AGENT", "Example Org admin@example.org")
    from config import SecConfig

    assert SecConfig().user_agent == "Example Org admin@example.org"


def test_sec_user_agent_has_no_default(monkeypatch):
    monkeypatch.delenv("SEC_USER_AGENT", raising=False)
    from config import SecConfig

    assert SecConfig().user_agent == ""


async def test_run_sec_sentinel_refuses_without_user_agent(monkeypatch, tmp_db, httpx_mock):
    monkeypatch.setattr(sec_sentinel_module.sec_cfg, "user_agent", "")
    with pytest.raises(sec_sentinel_module.SecUserAgentNotConfigured):
        await run_sec_sentinel(tmp_db, since_days=30, dry_run=False)
    assert httpx_mock.get_requests() == []
