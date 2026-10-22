"""
Cross-run deduplication cache backed by SQLite (stdlib).

Tracks seen patent IDs and archived URLs so the pipeline never re-publishes
the same Nostr event across invocations.
"""

import json
import sqlite3
import time
from pathlib import Path

# Increment when adding a new entry to MIGRATIONS.
SCHEMA_VERSION = 3

# Each entry: (target_version, ddl_sql).  Runs in order; skipped if
# PRAGMA user_version >= target_version.  Use CREATE ... IF NOT EXISTS
# so migrations are safe to replay against an existing schema.
MIGRATIONS: list[tuple[int, str]] = [
    (
        2,
        """
        CREATE TABLE IF NOT EXISTS correlation_events (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            event_type      TEXT    NOT NULL,
            entities_json   TEXT    NOT NULL,
            evidence_json   TEXT    NOT NULL,
            score           REAL    NOT NULL,
            detected_at     TEXT    NOT NULL,
            nostr_event_id  TEXT,
            dedup_hash      TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_correlation_dedup
            ON correlation_events (dedup_hash);
        CREATE INDEX IF NOT EXISTS idx_correlation_score
            ON correlation_events (score);
    """,
    ),
    (3, ""),
]

_SCHEMA_SQL = """
        CREATE TABLE IF NOT EXISTS seen (
            key           TEXT    PRIMARY KEY,
            first_seen_at INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS patent_velocity (
            ipc_class TEXT    NOT NULL,
            week      TEXT    NOT NULL,
            count     INTEGER NOT NULL,
            PRIMARY KEY (ipc_class, week)
        );

        CREATE TABLE IF NOT EXISTS researchers (
            name             TEXT    PRIMARY KEY,
            institution      TEXT,
            last_pub_date    TEXT,
            first_silence_at INTEGER,
            notified         INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS narrative_scores (
            url          TEXT    PRIMARY KEY,
            agency       TEXT    NOT NULL,
            analyzed_at  INTEGER NOT NULL,
            urgency      REAL    NOT NULL,
            specificity  REAL    NOT NULL,
            summary      TEXT
        );

        CREATE TABLE IF NOT EXISTS flight_observations (
            icao24      TEXT    NOT NULL,
            callsign    TEXT,
            facility    TEXT    NOT NULL,
            observed_at INTEGER NOT NULL,
            lat         REAL,
            lon         REAL,
            PRIMARY KEY (icao24, facility, observed_at)
        );

        CREATE TABLE IF NOT EXISTS dms_registrations (
            id                TEXT    PRIMARY KEY,
            display_name      TEXT    NOT NULL,
            encrypted_message TEXT    NOT NULL,
            interval_hours    INTEGER NOT NULL,
            last_checkin_at   INTEGER NOT NULL,
            nostr_pubkey      TEXT,
            active            INTEGER DEFAULT 1
        );

        CREATE TABLE IF NOT EXISTS dms_broadcasts (
            registration_id TEXT    NOT NULL,
            broadcast_at    INTEGER NOT NULL,
            nostr_event_id  TEXT
        );

        CREATE TABLE IF NOT EXISTS adsb_observations (
            hex         TEXT    NOT NULL,
            callsign    TEXT,
            facility    TEXT    NOT NULL,
            observed_at INTEGER NOT NULL,
            lat         REAL,
            lon         REAL,
            military    INTEGER DEFAULT 0,
            interesting INTEGER DEFAULT 0,
            PRIMARY KEY (hex, facility, observed_at)
        );

        CREATE TABLE IF NOT EXISTS sec_filings (
            accession_no  TEXT    PRIMARY KEY,
            cik           TEXT    NOT NULL,
            company       TEXT    NOT NULL,
            form_type     TEXT    NOT NULL,
            filed_at      TEXT    NOT NULL,
            items         TEXT,
            description   TEXT,
            url           TEXT    NOT NULL
        );

        CREATE TABLE IF NOT EXISTS correlation_events (
            event_id          TEXT    PRIMARY KEY,
            event_type        TEXT    NOT NULL,
            entity            TEXT    NOT NULL,
            score             REAL    NOT NULL,
            signals_json      TEXT    NOT NULL,
            evidence_json     TEXT    NOT NULL,
            sec_filed_at      TEXT,
            observed_at       INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS zk_intake_submissions (
            id               TEXT    PRIMARY KEY,
            submission_type  TEXT    NOT NULL,
            content_hash     TEXT    NOT NULL,
            ipfs_cid         TEXT,
            created_at       INTEGER NOT NULL
        );
"""


