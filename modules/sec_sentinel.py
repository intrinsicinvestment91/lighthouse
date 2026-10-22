"""
Module 9 — SEC EDGAR Filing Sentinel.

Monitors EDGAR for 8-K material-event filings from watched entities.
For entities with a known CIK (all entries in entities.json), queries the
EDGAR submissions API directly — this is precise and cannot produce
text-match false positives. For entities without a CIK, falls back to EFTS
full-text search with a filer-verification gate.

APIs used (no auth):
  data.sec.gov/submissions/CIK{padded}.json  — CIK-based filing queries
  efts.sec.gov/LATEST/search-index           — EFTS full-text fallback
  www.sec.gov/Archives/edgar/data/           — filing index pages
"""

import json
import logging
import sqlite3
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx

import config
from modules import db
from modules.alerts import ALERT_SCHEMA_VERSION
from modules.provenance import ProvenanceRecord
from modules.proxy_rotator import ProxyRotator, make_httpx_client
from modules.retry import http_retry
from modules.scoring import AlertScore
from modules.state_db import is_seen, mark_seen

logger = logging.getLogger(__name__)


class _ConfigProxy:
    """Delegate attribute access to the current runtime config object."""

    def __init__(self, getter):
        self._getter = getter

    def __getattr__(self, name: str):
        return getattr(self._getter(), name)

    def __setattr__(self, name: str, value):
        if name == "_getter":
            super().__setattr__(name, value)
            return
        setattr(self._getter(), name, value)


# Backward-compatible aliases for tests patching module-level config directly.
sec_cfg = _ConfigProxy(config.get_sec_cfg)
patent_cfg = _ConfigProxy(config.get_patent_cfg)


class SecUserAgentNotConfigured(RuntimeError):
    """SEC EDGAR requires a declared User-Agent with a contact email; none is configured."""


def _sec_user_agent() -> str:
    """Return the configured SEC User-Agent, or refuse (no request is made without one)."""
    ua = (config.get_sec_cfg().user_agent or "").strip()
    if not ua or "@" not in ua:
        raise SecUserAgentNotConfigured(
            "SEC_USER_AGENT is not set or has no contact email: SEC EDGAR asks "
            "automated clients to declare a User-Agent with an organization name and "
            "contact email (e.g. "
            "'Example Org admin@example.org'). SEC sentinel refused; no requests were sent."
        )
    return ua


def _accession_to_cik(accession_no: str) -> str:
    """Extract CIK (no leading zeros) from accession number '0001234567-26-000123'."""
    return str(int(accession_no.split("-")[0]))


def _filing_url(accession_no: str, cik: str) -> str:
    """Build EDGAR filing index URL from accession number and CIK."""
    no_dashes = accession_no.replace("-", "")
    return f"https://www.sec.gov/Archives/edgar/data/{cik}/{no_dashes}/{accession_no}-index.htm"


def is_material(items: str | list[str] | None, material_items: list[str]) -> bool:
    """Return True if any material 8-K item number appears in the filing."""
    if items is None:
        return False

    if isinstance(items, str):
        filing_items = {i.strip() for i in items.split(",") if i.strip()}
    elif isinstance(items, list):
        filing_items = {str(i).strip() for i in items if str(i).strip()}
    else:
        value = str(items).strip()
        filing_items = {value} if value else set()

    return bool(filing_items.intersection(set(material_items)))


def _name_matches_patterns(name: str, patterns: list[str]) -> bool:
    """Return True if the canonical company name contains any of the entity patterns."""
    lower = name.lower()
    return any(p.lower() in lower for p in patterns)


