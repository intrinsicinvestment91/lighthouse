"""Unit tests for modules/velocity_tracker.py"""

import sqlite3

import modules.db as db
import modules.velocity_tracker as velocity_tracker_module
from modules.alerts import AlertRecord
from modules.velocity_tracker import (
    _iso_week,
    detect_anomalies,
    record_counts,
    run_velocity_tracker,
)


def _seed_weeks(conn: sqlite3.Connection, ipc: str, counts: list[int]) -> None:
    """Insert synthetic weekly counts; week labels are sequential strings."""
    for i, count in enumerate(counts):
        conn.execute(
            "INSERT OR REPLACE INTO patent_velocity (ipc_class, week, count) VALUES (?, ?, ?)",
            (ipc, f"2026-W{i + 1:02d}", count),
        )
    conn.commit()


def _alerts_row_count_or_zero() -> int:
    try:
        return len(db.fetchall("SELECT * FROM alerts"))
    except sqlite3.OperationalError as exc:
        if "no such table: alerts" in str(exc):
            return 0
        raise


def test_iso_week_format():
    week = _iso_week()
    assert week.startswith("20") and "-W" in week


def test_detect_anomalies_no_history(tmp_db):
    _seed_weeks(tmp_db, "G21B", [5, 4, 6])  # fewer than min_history_weeks
    assert detect_anomalies(tmp_db, "G21B") == []


def test_detect_anomalies_spike(tmp_db, monkeypatch):
    monkeypatch.setattr(velocity_tracker_module.velocity_cfg, "min_history_weeks", 5)
    monkeypatch.setattr(velocity_tracker_module.velocity_cfg, "spike_zscore_threshold", 2.0)
    # Varied history so stdev > 0, then a large spike
    _seed_weeks(tmp_db, "G21B", [3, 5, 4, 6, 50])
    alerts = detect_anomalies(tmp_db, "G21B")
    assert len(alerts) == 1
    assert alerts[0]["type"] == "spike"
    assert alerts[0]["zscore"] > 2.0
    assert "score" in alerts[0]
    assert "score_label" in alerts[0]
    assert alerts[0]["score_label"] in {"low", "medium", "high"}
    assert "provenance" in alerts[0]
    assert alerts[0]["provenance"]["source_type"] == "velocity_tracker"
    assert alerts[0]["provenance"]["metadata"]["alert_type"] == "spike"
    assert alerts[0]["provenance"]["metadata"]["metric"] == "patent_velocity"
    record = AlertRecord(
        source="velocity_tracker",
        alert_type=alerts[0]["type"],
        entity=alerts[0].get("assignee"),
        score=alerts[0].get("score"),
        score_label=alerts[0].get("score_label"),
        provenance=alerts[0].get("provenance"),
        payload=alerts[0],
    )
    record.validate()


def test_detect_anomalies_no_spike_below_threshold(tmp_db, monkeypatch):
    monkeypatch.setattr(velocity_tracker_module.velocity_cfg, "min_history_weeks", 5)
    monkeypatch.setattr(velocity_tracker_module.velocity_cfg, "spike_zscore_threshold", 2.0)
    _seed_weeks(tmp_db, "G21B", [5, 5, 5, 5, 6])  # small increase, not a spike
    assert detect_anomalies(tmp_db, "G21B") == []


