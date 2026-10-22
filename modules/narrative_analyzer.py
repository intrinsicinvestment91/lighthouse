"""
Module 7 — Narrative Shift Analyzer.

Scores government press releases on urgency and specificity using the
Anthropic API, then flags agencies whose tone has shifted significantly
from their historical baseline — a potential indicator of acceleration
or information lockdown.

Requires: pip install "anthropic>=0.30" and ANTHROPIC_API_KEY env var.
Gracefully skips if either is absent.
"""

import json
import logging
import re
import sqlite3
import time
from datetime import UTC, datetime
from pathlib import Path

import config
from modules import db
from modules.alerts import ALERT_SCHEMA_VERSION
from modules.provenance import ProvenanceRecord
from modules.scoring import AlertScore

logger = logging.getLogger(__name__)


def _attach_alert_score(alert: dict) -> None:
    urgency_delta = abs(float(alert.get("urgency_delta", 0.0)))
    specificity_delta = abs(float(alert.get("specificity_delta", 0.0)))
    score = AlertScore(
        source_reliability=0.7,
        keyword_strength=min((urgency_delta + specificity_delta) / 2.0, 1.0),
        recency=1.0,
        uniqueness=min(max(urgency_delta, specificity_delta), 1.0),
        watchlist_match=1.0,
    )
    alert["score"] = round(score.total(), 3)
    alert["score_label"] = score.label()
    alert["alert_type"] = "narrative_shift"
    alert["schema_version"] = ALERT_SCHEMA_VERSION


def _attach_alert_provenance(alert: dict) -> None:
    provenance = ProvenanceRecord(
        source_type="web_archive",
        source_url=alert.get("url", ""),
        retrieved_at=datetime.now(UTC).isoformat(),
        metadata={"agency": alert.get("agency", "")},
    )
    alert["provenance"] = provenance.to_dict()


def _extract_text(html: str, max_chars: int = 4000) -> str:
    """Strip HTML tags for LLM input (no external deps)."""
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:max_chars]


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


def _page_search_text(page) -> str:
    parts: list[str] = []
    for key in ("agency", "url", "title"):
        value = getattr(page, key, None)
        if isinstance(value, str) and value:
            parts.append(value)
    return " ".join(parts).lower()


def _filter_pages_for_targets(
    pages: list,
    target_entities: list[str] | None = None,
    target_keywords: list[str] | None = None,
) -> list:
    """Return pages whose metadata matches target entities or keywords."""
    if not target_entities and not target_keywords:
        return pages

    filtered: list = []
    for page in pages:
        haystack = _page_search_text(page)
        targets: list[str] = []
        if target_entities:
            targets.extend(target_entities)
        if target_keywords:
            targets.extend(target_keywords)
        if any(target.lower() in haystack for target in targets):
            filtered.append(page)
    return filtered


