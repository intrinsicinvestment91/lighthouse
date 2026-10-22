"""
Module 5 — arXiv "Going Dark" Sentinel.

Monitors named researchers via the arXiv Atom feed API.
When an active researcher stops publishing for silence_threshold_days,
a Nostr alert is emitted — flagging a possible NDA or classification event.
"""

import json
import logging
import sqlite3
import time
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path

import httpx

from config import arxiv_cfg
from modules import db
from modules.alerts import ALERT_SCHEMA_VERSION
from modules.provenance import ProvenanceRecord
from modules.proxy_rotator import ProxyRotator, make_httpx_client
from modules.retry import http_retry
from modules.scoring import AlertScore

logger = logging.getLogger(__name__)

_ATOM_NS = "http://www.w3.org/2005/Atom"
_ARXIV_NS = "http://arxiv.org/schemas/atom"


def _filter_researchers_for_targets(
    researchers: list[str],
    target_entities: list[str] | None = None,
    target_keywords: list[str] | None = None,
) -> list[str]:
    """Return researchers whose label matches target entities or keywords."""
    has_entity_filter = bool(target_entities)
    has_keyword_filter = bool(target_keywords)
    if not has_entity_filter and not has_keyword_filter:
        return researchers

    targets: list[str] = []
    if target_entities:
        targets.extend(target_entities)
    if target_keywords:
        targets.extend(target_keywords)

    filtered: list[str] = []
    for researcher in researchers:
        researcher_lower = researcher.lower()
        if any(
            target.lower() in researcher_lower or researcher_lower in target.lower()
            for target in targets
        ):
            filtered.append(researcher)
    return filtered


def _paper_matches_target_keywords(
    paper: dict | None,
    target_keywords: list[str] | None = None,
) -> bool:
    """Return True when paper text matches any target keyword, or when no keywords."""
    if not target_keywords:
        return True
    if not paper:
        return False

    parts: list[str] = []
    for key in ("title", "summary", "abstract"):
        value = paper.get(key)
        if isinstance(value, str) and value:
            parts.append(value)
    categories = paper.get("categories")
    if isinstance(categories, list):
        parts.extend(str(c) for c in categories if c)

    haystack = " ".join(parts).lower()
    return any(keyword.lower() in haystack for keyword in target_keywords)


def _entry_to_paper(entry: ET.Element) -> dict:
    title_el = entry.find(f"{{{_ATOM_NS}}}title")
    summary_el = entry.find(f"{{{_ATOM_NS}}}summary")
    title = title_el.text.strip() if title_el is not None and title_el.text else ""
    summary = summary_el.text.strip() if summary_el is not None and summary_el.text else ""
    categories = [
        cat.get("term", "") for cat in entry.findall(f"{{{_ARXIV_NS}}}category") if cat.get("term")
    ]
    return {"title": title, "summary": summary, "categories": categories}


def _attach_alert_score(alert: dict) -> None:
    silent_days = int(alert.get("silent_days", 0))
    score = AlertScore(
        source_reliability=0.85,
        keyword_strength=0.7,
        recency=max(0.0, 1.0 - silent_days / 365),
        uniqueness=min(silent_days / 180, 1.0),
        watchlist_match=1.0,
    )
    alert["score"] = round(score.total(), 3)
    alert["score_label"] = score.label()
    alert["alert_type"] = "researcher_silence"
    alert["schema_version"] = ALERT_SCHEMA_VERSION


def _attach_alert_provenance(alert: dict) -> None:
    name = alert.get("name", "")
    provenance = ProvenanceRecord(
        source_type="arxiv_api",
        source_url=f"https://arxiv.org/search/?searchtype=author&query={name.replace(' ', '+')}",
        retrieved_at=datetime.now(UTC).isoformat(),
        metadata={"institution": alert.get("institution", "")},
    )
    alert["provenance"] = provenance.to_dict()


@http_retry
async def fetch_latest_paper(
    researcher: dict,
    client: httpx.AsyncClient,
) -> tuple[str | None, dict | None]:
    """
    Return (ISO date, paper dict) for the most recent arXiv paper, or (None, None).

    researcher dict keys: name (required), topics (optional list[str]).
    """
    name = researcher["name"]
    topics = researcher.get("topics", [])

    query_parts = [f'au:"{name}"']
    if topics:
        query_parts.append("(" + " OR ".join(f"ti:{t}" for t in topics) + ")")

    params = {
        "search_query": " AND ".join(query_parts),
        "start": 0,
        "max_results": 5,
        "sortBy": "lastUpdatedDate",
        "sortOrder": "descending",
    }
    resp = await client.get(arxiv_cfg.arxiv_base, params=params)
    resp.raise_for_status()

    try:
        root = ET.fromstring(resp.text)
    except ET.ParseError as exc:
        logger.warning("[arXiv] XML parse error for %s: %s", name, exc)
        return None, None

    entries = root.findall(f"{{{_ATOM_NS}}}entry")
    if not entries:
        return None, None

    entry = entries[0]
    published_el = entry.find(f"{{{_ATOM_NS}}}published")
    if published_el is None or not published_el.text:
        return None, None

    latest = published_el.text[:10]  # "2026-03-15T18:44:12Z" → "2026-03-15"
    return latest, _entry_to_paper(entry)