def init_db(path: str) -> sqlite3.Connection:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.executescript(_SCHEMA_SQL)
    conn.commit()

    cur_ver = conn.execute("PRAGMA user_version").fetchone()[0]
    if cur_ver == 0:
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        conn.commit()
        cur_ver = SCHEMA_VERSION

    for target_ver, sql in MIGRATIONS:
        if cur_ver < target_ver:
            if target_ver == 3:
                _migrate_correlation_events_v3(conn)
            if sql.strip():
                conn.executescript(sql)
            conn.execute(f"PRAGMA user_version = {target_ver}")
            conn.commit()
            cur_ver = target_ver

    return conn


def _column_exists(conn: sqlite3.Connection, table_name: str, column_name: str) -> bool:
    rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    return any(row[1] == column_name for row in rows)


def _add_column_if_missing(
    conn: sqlite3.Connection,
    table_name: str,
    column_name: str,
    column_def: str,
) -> None:
    if not _column_exists(conn, table_name, column_name):
        conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_def}")


def _migrate_correlation_events_v3(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS correlation_events (
            event_id          TEXT    PRIMARY KEY,
            event_type        TEXT    NOT NULL,
            entity            TEXT    NOT NULL,
            score             REAL    NOT NULL,
            signals_json      TEXT    NOT NULL,
            evidence_json     TEXT    NOT NULL,
            sec_filed_at      TEXT,
            observed_at       INTEGER NOT NULL
        )
        """
    )
    _add_column_if_missing(conn, "correlation_events", "event_id", "TEXT")
    _add_column_if_missing(conn, "correlation_events", "event_type", "TEXT")
    _add_column_if_missing(conn, "correlation_events", "entity", "TEXT NOT NULL DEFAULT ''")
    _add_column_if_missing(conn, "correlation_events", "score", "REAL NOT NULL DEFAULT 0")
    _add_column_if_missing(conn, "correlation_events", "signals_json", "TEXT NOT NULL DEFAULT '[]'")
    _add_column_if_missing(
        conn, "correlation_events", "evidence_json", "TEXT NOT NULL DEFAULT '{}'"
    )
    _add_column_if_missing(conn, "correlation_events", "sec_filed_at", "TEXT")
    _add_column_if_missing(conn, "correlation_events", "sec_filing_date", "TEXT")
    _add_column_if_missing(conn, "correlation_events", "observed_at", "INTEGER NOT NULL DEFAULT 0")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_correlation_score ON correlation_events (score)")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_correlation_observed ON correlation_events (observed_at)"
    )


def is_seen(conn: sqlite3.Connection, key: str) -> bool:
    row = conn.execute("SELECT 1 FROM seen WHERE key = ?", (key,)).fetchone()
    return row is not None


def mark_seen(conn: sqlite3.Connection, key: str) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO seen (key, first_seen_at) VALUES (?, ?)",
        (key, int(time.time())),
    )
    conn.commit()


def insert_correlation_event(
    conn: sqlite3.Connection,
    *,
    event_id: str,
    event_type: str,
    entity: str,
    score: float,
    signals: list[str],
    evidence: dict,
    sec_filed_at: str | None,
    observed_at: int,
) -> bool:
    cursor = conn.execute(
        """
        INSERT OR IGNORE INTO correlation_events
            (
                event_id, event_type, entity, score,
                signals_json, evidence_json, sec_filed_at, observed_at
            )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            event_id,
            event_type,
            entity,
            score,
            json.dumps(sorted(set(signals))),
            json.dumps(evidence, sort_keys=True),
            sec_filed_at,
            observed_at,
        ),
    )
    conn.commit()
    return cursor.rowcount > 0


def insert_zk_intake_submission(
    conn: sqlite3.Connection,
    *,
    submission_id: str,
    submission_type: str,
    content_hash: str,
    ipfs_cid: str | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO zk_intake_submissions
            (id, submission_type, content_hash, ipfs_cid, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            submission_id,
            submission_type,
            content_hash,
            ipfs_cid,
            int(time.time()),
        ),
    )
    conn.commit()