def _entity_matches_target(entity: dict, target: str) -> bool:
    """Return True if a target string matches an SEC entity name or assignee pattern."""
    target_lower = target.strip().lower()
    if not target_lower:
        return False

    name = entity.get("name") or ""
    if name.lower() == target_lower:
        return True
    if _name_matches_patterns(name, [target]):
        return True
    if _name_matches_patterns(target, entity.get("assignee_patterns", [])):
        return True

    for pattern in entity.get("assignee_patterns", []):
        pattern_lower = pattern.lower()
        if (
            pattern_lower == target_lower
            or target_lower in pattern_lower
            or pattern_lower in target_lower
        ):
            return True

    return False


def _filter_sec_entities_for_targets(
    sec_entities: list[dict],
    target_entities: list[str] | None,
) -> list[dict]:
    """Return SEC entity records narrowed to target_entities when provided."""
    if not target_entities:
        return sec_entities

    filtered = [
        entity
        for entity in sec_entities
        if any(_entity_matches_target(entity, target) for target in target_entities)
    ]
    logger.info(
        "[SEC] Target filter active: %d of %d entities selected",
        len(filtered),
        len(sec_entities),
    )
    return filtered


def _filter_efts_companies_for_targets(
    companies: list[str],
    target_entities: list[str],
) -> list[str]:
    """Return EFTS fallback companies narrowed to target_entities."""
    return [
        company
        for company in companies
        if any(
            company.lower() == target.lower()
            or _name_matches_patterns(company, [target])
            or _name_matches_patterns(target, [company])
            for target in target_entities
        )
    ]


def _load_sec_entities() -> list[dict]:
    """Load entity definitions from the correlator entities seed."""
    path = Path(config.get_correlator_cfg().entities_path)
    if not path.exists():
        logger.warning("[SEC] entities seed missing at %s — CIK-based queries unavailable", path)
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.error("[SEC] Failed loading entities seed: %s", exc)
        return []


def _attach_alert_score(alert: dict, *, is_material: bool) -> None:
    score = AlertScore(
        source_reliability=0.9,
        keyword_strength=1.0 if is_material else 0.5,
        recency=1.0,
        uniqueness=0.7,
        watchlist_match=1.0 if alert.get("company") else 0.0,
    )

    alert["score"] = round(score.total(), 3)
    alert["score_label"] = score.label()
    alert["alert_type"] = "sec_8k"
    alert["schema_version"] = ALERT_SCHEMA_VERSION


def build_sec_alert_dedup_key(
    accession_no: str,
    entity: str,
    alert_type: str,
) -> str:
    """Return a stable dedup identity for SEC material-filing alerts."""
    accession = accession_no.strip().lower()
    entity_norm = entity.strip().lower()
    alert_type_norm = alert_type.strip().lower()
    return f"sec:{accession}:{entity_norm}:{alert_type_norm}"


def _attach_alert_provenance(alert: dict) -> None:
    provenance = ProvenanceRecord(
        source_type="sec_sentinel",
        source_url=alert.get("url") or alert.get("filing_url"),
        metadata={
            "company": alert.get("company"),
            "form_type": alert.get("form_type"),
            "filing_date": alert.get("filing_date") or alert.get("filed_at"),
            "metric": "sec_material_filing",
        },
    )
    alert["provenance"] = provenance.to_dict()