def test_detect_anomalies_freeze(tmp_db, monkeypatch):
    monkeypatch.setattr(velocity_tracker_module.velocity_cfg, "min_history_weeks", 5)
    monkeypatch.setattr(velocity_tracker_module.velocity_cfg, "freeze_weeks", 3)
    _seed_weeks(tmp_db, "G21B", [5, 5, 0, 0, 0])
    alerts = detect_anomalies(tmp_db, "G21B")
    freeze_alerts = [a for a in alerts if a["type"] == "freeze"]
    assert freeze_alerts
    assert "score" in freeze_alerts[0]
    assert "score_label" in freeze_alerts[0]
    assert freeze_alerts[0]["score_label"] in {"low", "medium", "high"}
    assert "provenance" in freeze_alerts[0]
    assert freeze_alerts[0]["provenance"]["source_type"] == "velocity_tracker"
    assert freeze_alerts[0]["provenance"]["metadata"]["alert_type"] == "freeze"
    assert freeze_alerts[0]["provenance"]["metadata"]["metric"] == "patent_velocity"
    record = AlertRecord(
        source="velocity_tracker",
        alert_type=freeze_alerts[0]["type"],
        entity=freeze_alerts[0].get("assignee"),
        score=freeze_alerts[0].get("score"),
        score_label=freeze_alerts[0].get("score_label"),
        provenance=freeze_alerts[0].get("provenance"),
        payload=freeze_alerts[0],
    )
    record.validate()


def test_detect_anomalies_freeze_requires_prior_activity(tmp_db, monkeypatch):
    monkeypatch.setattr(velocity_tracker_module.velocity_cfg, "min_history_weeks", 5)
    monkeypatch.setattr(velocity_tracker_module.velocity_cfg, "freeze_weeks", 3)
    # All zeros from the start — mean is 0, so no freeze alert expected
    _seed_weeks(tmp_db, "G21B", [0, 0, 0, 0, 0])
    alerts = detect_anomalies(tmp_db, "G21B")
    assert not any(a["type"] == "freeze" for a in alerts)


async def test_record_counts_upserts(tmp_db):
    records = [
        {"ipc": "G21B", "source": "USPTO"},
        {"ipc": "G21B", "source": "WIPO"},
        {"ipc": "C22C", "source": "USPTO"},
    ]
    await record_counts(tmp_db, records)
    rows = {
        r[0]: r[1]
        for r in tmp_db.execute("SELECT ipc_class, count FROM patent_velocity").fetchall()
    }
    assert rows["G21B"] == 2
    assert rows["C22C"] == 1


async def test_run_velocity_tracker_dry_run_skips_insert(tmp_db):
    records = [{"ipc": "G21B", "source": "USPTO"}]
    await run_velocity_tracker(tmp_db, records, dry_run=True)
    count = tmp_db.execute("SELECT COUNT(*) FROM patent_velocity").fetchone()[0]
    assert count == 0


async def test_run_velocity_tracker_returns_list(tmp_db):
    result = await run_velocity_tracker(tmp_db, [], dry_run=False)
    assert isinstance(result, list)


async def test_run_velocity_tracker_persists_alerts(tmp_db, tmp_db_path, monkeypatch):
    monkeypatch.setattr(velocity_tracker_module.velocity_cfg, "min_history_weeks", 5)
    monkeypatch.setattr(velocity_tracker_module.velocity_cfg, "spike_zscore_threshold", 2.0)
    monkeypatch.setattr(db, "DB_PATH", tmp_db_path)
    _seed_weeks(tmp_db, "G21B", [3, 5, 4, 6, 50])

    alerts = await run_velocity_tracker(tmp_db, [], dry_run=False)

    rows = db.fetchall("SELECT * FROM alerts")
    assert alerts
    assert len(rows) >= 1


async def test_run_velocity_tracker_dry_run_does_not_persist_alerts(
    tmp_db, tmp_db_path, monkeypatch
):
    monkeypatch.setattr(velocity_tracker_module.velocity_cfg, "min_history_weeks", 5)
    monkeypatch.setattr(velocity_tracker_module.velocity_cfg, "spike_zscore_threshold", 2.0)
    monkeypatch.setattr(db, "DB_PATH", tmp_db_path)
    _seed_weeks(tmp_db, "G21B", [3, 5, 4, 6, 50])

    alerts = await run_velocity_tracker(tmp_db, [], dry_run=True)

    assert alerts
    assert _alerts_row_count_or_zero() == 0