async def run_arxiv_sentinel(
    conn: sqlite3.Connection,
    dry_run: bool = False,
    rotator: ProxyRotator | None = None,
    target_entities: list[str] | None = None,
    target_keywords: list[str] | None = None,
) -> list[dict]:
    """Scan all configured researchers; return silence-alert dicts."""
    if not arxiv_cfg.researchers:
        logger.info("[arXiv] No researchers configured — skipping")
        return []

    all_researchers = arxiv_cfg.researchers
    match_labels = [f"{r['name']} {r.get('institution', '')}".strip() for r in all_researchers]
    if target_entities or target_keywords:
        selected_labels = set(
            _filter_researchers_for_targets(match_labels, target_entities, target_keywords)
        )
        researchers = [
            r
            for r, label in zip(all_researchers, match_labels, strict=True)
            if label in selected_labels
        ]
        logger.info(
            "[arXiv] Target filter active: %d of %d researchers selected",
            len(researchers),
            len(all_researchers),
        )
    else:
        researchers = all_researchers

    if not researchers:
        logger.info("[arXiv] Target filter excluded all researchers — skipping")
        return []

    alerts: list[dict] = []
    now = int(time.time())

    async with make_httpx_client(rotator, timeout=arxiv_cfg.http_timeout) as client:
        for researcher in researchers:
            name = researcher["name"]
            institution = researcher.get("institution", "")

            latest, paper = await fetch_latest_paper(researcher, client)

            if (
                latest
                and target_keywords
                and not _paper_matches_target_keywords(paper, target_keywords)
            ):
                logger.info(
                    "[arXiv] %s — latest paper skipped (keyword filter)",
                    name,
                )
                continue

            row = conn.execute(
                "SELECT last_pub_date, first_silence_at, notified FROM researchers WHERE name = ?",
                (name,),
            ).fetchone()

            if latest:
                if not dry_run:
                    conn.execute(
                        """
                        INSERT INTO researchers
                            (name, institution, last_pub_date, first_silence_at, notified)
                        VALUES (?, ?, ?, NULL, 0)
                        ON CONFLICT (name) DO UPDATE SET
                            institution = excluded.institution,
                            last_pub_date = excluded.last_pub_date,
                            first_silence_at = NULL,
                            notified = 0
                        """,
                        (name, institution, latest),
                    )
                    conn.commit()
                logger.info("[arXiv] %s — last paper: %s", name, latest)
            else:
                # No recent papers — start or continue silence tracking
                first_silence_at = row[1] if row and row[1] else now
                notified = row[2] if row else 0
                last_pub_date = row[0] if row else None

                if not dry_run:
                    conn.execute(
                        """
                        INSERT INTO researchers
                            (name, institution, last_pub_date, first_silence_at, notified)
                        VALUES (?, ?, ?, ?, 0)
                        ON CONFLICT (name) DO UPDATE SET
                            institution = excluded.institution,
                            first_silence_at = COALESCE(researchers.first_silence_at,
                                                        excluded.first_silence_at)
                        """,
                        (name, institution, last_pub_date, first_silence_at),
                    )
                    conn.commit()

                silent_days = (now - first_silence_at) // 86400
                if silent_days >= arxiv_cfg.silence_threshold_days and not notified:
                    alert = {
                        "name": name,
                        "institution": institution,
                        "last_pub_date": last_pub_date,
                        "silent_days": silent_days,
                    }
                    alerts.append(alert)
                    logger.warning(
                        "[arXiv] SILENCE ALERT: %s (%s) — %d days",
                        name,
                        institution,
                        silent_days,
                    )
                    if not dry_run:
                        conn.execute(
                            "UPDATE researchers SET notified = 1 WHERE name = ?",
                            (name,),
                        )
                        conn.commit()

    for alert in alerts:
        _attach_alert_score(alert)
        _attach_alert_provenance(alert)
    if not dry_run:
        db.init_alerts_table()
        for alert in alerts:
            db.insert_alert("arxiv_sentinel", alert)

    out_dir = Path(arxiv_cfg.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = int(time.time())
    (out_dir / f"arxiv_{ts}.json").write_text(json.dumps(alerts, indent=2))

    return alerts


if __name__ == "__main__":
    import asyncio
    import logging as _logging

    from modules.data_paths import data_path
    from modules.state_db import init_db

    _logging.basicConfig(level=logging.INFO)
    _conn = init_db(data_path("state.db"))
    asyncio.run(run_arxiv_sentinel(_conn))