def _process_hits(hits: list[dict], conn: sqlite3.Connection, dry_run: bool) -> list[dict]:
    """Normalize filing hits, persist to DB, and return alert dicts for material filings."""
    alerts: list[dict] = []
    for hit in hits:
        accession_no = hit.get("_id", "")
        if not accession_no:
            continue

        seen_key = f"sec:{accession_no}"
        if is_seen(conn, seen_key):
            continue

        source = hit.get("_source", {})
        entity_name = source.get("entity_name", "")
        form_type = source.get("form_type", "8-K")
        filed_at = source.get("file_date", "")
        raw_items = source.get("items", "")
        if isinstance(raw_items, str):
            items_str = raw_items
        elif isinstance(raw_items, list):
            items_str = ",".join(str(i).strip() for i in raw_items if str(i).strip())
        elif raw_items is None:
            items_str = ""
        else:
            items_str = str(raw_items).strip()

        cik = _accession_to_cik(accession_no)
        url = _filing_url(accession_no, cik)
        description = f"8-K items: {items_str}" if items_str else form_type
        flagged = is_material(raw_items, config.get_sec_cfg().material_items)

        if not dry_run:
            conn.execute(
                """
                INSERT OR IGNORE INTO sec_filings
                    (accession_no, cik, company, form_type, filed_at,
                     items, description, url)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (accession_no, cik, entity_name, form_type, filed_at, items_str, description, url),
            )
            conn.commit()
            mark_seen(conn, seen_key)

        if flagged:
            alert = {
                "accession_no": accession_no,
                "cik": cik,
                "company": entity_name,
                "form_type": form_type,
                "filed_at": filed_at,
                "items": items_str,
                "description": description,
                "url": url,
            }
            _attach_alert_score(alert, is_material=flagged)
            _attach_alert_provenance(alert)
            if not dry_run:
                dedup_key = build_sec_alert_dedup_key(
                    alert["accession_no"],
                    alert["company"],
                    alert["alert_type"],
                )
                inserted = db.insert_alert("sec_sentinel", alert, dedup_key=dedup_key)
                if not inserted:
                    logger.info("[SEC] Duplicate filing skipped: %s", accession_no)
            alerts.append(alert)
            logger.warning(
                "[SEC] MATERIAL FILING: %s %s (%s) items=%s",
                entity_name,
                form_type,
                filed_at,
                items_str,
            )

    return alerts


@http_retry
async def fetch_edgar_filings(
    company: str,
    since: str,
    until: str,
    client: httpx.AsyncClient,
) -> list[dict]:
    """
    Query EDGAR EFTS for filings mentioning a company name in the given date range.
    Returns raw hit dicts from the API response.
    """
    params = {
        "q": f'"{company}"',
        "forms": ",".join(config.get_sec_cfg().form_types),
        "dateRange": "custom",
        "startdt": since,
        "enddt": until,
    }
    headers = {"User-Agent": _sec_user_agent()}
    resp = await client.get(config.get_sec_cfg().efts_base, params=params, headers=headers)
    resp.raise_for_status()
    body = resp.json()
    return body.get("hits", {}).get("hits", [])


@http_retry
async def fetch_filings_by_cik(
    cik: str,
    entity_name: str,
    since: str,
    until: str,
    client: httpx.AsyncClient,
) -> list[dict]:
    """
    Query EDGAR submissions API directly for a known CIK.
    Only returns filings the entity actually filed — no text-match ambiguity.
    Returns EFTS-compatible hit dicts so downstream processing is unchanged.
    """
    url = f"https://data.sec.gov/submissions/CIK{cik.zfill(10)}.json"
    headers = {"User-Agent": _sec_user_agent()}
    resp = await client.get(url, headers=headers)
    resp.raise_for_status()
    data = resp.json()

    canonical_name = data.get("name") or entity_name
    recent = data.get("filings", {}).get("recent", {})
    target_forms = set(config.get_sec_cfg().form_types)

    hits: list[dict] = []
    for accession, date, form, items in zip(
        recent.get("accessionNumber", []),
        recent.get("filingDate", []),
        recent.get("form", []),
        recent.get("items", []),
        strict=False,
    ):
        if form not in target_forms:
            continue
        if not (since <= date <= until):
            continue
        hits.append(
            {
                "_id": accession,
                "_source": {
                    "entity_name": canonical_name,
                    "form_type": form,
                    "file_date": date,
                    "items": items or "",
                },
            }
        )
    return hits


async def _fetch_company_name(cik: str, client: httpx.AsyncClient) -> str | None:
    """Return the canonical registered company name from EDGAR submissions API."""
    url = f"https://data.sec.gov/submissions/CIK{cik.zfill(10)}.json"
    headers = {"User-Agent": _sec_user_agent()}
    try:
        resp = await client.get(url, headers=headers)
        resp.raise_for_status()
        return resp.json().get("name")
    except Exception as exc:
        logger.warning("[SEC] Could not fetch company name for CIK %s: %s", cik, exc)
        return None


async def run_sec_sentinel(
    conn: sqlite3.Connection,
    since_days: int = 7,
    dry_run: bool = False,
    rotator: ProxyRotator | None = None,
    target_entities: list[str] | None = None,
) -> list[dict]:
    """Scan EDGAR for material 8-K filings; return alert dicts for flagged filings."""
    _sec_user_agent()
    if not dry_run:
        db.init_alerts_table()

    since = (datetime.now(UTC) - timedelta(days=since_days)).strftime("%Y-%m-%d")
    until = datetime.now(UTC).strftime("%Y-%m-%d")
    alerts: list[dict] = []

    entities = _filter_sec_entities_for_targets(_load_sec_entities(), target_entities)

    # Entities with known CIKs — use submissions API (precise, no text-match false positives).
    cik_entities = [(e["sec_cik"].lstrip("0"), e) for e in entities if e.get("sec_cik")]

    # Companies not covered by any entity CIK — fall back to EFTS with filer verification.
    cik_patterns = {p.lower() for _, e in cik_entities for p in e.get("assignee_patterns", [])}
    efts_companies = [c for c in patent_cfg.assignees_of_interest if c.lower() not in cik_patterns]
    if target_entities:
        efts_companies = _filter_efts_companies_for_targets(efts_companies, target_entities)

    async with make_httpx_client(rotator, timeout=config.get_sec_cfg().http_timeout) as client:
        # --- Phase 1: CIK-based queries ---
        for cik, entity in cik_entities:
            try:
                hits = await fetch_filings_by_cik(cik, entity["name"], since, until, client)
            except Exception as exc:
                logger.warning(
                    "[SEC] CIK query failed for %s (CIK %s): %s", entity["name"], cik, exc
                )
                continue
            logger.info("[SEC] %s (CIK %s): %d filing(s) in window", entity["name"], cik, len(hits))
            alerts.extend(_process_hits(hits, conn, dry_run))

        # --- Phase 2: EFTS fallback with filer verification ---
        for company in efts_companies:
            try:
                hits = await fetch_edgar_filings(company, since, until, client)
            except Exception as exc:
                logger.warning("[SEC] EFTS query failed for %s: %s", company, exc)
                continue
            logger.info("[SEC] %s (EFTS): %d filing(s) in window", company, len(hits))

            verified: list[dict] = []
            for hit in hits:
                source = hit.get("_source", {})
                entity_name = source.get("entity_name")
                if not entity_name:
                    raw_cik = _accession_to_cik(hit.get("_id", "0-0-0"))
                    entity_name = await _fetch_company_name(raw_cik, client)
                if not entity_name:
                    logger.warning("[SEC] Could not verify filer for %s — skipping", hit.get("_id"))
                    continue
                if not _name_matches_patterns(entity_name, [company]):
                    logger.warning(
                        "[SEC] Filer mismatch: searched=%r actual=%r accession=%s — skipping",
                        company,
                        entity_name,
                        hit.get("_id"),
                    )
                    continue
                source["entity_name"] = entity_name
                verified.append(hit)
            alerts.extend(_process_hits(verified, conn, dry_run))

    out_dir = Path(config.get_sec_cfg().output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = int(time.time())
    (out_dir / f"sec_{ts}.json").write_text(json.dumps(alerts, indent=2))

    return alerts


if __name__ == "__main__":
    import asyncio
    import logging as _logging

    from modules.data_paths import data_path
    from modules.state_db import init_db

    _logging.basicConfig(level=logging.INFO)
    _conn = init_db(data_path("state.db"))
    asyncio.run(run_sec_sentinel(_conn, dry_run=True))