async def analyze_page(page, client) -> dict | None:
    """
    Score a single ArchivedPage via LLM.
    Returns {url, agency, urgency, specificity, summary} or None on failure.
    """
    local_path = Path(page.local_path)
    if not local_path.exists():
        logger.debug("[Narrative] Missing local file for %s — skip", page.url)
        return None

    html = local_path.read_text(encoding="utf-8", errors="replace")
    text = _extract_text(html)
    if not text:
        return None

    prompt = f"""Analyze this government press release for tone signals.

Respond with JSON only — no explanation, no markdown fences.

{{
  "urgency": <integer 1-10, 1=routine, 10=emergency>,
  "specificity": <integer 1-10, 1=vague, 10=precise/technical>,
  "summary": "<one sentence describing the key announcement>"
}}

TEXT:
{text}"""

    try:
        message = await client.messages.create(
            model=config.get_narrative_cfg().model,
            max_tokens=256,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = message.content[0].text.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        scores = json.loads(raw)
        return {
            "url": page.url,
            "agency": page.agency,
            "urgency": float(scores["urgency"]),
            "specificity": float(scores["specificity"]),
            "summary": str(scores.get("summary", "")),
        }
    except Exception as exc:
        logger.warning("[Narrative] LLM failed for %s: %s", page.url, exc)
        return None


def _store_score(conn: sqlite3.Connection, score: dict) -> None:
    conn.execute(
        """
        INSERT INTO narrative_scores (url, agency, analyzed_at, urgency, specificity, summary)
        VALUES (:url, :agency, :analyzed_at, :urgency, :specificity, :summary)
        ON CONFLICT (url) DO UPDATE SET
            analyzed_at = excluded.analyzed_at,
            urgency = excluded.urgency,
            specificity = excluded.specificity,
            summary = excluded.summary
        """,
        {**score, "analyzed_at": int(time.time())},
    )
    conn.commit()


def detect_shifts(conn: sqlite3.Connection, agency: str) -> list[dict]:
    """
    Compare the newest scores for an agency against their rolling historical mean.
    Returns alert dicts where urgency or specificity delta exceeds the threshold.
    """
    cutoff = int(time.time()) - config.get_narrative_cfg().context_window_days * 86400
    rows = conn.execute(
        """
        SELECT url, urgency, specificity, summary, analyzed_at
        FROM narrative_scores
        WHERE agency = ?
        ORDER BY analyzed_at DESC
        """,
        (agency,),
    ).fetchall()

    if len(rows) < 3:
        return []

    historical = [(r[1], r[2]) for r in rows if r[4] < cutoff]
    if len(historical) < 2:
        return []

    mu_urgency = sum(r[0] for r in historical) / len(historical)
    mu_specificity = sum(r[1] for r in historical) / len(historical)

    alerts = []
    for url, urgency, specificity, summary, analyzed_at in rows:
        if analyzed_at < cutoff:
            continue
        u_delta = urgency - mu_urgency
        s_delta = specificity - mu_specificity
        if (
            abs(u_delta) >= config.get_narrative_cfg().shift_threshold
            or abs(s_delta) >= config.get_narrative_cfg().shift_threshold
        ):
            alerts.append(
                {
                    "agency": agency,
                    "url": url,
                    "urgency_delta": round(u_delta, 2),
                    "specificity_delta": round(s_delta, 2),
                    "summary": summary,
                }
            )
    return alerts


async def run_narrative_analyzer(
    conn: sqlite3.Connection,
    pages: list,
    dry_run: bool = False,
    target_entities: list[str] | None = None,
    target_keywords: list[str] | None = None,
) -> list[dict]:
    """Analyze all newly archived pages; return shift-alert dicts."""
    if not config.get_narrative_cfg().anthropic_api_key:
        logger.info("[Narrative] ANTHROPIC_API_KEY not set — skipping")
        return []

    try:
        import anthropic
    except ImportError:
        logger.warning("[Narrative] anthropic package not installed — skipping")
        return []

    if target_entities or target_keywords:
        logger.info("[Narrative] Target filter active")
        pages = _filter_pages_for_targets(pages, target_entities, target_keywords)
        if not pages:
            return []

    client = anthropic.AsyncAnthropic(api_key=config.get_narrative_cfg().anthropic_api_key)

    new_scores: list[dict] = []
    for page in pages:
        if target_entities or target_keywords:
            local_path = Path(page.local_path)
            if local_path.exists():
                html = local_path.read_text(encoding="utf-8", errors="replace")
                text = _extract_text(html)
                if text and not _text_matches_targets(text, target_entities, target_keywords):
                    continue

        score = await analyze_page(page, client)
        if score:
            new_scores.append(score)
            if not dry_run:
                _store_score(conn, score)

    agencies = {s["agency"] for s in new_scores}
    all_alerts: list[dict] = []
    for agency in sorted(agencies):
        all_alerts.extend(detect_shifts(conn, agency))

    if all_alerts:
        logger.warning(
            "[Narrative] %d shift alerts: %s",
            len(all_alerts),
            [a["agency"] for a in all_alerts],
        )

    for alert in all_alerts:
        _attach_alert_score(alert)
        _attach_alert_provenance(alert)
    if not dry_run:
        db.init_alerts_table()
        for alert in all_alerts:
            db.insert_alert("narrative_analyzer", alert)

    out_dir = Path(config.get_narrative_cfg().output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = int(time.time())
    (out_dir / f"narrative_{ts}.json").write_text(json.dumps(all_alerts, indent=2))

    return all_alerts
