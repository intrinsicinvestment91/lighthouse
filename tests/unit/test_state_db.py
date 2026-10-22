"""Unit tests for modules/state_db.py"""

import sqlite3

from modules.state_db import (
    SCHEMA_VERSION,
    init_db,
    is_seen,
    mark_seen,
)


def test_init_creates_table(tmp_db_path):
    conn = init_db(tmp_db_path)
    cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='seen'")
    assert cursor.fetchone() is not None
    conn.close()


def test_is_seen_false_for_new_key(tmp_db):
    assert is_seen(tmp_db, "patent:US999999") is False


def test_mark_then_is_seen(tmp_db):
    mark_seen(tmp_db, "patent:US111111")
    assert is_seen(tmp_db, "patent:US111111") is True


def test_is_seen_does_not_cross_pollute(tmp_db):
    mark_seen(tmp_db, "patent:US111111")
    assert is_seen(tmp_db, "archive:https://example.com") is False


def test_duplicate_mark_is_idempotent(tmp_db):
    mark_seen(tmp_db, "patent:US222222")
    mark_seen(tmp_db, "patent:US222222")  # should not raise
    assert is_seen(tmp_db, "patent:US222222") is True


def test_first_seen_at_is_set(tmp_db):
    mark_seen(tmp_db, "patent:US333333")
    row = tmp_db.execute(
        "SELECT first_seen_at FROM seen WHERE key = ?", ("patent:US333333",)
    ).fetchone()
    assert row is not None
    assert row[0] > 0


def test_schema_version_set_on_fresh_db(tmp_db_path):
    conn = init_db(tmp_db_path)
    ver = conn.execute("PRAGMA user_version").fetchone()[0]
    assert ver == SCHEMA_VERSION
    conn.close()


def test_schema_version_idempotent_on_second_open(tmp_db_path):
    conn1 = init_db(tmp_db_path)
    conn1.close()
    conn2 = init_db(tmp_db_path)
    ver = conn2.execute("PRAGMA user_version").fetchone()[0]
    assert ver == SCHEMA_VERSION
    conn2.close()


def test_schema_version_constant_is_positive_int():
    assert isinstance(SCHEMA_VERSION, int)
    assert SCHEMA_VERSION >= 1


def test_init_upgrades_v2_correlation_events_with_missing_observed_at(tmp_db_path):
    conn = sqlite3.connect(tmp_db_path)
    conn.executescript(
        """
        CREATE TABLE correlation_events (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            event_type      TEXT    NOT NULL,
            entities_json   TEXT    NOT NULL,
            evidence_json   TEXT    NOT NULL,
            score           REAL    NOT NULL,
            detected_at     TEXT    NOT NULL,
            nostr_event_id  TEXT,
            dedup_hash      TEXT
        );
        PRAGMA user_version = 2;
        """
    )
    conn.commit()
    conn.close()

    upgraded = init_db(tmp_db_path)
    cols = {row[1] for row in upgraded.execute("PRAGMA table_info(correlation_events)").fetchall()}
    assert "event_id" in cols
    assert "score" in cols
    assert "signals_json" in cols
    assert "evidence_json" in cols
    assert "sec_filed_at" in cols
    assert "observed_at" in cols
    upgraded.close()
