"""
Module 4 — Patent Velocity Tracker.

Detects statistical anomalies in weekly patent filing rates per IPC class.
A spike (>2σ above rolling mean) signals a breakthrough cluster;
a multi-week freeze signals possible classification / NDA activity.
"""

import json
import logging
import sqlite3
import time
from datetime import UTC, datetime
from pathlib import Path
from statistics import mean, stdev

import config
from modules import db
from modules.alerts import ALERT_SCHEMA_VERSION
from modules.provenance import ProvenanceRecord
from modules.scoring import AlertScore

logger = logging.getLogger(__name__)


class _VelocityCfgProxy:
    """Delegate attribute access to the current runtime velocity config."""

    def __getattr__(self, name: str):
        return getattr(config.get_velocity_cfg(), name)

    def __setattr__(self, name: str, value):
        setattr(config.get_velocity_cfg(), name, value)


# Backward-compatible alias for tests that patch module-level config directly.
velocity_cfg = _VelocityCfgProxy()


def _iso_week(ts: float | None = None) -> str:
    """Return ISO week string, e.g. '2026-W17'."""
    dt = datetime.fromtimestamp(ts, UTC) if ts else datetime.now(UTC)
    return f"{dt.year}-W{dt.isocalendar().week:02d}"


async def record_counts(conn: sqlite3.Connection, records: list[dict]) -> None:
    """Upsert weekly filing counts derived from a completed patent run."""
    week = _iso_week()
    class_counts: dict[str, int] = {}
    for rec in records:
        ipc = (rec.get("ipc") or "UNKNOWN").split()[0]
        class_counts[ipc] = class_counts.get(ipc, 0) + 1

    for ipc_class, count in class_counts.items():
        conn.execute(
            """
            INSERT INTO patent_velocity (ipc_class, week, count) VALUES (?, ?, ?)
            ON CONFLICT (ipc_class, week) DO UPDATE SET count = count + excluded.count
            """,
            (ipc_class, week, count),
        )
    conn.commit()


def detect_anomalies(conn: sqlite3.Connection, ipc_class: str) -> list[dict]:
    """Return anomaly dicts for a class: {type, ipc_class, week, zscore, count, mean}."""
    rows = conn.execute(
        "SELECT week, count FROM patent_velocity WHERE ipc_class = ? ORDER BY week",
        (ipc_class,),
    ).fetchall()

    if len(rows) < config.get_velocity_cfg().min_history_weeks:
        return []

    weeks = [r[0] for r in rows]
    counts = [r[1] for r in rows]
    current_week = weeks[-1]
    current_count = counts[-1]
    history = counts[:-1]

    anomalies: list[dict] = []
    mu = mean(history)
    sigma = stdev(history) if len(history) > 1 else 0.0

    if sigma > 0:
        z = (current_count - mu) / sigma
        if z >= config.get_velocity_cfg().spike_zscore_threshold:
            alert = {
                "type": "spike",
                "ipc_class": ipc_class,
                "week": current_week,
                "zscore": round(z, 2),
                "count": current_count,
                "mean": round(mu, 1),
            }
            _attach_alert_score(alert)
            _attach_alert_provenance(alert)
            anomalies.append(alert)

    # Freeze: N consecutive zero-count weeks at the tail
    tail = counts[-config.get_velocity_cfg().freeze_weeks :]
    if len(tail) >= config.get_velocity_cfg().freeze_weeks and all(c == 0 for c in tail) and mu > 0:
        alert = {
            "type": "freeze",
            "ipc_class": ipc_class,
            "week": current_week,
            "zscore": 0.0,
            "count": 0,
            "mean": round(mu, 1),
        }
        _attach_alert_score(alert)
        _attach_alert_provenance(alert)
        anomalies.append(alert)

    return anomalies


def _attach_alert_score(alert: dict) -> None:
    """Add deterministic score metadata to anomaly alerts."""
    is_spike = alert.get("type") == "spike"
    zscore = float(alert.get("zscore", 0.0))
    # Higher z-score implies a rarer deviation; freeze uses a neutral baseline.
    uniqueness = min(max(zscore / 4.0, 0.0), 1.0) if is_spike else 0.5
    score = AlertScore(
        source_reliability=0.8,
        keyword_strength=0.7 if is_spike else 0.6,
        recency=1.0,
        uniqueness=uniqueness,
        watchlist_match=0.0,
    )
    alert["score"] = round(score.total(), 3)
    alert["score_label"] = score.label()
    alert["alert_type"] = "velocity_spike" if is_spike else "velocity_freeze"
    alert["schema_version"] = ALERT_SCHEMA_VERSION


def _attach_alert_provenance(alert: dict, *, source_type: str = "velocity_tracker") -> None:
    """Attach provenance metadata to velocity anomaly alerts."""
    provenance = ProvenanceRecord(
        source_type=source_type,
        metadata={
            "alert_type": alert.get("type"),
            "assignee": alert.get("assignee"),
            "metric": "patent_velocity",
        },
    )
    alert["provenance"] = provenance.to_dict()


async def run_velocity_tracker(
    conn: sqlite3.Connection,
    records: list[dict],
    dry_run: bool = False,
) -> list[dict]:
    """Record this run's counts then detect anomalies across all IPC classes."""
    if not dry_run:
        db.init_alerts_table()
        await record_counts(conn, records)

    known_classes = {
        r[0] for r in conn.execute("SELECT DISTINCT ipc_class FROM patent_velocity").fetchall()
    }

    anomalies: list[dict] = []
    for ipc_class in sorted(known_classes):
        anomalies.extend(detect_anomalies(conn, ipc_class))

    if not dry_run:
        for alert in anomalies:
            db.insert_alert("velocity_tracker", alert)

    if anomalies:
        logger.info(
            "[Velocity] %d anomalies: %s",
            len(anomalies),
            [f"{a['type']}/{a['ipc_class']}" for a in anomalies],
        )

    out_dir = Path(config.get_velocity_cfg().output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = int(time.time())
    (out_dir / f"velocity_{ts}.json").write_text(json.dumps(anomalies, indent=2))

    return anomalies


if __name__ == "__main__":
    import asyncio
    import logging as _logging

    from modules.data_paths import data_path
    from modules.state_db import init_db

    _logging.basicConfig(level=logging.INFO)
    _conn = init_db(data_path("state.db"))
    asyncio.run(run_velocity_tracker(_conn, []))
