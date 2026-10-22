import contextlib
import hashlib
import json
import sqlite3
import time
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path

from modules.alerts import ALERT_SCHEMA_VERSION
from modules.callsigns import normalize_callsign
from modules.data_paths import resolve_db_path
from modules.graph import GRAPH_SCHEMA_VERSION, GraphEdge, GraphNode
from modules.observation_normalizer import (
    NormalizedEntity,
    NormalizedObservationSet,
    NormalizedRelationship,
)

CALLSIGN_OBSERVATION_SCHEMA_VERSION = 1

DB_PATH = resolve_db_path()


def get_connection():
    return sqlite3.connect(DB_PATH)


def get_connection_row():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def get_readonly_connection():
    """A connection SQLite itself will refuse to write through (LHP-INTEL-02A).

    Two things an ordinary `sqlite3.connect(path)` does that a read must not. It **creates the
    database file** when it is missing — so a read on a machine that has never observed anything
    would leave a database behind. And it permits every write, leaving "this layer is read-only" as
    a promise kept by convention.

    `mode=ro` does neither: a missing database raises, and INSERT / UPDATE / DELETE / CREATE / DROP
    all raise `attempt to write a readonly database`. The guarantee stops being a promise and
    becomes a mechanism.
    """
    conn = sqlite3.connect(f"{Path(DB_PATH).resolve().as_uri()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def execute(query: str, params=()):
    with get_connection() as conn:
        return conn.execute(query, params)


def fetchall(query: str, params=()):
    with get_connection_row() as conn:
        return conn.execute(query, params).fetchall()


def fetchone(query: str, params=()):
    with get_connection_row() as conn:
        return conn.execute(query, params).fetchone()


def readonly_fetchall(query: str, params=()):
    conn = get_readonly_connection()
    try:
        return conn.execute(query, params).fetchall()
    finally:
        conn.close()


def readonly_fetchone(query: str, params=()):
    conn = get_readonly_connection()
    try:
        return conn.execute(query, params).fetchone()
    finally:
        conn.close()


def _since_iso(since_days: int | None) -> str | None:
    if since_days is None:
        return None
    return (datetime.now(UTC) - timedelta(days=since_days)).isoformat()


def _since_epoch(since_days: int | None) -> int | None:
    """Convert a lookback window to a Unix timestamp cutoff (for observed_at INTEGER columns)."""
    if since_days is None:
        return None
    return int((datetime.now(UTC) - timedelta(days=since_days)).timestamp())


def _normalize_entity(value: str | None) -> str | None:
    if not value:
        return None
    return value.strip().lower()


def _row_to_alert_result(row: sqlite3.Row) -> dict:
    return {
        **dict(row),
        "alert": json.loads(row["alert_json"]),
    }


def _get_table_columns(table_name: str) -> set[str]:
    rows = fetchall(f"PRAGMA table_info({table_name})")
    return {row["name"] for row in rows}


def _ensure_alerts_column(name: str, definition: str) -> None:
    columns = _get_table_columns("alerts")
    if name in columns:
        return
    execute(f"ALTER TABLE alerts ADD COLUMN {name} {definition}")


def init_alerts_table():
    execute(
        """
        CREATE TABLE IF NOT EXISTS alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT,
            created_at TEXT,
            alert_json TEXT,
            score REAL,
            score_label TEXT,
            entity TEXT,
            alert_type TEXT,
            schema_version INTEGER DEFAULT 1,
            dedup_hash TEXT
        )
        """
    )
    _ensure_alerts_column("score", "REAL")
    _ensure_alerts_column("score_label", "TEXT")
    _ensure_alerts_column("entity", "TEXT")
    _ensure_alerts_column("alert_type", "TEXT")
    _ensure_alerts_column("schema_version", "INTEGER DEFAULT 1")
    _ensure_alerts_column("dedup_hash", "TEXT")
    execute("CREATE INDEX IF NOT EXISTS idx_alerts_dedup_hash ON alerts (dedup_hash)")


def _compute_dedup_hash(source: str, alert: dict) -> str:
    hashable = {k: v for k, v in alert.items() if k != "provenance"}
    raw = f"{source}:{json.dumps(hashable, sort_keys=True)}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _dedup_hash_from_key(source: str, dedup_key: str) -> str:
    raw = f"{source}:{dedup_key}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def insert_alert(source: str, alert: dict, *, dedup_key: str | None = None) -> bool:
    """Insert an alert row. Returns False (skipping) if an identical alert already exists."""
    entity = alert.get("entity") or alert.get("company") or alert.get("assignee")
    alert_type = alert.get("type") or alert.get("form_type") or alert.get("alert_type")
    schema_version = alert.get("schema_version", ALERT_SCHEMA_VERSION)
    if dedup_key is not None:
        dedup_hash = _dedup_hash_from_key(source, dedup_key)
    else:
        dedup_hash = _compute_dedup_hash(source, alert)

    existing = fetchone("SELECT id FROM alerts WHERE dedup_hash = ?", (dedup_hash,))
    if existing is not None:
        return False

    execute(
        """
        INSERT INTO alerts (
            source, created_at, alert_json, score, score_label, entity, alert_type,
            schema_version, dedup_hash
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            source,
            datetime.now(UTC).isoformat(),
            json.dumps(alert),
            alert.get("score"),
            alert.get("score_label"),
            entity,
            alert_type,
            schema_version,
            dedup_hash,
        ),
    )
    return True


def fetch_alerts(limit: int = 10, since_days: int | None = None):
    cutoff = _since_iso(since_days)
    if cutoff:
        rows = fetchall(
            "SELECT * FROM alerts WHERE created_at >= ? ORDER BY id DESC LIMIT ?",
            (cutoff, limit),
        )
    else:
        rows = fetchall("SELECT * FROM alerts ORDER BY id DESC LIMIT ?", (limit,))
    return [_row_to_alert_result(row) for row in rows]


def fetch_alerts_by_source(source: str, limit: int = 10, since_days: int | None = None):
    cutoff = _since_iso(since_days)
    if cutoff:
        rows = fetchall(
            "SELECT * FROM alerts WHERE source = ? AND created_at >= ? ORDER BY id DESC LIMIT ?",
            (source, cutoff, limit),
        )
    else:
        rows = fetchall(
            "SELECT * FROM alerts WHERE source = ? ORDER BY id DESC LIMIT ?",
            (source, limit),
        )
    return [_row_to_alert_result(row) for row in rows]


def fetch_alerts_min_score(min_score: float, limit: int = 10, since_days: int | None = None):
    cutoff = _since_iso(since_days)
    if cutoff:
        rows = fetchall(
            """
            SELECT * FROM alerts
            WHERE score >= ? AND created_at >= ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (min_score, cutoff, limit),
        )
    else:
        rows = fetchall(
            "SELECT * FROM alerts WHERE score >= ? ORDER BY id DESC LIMIT ?",
            (min_score, limit),
        )
    return [_row_to_alert_result(row) for row in rows]


def fetch_alerts_by_label(label: str, limit: int = 10, since_days: int | None = None):
    cutoff = _since_iso(since_days)
    if cutoff:
        rows = fetchall(
            """
            SELECT * FROM alerts
            WHERE score_label = ? AND created_at >= ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (label, cutoff, limit),
        )
    else:
        rows = fetchall(
            "SELECT * FROM alerts WHERE score_label = ? ORDER BY id DESC LIMIT ?",
            (label, limit),
        )
    return [_row_to_alert_result(row) for row in rows]


def fetch_alerts_by_schema_version(version: int, limit: int = 10, since_days: int | None = None):
    cutoff = _since_iso(since_days)
    if cutoff:
        rows = fetchall(
            """
            SELECT * FROM alerts
            WHERE schema_version = ? AND created_at >= ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (version, cutoff, limit),
        )
    else:
        rows = fetchall(
            "SELECT * FROM alerts WHERE schema_version = ? ORDER BY id DESC LIMIT ?",
            (version, limit),
        )
    return [_row_to_alert_result(row) for row in rows]


def find_related_alerts(entity: str, limit: int = 20, since_days: int | None = None):
    normalized = _normalize_entity(entity)
    if not normalized:
        return []

    cutoff = _since_iso(since_days)
    if cutoff:
        rows = fetchall(
            """
            SELECT * FROM alerts
            WHERE entity IS NOT NULL AND created_at >= ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (cutoff, limit),
        )
    else:
        rows = fetchall(
            "SELECT * FROM alerts WHERE entity IS NOT NULL ORDER BY id DESC LIMIT ?",
            (limit,),
        )

    structured_matches = [
        _row_to_alert_result(row) for row in rows if _normalize_entity(row["entity"]) == normalized
    ]
    if structured_matches:
        return structured_matches[:limit]

    # Back-compat fallback for historical rows where structured entity is absent.
    alerts = fetch_alerts(limit=1000, since_days=since_days)
    related = []
    for a in alerts:
        alert = a["alert"]
        if (
            entity in str(alert.get("company", ""))
            or entity in str(alert.get("assignee", ""))
            or entity in str(alert.get("metadata", ""))
        ):
            related.append(a)

    return related[:limit]


# ---------------------------------------------------------------------------
# Correlation events
# ---------------------------------------------------------------------------


def init_correlation_table() -> None:
    execute(
        """
        CREATE TABLE IF NOT EXISTS correlation_events (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id        TEXT,
            event_type      TEXT    NOT NULL,
            entity          TEXT    NOT NULL DEFAULT '',
            entities_json   TEXT    NOT NULL,
            signals_json    TEXT    NOT NULL DEFAULT '[]',
            evidence_json   TEXT    NOT NULL,
            score           REAL    NOT NULL,
            detected_at     TEXT    NOT NULL,
            observed_at     INTEGER,
            sec_filed_at    TEXT,
            nostr_event_id  TEXT,
            dedup_hash      TEXT
        )
        """
    )
    _ensure_correlation_column("id", "INTEGER")
    _ensure_correlation_column("event_id", "TEXT")
    _ensure_correlation_column("entity", "TEXT NOT NULL DEFAULT ''")
    _ensure_correlation_column("entities_json", "TEXT NOT NULL DEFAULT '[]'")
    _ensure_correlation_column("signals_json", "TEXT NOT NULL DEFAULT '[]'")
    _ensure_correlation_column("evidence_json", "TEXT NOT NULL DEFAULT '{}'")
    _ensure_correlation_column("detected_at", "TEXT")
    _ensure_correlation_column("nostr_event_id", "TEXT")
    _ensure_correlation_column("dedup_hash", "TEXT")
    _ensure_correlation_column("observed_at", "INTEGER")
    _ensure_correlation_column("sec_filed_at", "TEXT")
    _ensure_correlation_column("verification_status", "TEXT NOT NULL DEFAULT 'unverified'")
    _ensure_correlation_column("source_trust_score", "REAL NOT NULL DEFAULT 0.0")
    _ensure_correlation_column("is_single_source", "INTEGER NOT NULL DEFAULT 0")
    _ensure_correlation_column("probation_expires_at", "INTEGER")
    _ensure_correlation_column("promoted_at", "INTEGER")
    _ensure_correlation_column("witness_hash", "TEXT")
    _ensure_correlation_column("witness_signature", "TEXT")
    execute(
        "CREATE INDEX IF NOT EXISTS idx_correlation_probation "
        "ON correlation_events (probation_expires_at) WHERE promoted_at IS NULL"
    )

    # Backfill compatibility fields for rows created before canonical schema.
    execute("UPDATE correlation_events SET id = rowid WHERE id IS NULL")
    escaped_entity = "'[\"' || REPLACE(entity, '\"', '\\\"') || '\"]'"
    execute(
        f"UPDATE correlation_events SET entities_json = {escaped_entity} "
        "WHERE (entities_json IS NULL OR entities_json = '' OR entities_json = '[]') "
        "AND entity IS NOT NULL AND entity != ''"
    )
    execute(
        "UPDATE correlation_events SET detected_at = ? "
        "WHERE detected_at IS NULL OR detected_at = ''",
        (datetime.now(UTC).isoformat(),),
    )
    execute("CREATE INDEX IF NOT EXISTS idx_correlation_dedup ON correlation_events (dedup_hash)")
    execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_correlation_event_id "
        "ON correlation_events (event_id)"
    )
    execute("CREATE INDEX IF NOT EXISTS idx_correlation_score ON correlation_events (score)")
    execute(
        "CREATE INDEX IF NOT EXISTS idx_correlation_detected_at ON correlation_events (detected_at)"
    )


def _ensure_correlation_column(name: str, definition: str) -> None:
    columns = _get_table_columns("correlation_events")
    if name in columns:
        return
    execute(f"ALTER TABLE correlation_events ADD COLUMN {name} {definition}")


def _correlation_dedup_hash(
    event_type: str,
    entities: list[str],
    signals: list[str],
    evidence: object,
    event_id: str | None = None,
) -> str:
    if event_id:
        return hashlib.sha256(f"event:{event_id}".encode()).hexdigest()[:16]
    raw = json.dumps(
        {
            "event_type": event_type,
            "entities": sorted(set(entities)),
            "signals": sorted(set(signals)),
            "evidence": evidence,
        },
        sort_keys=True,
    )
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def insert_correlation(
    event_type: str,
    entities: list[str],
    evidence: dict | list[dict],
    score: float,
    *,
    event_id: str | None = None,
    entity: str | None = None,
    signals: list[str] | None = None,
    sec_filed_at: str | None = None,
    observed_at: int | None = None,
    verification_status: str = "unverified",
    source_trust_score: float = 0.0,
    is_single_source: bool = False,
    probation_expires_at: int | None = None,
    witness_hash: str | None = None,
    witness_signature: str | None = None,
) -> bool:
    """Persist a correlation event. Returns False if an identical event already exists."""
    if not entities:
        entities = [entity] if entity else []
    canonical_entity = entity or (entities[0] if entities else "")
    normalized_signals = sorted(set(signals or []))
    dedup_hash = _correlation_dedup_hash(
        event_type,
        entities,
        normalized_signals,
        evidence,
        event_id=event_id,
    )

    if event_id and fetchone("SELECT id FROM correlation_events WHERE event_id = ?", (event_id,)):
        return False
    if fetchone("SELECT id FROM correlation_events WHERE dedup_hash = ?", (dedup_hash,)):
        return False

    execute(
        """
        INSERT INTO correlation_events
            (
                id, event_id, event_type, entity, entities_json, signals_json,
                evidence_json, score, detected_at, observed_at, sec_filed_at, dedup_hash,
                verification_status, source_trust_score, is_single_source, probation_expires_at,
                witness_hash, witness_signature
            )
        VALUES (
            (SELECT COALESCE(MAX(id), 0) + 1 FROM correlation_events),
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
        )
        """,
        (
            event_id,
            event_type,
            canonical_entity,
            json.dumps(entities),
            json.dumps(normalized_signals),
            json.dumps(evidence),
            score,
            datetime.now(UTC).isoformat(),
            observed_at,
            sec_filed_at,
            dedup_hash,
            verification_status,
            source_trust_score,
            1 if is_single_source else 0,
            probation_expires_at,
            witness_hash,
            witness_signature,
        ),
    )
    return True


def fetch_correlations(
    min_score: float | None = None,
    since_days: int | None = None,
    limit: int = 10,
) -> list[dict]:
    cutoff = _since_iso(since_days)
    conditions: list[str] = []
    params: tuple = ()

    if min_score is not None:
        conditions.append("score >= ?")
        params += (min_score,)
    if cutoff:
        conditions.append("detected_at >= ?")
        params += (cutoff,)

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    params += (limit,)

    rows = fetchall(
        f"SELECT * FROM correlation_events {where} ORDER BY score DESC, id DESC LIMIT ?",
        params,
    )
    results: list[dict] = []
    for row in rows:
        entities = json.loads(row["entities_json"] or "[]")
        if not entities and row["entity"]:
            entities = [row["entity"]]
        results.append(
            {
                **dict(row),
                "entities": entities,
                "signals": json.loads(row["signals_json"] or "[]"),
                "evidence": json.loads(row["evidence_json"] or "{}"),
            }
        )
    return results


def fetch_correlation_by_id(event_id: int | str) -> dict | None:
    if isinstance(event_id, int):
        row = fetchone("SELECT * FROM correlation_events WHERE id = ?", (event_id,))
    else:
        row = fetchone("SELECT * FROM correlation_events WHERE event_id = ?", (event_id,))
    if row is None:
        return None
    entities = json.loads(row["entities_json"] or "[]")
    if not entities and row["entity"]:
        entities = [row["entity"]]
    return {
        **dict(row),
        "entities": entities,
        "signals": json.loads(row["signals_json"] or "[]"),
        "evidence": json.loads(row["evidence_json"] or "{}"),
    }


def fetch_probationary_correlations(since_days: int | None = None) -> list[dict]:
    """Return correlations whose 30-minute probation window has elapsed but are not yet promoted."""
    import time as _time

    now = int(_time.time())
    cutoff = _since_iso(since_days)
    if cutoff:
        rows = fetchall(
            """
            SELECT * FROM correlation_events
            WHERE probation_expires_at <= ? AND promoted_at IS NULL AND detected_at >= ?
            ORDER BY score DESC
            """,
            (now, cutoff),
        )
    else:
        rows = fetchall(
            """
            SELECT * FROM correlation_events
            WHERE probation_expires_at <= ? AND promoted_at IS NULL
            ORDER BY score DESC
            """,
            (now,),
        )
    results: list[dict] = []
    for row in rows:
        entities = json.loads(row["entities_json"] or "[]")
        if not entities and row["entity"]:
            entities = [row["entity"]]
        results.append(
            {
                **dict(row),
                "entities": entities,
                "signals": json.loads(row["signals_json"] or "[]"),
                "evidence": json.loads(row["evidence_json"] or "{}"),
            }
        )
    return results


def promote_correlation(event_id: str, promoted_at: int) -> bool:
    """Mark a probationary correlation as promoted. Returns False if event_id not found."""
    row = fetchone("SELECT id FROM correlation_events WHERE event_id = ?", (event_id,))
    if row is None:
        return False
    execute(
        "UPDATE correlation_events SET promoted_at = ? WHERE event_id = ?",
        (promoted_at, event_id),
    )
    return True


def init_graph_tables() -> None:
    execute(
        """
        CREATE TABLE IF NOT EXISTS graph_nodes (
            node_id TEXT PRIMARY KEY,
            node_type TEXT NOT NULL,
            label TEXT NOT NULL,
            properties_json TEXT NOT NULL,
            schema_version INTEGER NOT NULL
        )
        """
    )
    execute(
        """
        CREATE TABLE IF NOT EXISTS graph_edges (
            edge_id TEXT PRIMARY KEY,
            source_id TEXT NOT NULL,
            target_id TEXT NOT NULL,
            relation TEXT NOT NULL,
            weight REAL NOT NULL,
            properties_json TEXT NOT NULL,
            schema_version INTEGER NOT NULL
        )
        """
    )
    execute("CREATE INDEX IF NOT EXISTS idx_graph_nodes_type ON graph_nodes (node_type)")
    execute("CREATE INDEX IF NOT EXISTS idx_graph_edges_source ON graph_edges (source_id)")
    execute("CREATE INDEX IF NOT EXISTS idx_graph_edges_target ON graph_edges (target_id)")


def upsert_graph_node(node: GraphNode) -> None:
    node.validate()
    execute(
        """
        INSERT INTO graph_nodes
            (node_id, node_type, label, properties_json, schema_version)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(node_id) DO UPDATE SET
            node_type=excluded.node_type,
            label=excluded.label,
            properties_json=excluded.properties_json,
            schema_version=excluded.schema_version
        """,
        (
            node.node_id,
            node.node_type,
            node.label,
            json.dumps(node.properties or {}),
            node.schema_version or GRAPH_SCHEMA_VERSION,
        ),
    )


def upsert_graph_edge(edge: GraphEdge) -> None:
    edge.validate()
    execute(
        """
        INSERT INTO graph_edges
            (edge_id, source_id, target_id, relation, weight, properties_json, schema_version)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(edge_id) DO UPDATE SET
            source_id=excluded.source_id,
            target_id=excluded.target_id,
            relation=excluded.relation,
            weight=excluded.weight,
            properties_json=excluded.properties_json,
            schema_version=excluded.schema_version
        """,
        (
            edge.edge_id,
            edge.source_id,
            edge.target_id,
            edge.relation,
            edge.weight,
            json.dumps(edge.properties or {}),
            edge.schema_version or GRAPH_SCHEMA_VERSION,
        ),
    )


def fetch_graph_node(node_id: str) -> dict | None:
    row = fetchone("SELECT * FROM graph_nodes WHERE node_id = ?", (node_id,))
    if row is None:
        return None
    return {
        **dict(row),
        "properties": json.loads(row["properties_json"]),
    }


def fetch_graph_neighbors(node_id: str, limit: int = 50) -> list[dict]:
    rows = fetchall(
        """
        SELECT * FROM graph_edges
        WHERE source_id = ? OR target_id = ?
        ORDER BY edge_id
        LIMIT ?
        """,
        (node_id, node_id, limit),
    )
    return [
        {
            **dict(row),
            "properties": json.loads(row["properties_json"]),
        }
        for row in rows
    ]


def fetch_graph_neighbors_nodes(node_id: str, limit: int = 50) -> list[dict]:
    edges = fetch_graph_neighbors(node_id=node_id, limit=limit)
    neighbor_ids: set[str] = set()
    for edge in edges:
        source_id = edge["source_id"]
        target_id = edge["target_id"]
        if source_id != node_id:
            neighbor_ids.add(source_id)
        if target_id != node_id:
            neighbor_ids.add(target_id)

    neighbors: list[dict] = []
    for neighbor_id in sorted(neighbor_ids):
        node = fetch_graph_node(neighbor_id)
        if node is not None:
            neighbors.append(node)
    return neighbors


def fetch_graph_edges_between(source_id: str, target_id: str) -> list[dict]:
    rows = fetchall(
        """
        SELECT * FROM graph_edges
        WHERE (source_id = ? AND target_id = ?)
           OR (source_id = ? AND target_id = ?)
        ORDER BY edge_id
        """,
        (source_id, target_id, target_id, source_id),
    )
    return [
        {
            **dict(row),
            "properties": json.loads(row["properties_json"]),
        }
        for row in rows
    ]


def traverse_graph(node_id: str, depth: int = 1, limit: int = 100) -> dict:
    if limit <= 0:
        return {"nodes": [], "edges": []}

    visited_nodes: set[str] = {node_id}
    visited_edges: set[str] = set()
    queue: list[tuple[str, int]] = [(node_id, 0)]

    while queue and len(visited_nodes) < limit:
        current_node_id, current_depth = queue.pop(0)
        if current_depth >= depth:
            continue

        edges = fetch_graph_neighbors(node_id=current_node_id, limit=limit)
        for edge in edges:
            visited_edges.add(edge["edge_id"])
            adjacent_ids = [edge["source_id"], edge["target_id"]]
            for adjacent_id in adjacent_ids:
                if adjacent_id not in visited_nodes:
                    visited_nodes.add(adjacent_id)
                    queue.append((adjacent_id, current_depth + 1))
                    if len(visited_nodes) >= limit:
                        break
            if len(visited_nodes) >= limit:
                break

    nodes = []
    for visited_id in sorted(visited_nodes):
        node = fetch_graph_node(visited_id)
        if node is not None:
            nodes.append(node)

    edges = []
    for edge_id in sorted(visited_edges):
        row = fetchone("SELECT * FROM graph_edges WHERE edge_id = ?", (edge_id,))
        if row is not None:
            edges.append({**dict(row), "properties": json.loads(row["properties_json"])})

    return {"nodes": nodes, "edges": edges}


def _row_to_callsign_observation(row: sqlite3.Row) -> dict:
    return {
        **dict(row),
        "tags": json.loads(row["tags_json"] or "[]"),
        "enrichment": json.loads(row["enrichment_json"] or "{}"),
    }


def init_callsign_history_table() -> None:
    execute(
        """
        CREATE TABLE IF NOT EXISTS callsign_observations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            normalized_callsign TEXT NOT NULL,
            raw_callsign TEXT,
            prefix TEXT,
            category TEXT,
            military_style INTEGER NOT NULL DEFAULT 0,
            confidence REAL NOT NULL DEFAULT 0.0,
            source TEXT NOT NULL,
            facility TEXT,
            entity TEXT,
            icao_hex TEXT,
            lat REAL,
            lon REAL,
            observed_at INTEGER NOT NULL,
            tags_json TEXT NOT NULL DEFAULT '[]',
            enrichment_json TEXT NOT NULL DEFAULT '{}',
            schema_version INTEGER NOT NULL DEFAULT 1,
            created_at INTEGER NOT NULL
        )
        """
    )
    execute(
        """
        CREATE INDEX IF NOT EXISTS idx_callsign_observations_callsign
        ON callsign_observations(normalized_callsign)
        """
    )
    execute(
        """
        CREATE INDEX IF NOT EXISTS idx_callsign_observations_observed_at
        ON callsign_observations(observed_at)
        """
    )
    execute(
        """
        CREATE INDEX IF NOT EXISTS idx_callsign_observations_facility
        ON callsign_observations(facility)
        """
    )
    execute(
        """
        CREATE INDEX IF NOT EXISTS idx_callsign_observations_source
        ON callsign_observations(source)
        """
    )


def insert_callsign_observation(
    *,
    callsign_enrichment: dict,
    source: str,
    facility: str | None = None,
    entity: str | None = None,
    icao_hex: str | None = None,
    lat: float | None = None,
    lon: float | None = None,
    observed_at: int | None = None,
) -> int | None:
    init_callsign_history_table()
    normalized = callsign_enrichment.get("normalized_callsign")
    if not normalized:
        return None

    created_at = int(time.time())
    observed = observed_at if observed_at is not None else created_at
    tags = callsign_enrichment.get("tags") or []
    military_style = int(bool(callsign_enrichment.get("military_style", False)))
    confidence = float(callsign_enrichment.get("confidence", 0.0))
    schema_version = int(
        callsign_enrichment.get("schema_version", CALLSIGN_OBSERVATION_SCHEMA_VERSION)
    )

    with get_connection() as conn:
        cursor = conn.execute(
            """
            INSERT INTO callsign_observations (
                normalized_callsign, raw_callsign, prefix, category,
                military_style, confidence, source, facility, entity,
                icao_hex, lat, lon, observed_at, tags_json, enrichment_json,
                schema_version, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                normalized,
                callsign_enrichment.get("callsign"),
                callsign_enrichment.get("prefix"),
                callsign_enrichment.get("category"),
                military_style,
                confidence,
                source,
                facility,
                entity,
                icao_hex,
                lat,
                lon,
                observed,
                json.dumps(tags),
                json.dumps(callsign_enrichment),
                schema_version,
                created_at,
            ),
        )
        conn.commit()
        return cursor.lastrowid


def fetch_callsign_history(
    normalized_callsign: str,
    limit: int = 50,
) -> list[dict]:
    init_callsign_history_table()
    normalized = normalize_callsign(normalized_callsign)
    if not normalized:
        return []
    rows = fetchall(
        """
        SELECT * FROM callsign_observations
        WHERE normalized_callsign = ?
        ORDER BY observed_at DESC, id DESC
        LIMIT ?
        """,
        (normalized, limit),
    )
    return [_row_to_callsign_observation(row) for row in rows]


def fetch_recent_callsigns(limit: int = 50) -> list[dict]:
    init_callsign_history_table()
    rows = fetchall(
        """
        SELECT * FROM callsign_observations
        ORDER BY observed_at DESC, id DESC
        LIMIT ?
        """,
        (limit,),
    )
    return [_row_to_callsign_observation(row) for row in rows]


def fetch_callsign_activity(
    normalized_callsign: str,
    since_days: int | None = None,
    limit: int = 500,
) -> list[dict]:
    """Fetch all observations for a callsign, ordered oldest-first for temporal analysis."""
    init_callsign_history_table()
    normalized = normalize_callsign(normalized_callsign)
    if not normalized:
        return []
    since = _since_epoch(since_days)
    if since is not None:
        rows = fetchall(
            """
            SELECT * FROM callsign_observations
            WHERE normalized_callsign = ? AND observed_at >= ?
            ORDER BY observed_at ASC, id ASC
            LIMIT ?
            """,
            (normalized, since, limit),
        )
    else:
        rows = fetchall(
            """
            SELECT * FROM callsign_observations
            WHERE normalized_callsign = ?
            ORDER BY observed_at ASC, id ASC
            LIMIT ?
            """,
            (normalized, limit),
        )
    return [_row_to_callsign_observation(row) for row in rows]


def fetch_facility_activity(
    facility: str,
    since_days: int | None = None,
    limit: int = 500,
) -> list[dict]:
    """Fetch all observations at a facility, ordered oldest-first for temporal analysis."""
    init_callsign_history_table()
    if not facility:
        return []
    since = _since_epoch(since_days)
    if since is not None:
        rows = fetchall(
            """
            SELECT * FROM callsign_observations
            WHERE facility = ? AND observed_at >= ?
            ORDER BY observed_at ASC, id ASC
            LIMIT ?
            """,
            (facility, since, limit),
        )
    else:
        rows = fetchall(
            """
            SELECT * FROM callsign_observations
            WHERE facility = ?
            ORDER BY observed_at ASC, id ASC
            LIMIT ?
            """,
            (facility, limit),
        )
    return [_row_to_callsign_observation(row) for row in rows]


def fetch_recent_activity(since_days: int = 7, limit: int = 500) -> list[dict]:
    """Fetch all callsign observations within a recent window, newest first."""
    init_callsign_history_table()
    since = _since_epoch(since_days)
    if since is not None:
        rows = fetchall(
            """
            SELECT * FROM callsign_observations
            WHERE observed_at >= ?
            ORDER BY observed_at DESC, id DESC
            LIMIT ?
            """,
            (since, limit),
        )
    else:
        rows = fetchall(
            """
            SELECT * FROM callsign_observations
            ORDER BY observed_at DESC, id DESC
            LIMIT ?
            """,
            (limit,),
        )
    return [_row_to_callsign_observation(row) for row in rows]


def fetch_recurring_callsigns_summary(
    min_observations: int = 5,
    min_days: int = 3,
    limit: int = 100,
) -> list[dict]:
    """
    SQL-aggregated summary of callsigns exceeding observation and active-day thresholds.

    Read-only. Returns normalized_callsign, obs_count, day_count, first_ts, last_ts,
    any_military. Caller can use this for fast recurring-callsign discovery without
    fetching full history rows.
    """
    init_callsign_history_table()
    rows = fetchall(
        """
        SELECT
            normalized_callsign,
            COUNT(*) AS obs_count,
            COUNT(DISTINCT date(observed_at, 'unixepoch')) AS day_count,
            MIN(observed_at) AS first_ts,
            MAX(observed_at) AS last_ts,
            MAX(military_style) AS any_military
        FROM callsign_observations
        GROUP BY normalized_callsign
        HAVING obs_count >= ? AND day_count >= ?
        ORDER BY obs_count DESC
        LIMIT ?
        """,
        (min_observations, min_days, limit),
    )
    return [dict(row) for row in rows]


def fetch_callsign_summary(limit: int = 50) -> list[dict]:
    init_callsign_history_table()
    rows = fetchall(
        """
        WITH ranked AS (
            SELECT
                normalized_callsign,
                prefix,
                category,
                military_style,
                source,
                facility,
                observed_at,
                COUNT(*) OVER (PARTITION BY normalized_callsign) AS count,
                MIN(observed_at) OVER (PARTITION BY normalized_callsign) AS first_seen,
                MAX(observed_at) OVER (PARTITION BY normalized_callsign) AS last_seen,
                ROW_NUMBER() OVER (
                    PARTITION BY normalized_callsign
                    ORDER BY observed_at DESC, id DESC
                ) AS rn
            FROM callsign_observations
        )
        SELECT
            normalized_callsign,
            prefix,
            category,
            military_style,
            count,
            first_seen,
            last_seen,
            facility AS latest_facility,
            source AS latest_source
        FROM ranked
        WHERE rn = 1
        ORDER BY count DESC, last_seen DESC
        LIMIT ?
        """,
        (limit,),
    )
    return [dict(row) for row in rows]


# -- normalized observations (LHP-INTEL-01B) ------------------------------------------------
#
# A separate storage boundary from `alerts` and the graph, and deliberately so. An alert carries a
# score and a label because it is a triage surface; a graph edge carries a weight because it claims
# a strength. Both of those are *opinions*, and the intelligence foundation must be able to record
# "this repository contains a Dockerfile" without asserting that it matters. So these tables store
# facts and nothing else: there is no score column, no weight column, and no priority column, and
# because the vocabulary of an attribute key is the closed observation vocabulary, there is nowhere
# for one to be added later without changing a contract.
#
# Insert-only. Re-observing an unchanged repository reproduces the same digests, so it is stored
# once — that is what the observation layer's timestamp-free digest was for.


def init_observation_tables() -> None:
    execute(
        """
        CREATE TABLE IF NOT EXISTS observation_sets (
            normalization_digest TEXT PRIMARY KEY,
            source_observation_set_id TEXT NOT NULL,
            source_artifact_digest TEXT NOT NULL,
            source_schema_version INTEGER NOT NULL,
            scope_type TEXT NOT NULL,
            scope_id TEXT NOT NULL,
            scope_path TEXT NOT NULL,
            derivation_version TEXT NOT NULL,
            schema_version INTEGER NOT NULL,
            serialization_version INTEGER NOT NULL,
            observed_at TEXT NOT NULL,
            stored_at TEXT NOT NULL,
            undetermined_json TEXT NOT NULL,
            artifact_json TEXT NOT NULL,
            lighthouse_version TEXT,
            profile_id TEXT
        )
        """
    )
    execute(
        """
        CREATE TABLE IF NOT EXISTS normalized_entities (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            entity_id TEXT NOT NULL,
            entity_type TEXT NOT NULL,
            value TEXT NOT NULL,
            attributes_json TEXT NOT NULL,
            scope_id TEXT NOT NULL,
            source_observation_set_id TEXT NOT NULL,
            source_artifact_digest TEXT NOT NULL,
            source_observations_json TEXT NOT NULL,
            derivation_version TEXT NOT NULL,
            observed_at TEXT NOT NULL,
            schema_version INTEGER NOT NULL,
            digest TEXT NOT NULL
        )
        """
    )
    execute(
        """
        CREATE TABLE IF NOT EXISTS normalized_relationships (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            relationship_id TEXT NOT NULL,
            relationship_type TEXT NOT NULL,
            subject_id TEXT NOT NULL,
            object_id TEXT NOT NULL,
            value TEXT NOT NULL,
            scope_id TEXT NOT NULL,
            source_observation_set_id TEXT NOT NULL,
            source_artifact_digest TEXT NOT NULL,
            source_observations_json TEXT NOT NULL,
            derivation_version TEXT NOT NULL,
            observed_at TEXT NOT NULL,
            schema_version INTEGER NOT NULL,
            digest TEXT NOT NULL
        )
        """
    )
    execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_normalized_entity_identity "
        "ON normalized_entities (entity_id, source_artifact_digest, derivation_version)"
    )
    execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_normalized_relationship_identity "
        "ON normalized_relationships (relationship_id, source_artifact_digest, derivation_version)"
    )
    execute("CREATE INDEX IF NOT EXISTS idx_observation_sets_scope ON observation_sets (scope_id)")
    execute(
        "CREATE INDEX IF NOT EXISTS idx_normalized_entities_type "
        "ON normalized_entities (entity_type)"
    )
    execute(
        "CREATE INDEX IF NOT EXISTS idx_normalized_entities_scope ON normalized_entities (scope_id)"
    )
    execute(
        "CREATE INDEX IF NOT EXISTS idx_normalized_relationships_subject "
        "ON normalized_relationships (subject_id)"
    )


def insert_observation_set(result: NormalizedObservationSet) -> bool:
    """Record one normalized observation. Returns False if this exact content is already stored."""
    result.validate()  # fail closed at the persistence boundary
    existing = fetchone(
        "SELECT 1 FROM observation_sets WHERE normalization_digest = ?",
        (result.normalization_digest,),
    )
    if existing is not None:
        return False

    execute(
        """
        INSERT INTO observation_sets (
            normalization_digest, source_observation_set_id, source_artifact_digest,
            source_schema_version, scope_type, scope_id, scope_path, derivation_version,
            schema_version, serialization_version, observed_at, stored_at, undetermined_json,
            artifact_json, lighthouse_version, profile_id
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            result.normalization_digest,
            result.source_observation_set_id,
            result.source_artifact_digest,
            result.source_schema_version,
            result.scope_type,
            result.scope_id,
            result.scope_path,
            result.derivation_version,
            result.schema_version,
            result.serialization_version,
            result.observed_at,
            datetime.now(UTC).isoformat(),
            json.dumps(sorted(result.undetermined_observations)),
            json.dumps(result.source_artifact, sort_keys=True),
            result.lighthouse_version,
            result.profile_id,
        ),
    )
    return True


def insert_normalized_entity(entity: NormalizedEntity) -> bool:
    """Insert one normalized entity. `to_dict()` validates, so a malformed or tampered record
    cannot reach the database at all."""
    payload = entity.to_dict()
    existing = fetchone(
        "SELECT 1 FROM normalized_entities "
        "WHERE entity_id = ? AND source_artifact_digest = ? AND derivation_version = ?",
        (
            payload["entity_id"],
            payload["source_artifact_digest"],
            payload["derivation_version"],
        ),
    )
    if existing is not None:
        return False

    execute(
        """
        INSERT INTO normalized_entities (
            entity_id, entity_type, value, attributes_json, scope_id,
            source_observation_set_id, source_artifact_digest, source_observations_json,
            derivation_version, observed_at, schema_version, digest
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            payload["entity_id"],
            payload["entity_type"],
            payload["value"],
            json.dumps(payload["attributes"], sort_keys=True),
            payload["scope_id"],
            payload["source_observation_set_id"],
            payload["source_artifact_digest"],
            json.dumps(payload["source_observations"]),
            payload["derivation_version"],
            payload["observed_at"],
            payload["schema_version"],
            payload["digest"],
        ),
    )
    return True


def insert_normalized_relationship(relationship: NormalizedRelationship) -> bool:
    """Insert one normalized relationship. Validates before writing, as entities do."""
    payload = relationship.to_dict()
    existing = fetchone(
        "SELECT 1 FROM normalized_relationships "
        "WHERE relationship_id = ? AND source_artifact_digest = ? AND derivation_version = ?",
        (
            payload["relationship_id"],
            payload["source_artifact_digest"],
            payload["derivation_version"],
        ),
    )
    if existing is not None:
        return False

    execute(
        """
        INSERT INTO normalized_relationships (
            relationship_id, relationship_type, subject_id, object_id, value, scope_id,
            source_observation_set_id, source_artifact_digest, source_observations_json,
            derivation_version, observed_at, schema_version, digest
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            payload["relationship_id"],
            payload["relationship_type"],
            payload["subject_id"],
            payload["object_id"],
            payload["value"],
            payload["scope_id"],
            payload["source_observation_set_id"],
            payload["source_artifact_digest"],
            json.dumps(payload["source_observations"]),
            payload["derivation_version"],
            payload["observed_at"],
            payload["schema_version"],
            payload["digest"],
        ),
    )
    return True


def store_normalized_observation(result: NormalizedObservationSet) -> bool:
    """Persist a normalized observation and everything it normalized into.

    Idempotent by content identity: an unchanged repository always produces the same
    `normalization_digest`, so observing it a hundred times stores it once. Returns False when the
    content is already present — and writes nothing at all in that case, because the store is
    insert-only and never repairs, overwrites, or re-times an existing row.
    """
    if not insert_observation_set(result):
        return False
    for entity in result.entities:
        insert_normalized_entity(entity)
    for relationship in result.relationships:
        insert_normalized_relationship(relationship)
    return True


# -- reading them back (LHP-INTEL-02A) ------------------------------------------------------
#
# Every function below goes through `get_readonly_connection()`, so SQLite itself refuses the write.
# The ORDER BY clauses carry a tiebreak because the obvious sort keys are not unique: two sets can
# share a `stored_at`, and one entity observed in two artifacts is two rows with the same
# `entity_id`. Without the tiebreak the output would not be reproducible, which the read surface
# promises that it is.

_OBSERVATION_TABLES = ("observation_sets", "normalized_entities", "normalized_relationships")


def observation_tables_exist() -> bool:
    """True when the observation store is present. A read: it never creates the database."""
    if not Path(DB_PATH).exists():
        return False
    rows = readonly_fetchall(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name IN (?, ?, ?)",
        _OBSERVATION_TABLES,
    )
    return len(rows) == len(_OBSERVATION_TABLES)


def fetch_observation_sets(
    scope_id: str | None = None,
    limit: int = 50,
    *,
    scope_type: str | None = None,
    digest: str | None = None,
    since: str | None = None,
    until: str | None = None,
) -> list[dict]:
    clauses: list[str] = []
    params: list[object] = []
    for column, value in (
        ("scope_id", scope_id),
        ("scope_type", scope_type),
        ("normalization_digest", digest),
    ):
        if value:
            clauses.append(f"{column} = ?")
            params.append(value)
    # Stored timestamps are UTC ISO-8601, so a lexicographic comparison is a chronological one.
    if since:
        clauses.append("observed_at >= ?")
        params.append(since)
    if until:
        clauses.append("observed_at <= ?")
        params.append(until)
    where = f"WHERE {' AND '.join(clauses)} " if clauses else ""
    params.append(limit)
    rows = readonly_fetchall(
        f"SELECT * FROM observation_sets {where}"
        "ORDER BY stored_at DESC, normalization_digest ASC LIMIT ?",
        tuple(params),
    )
    return [dict(row) for row in rows]


def fetch_observation_set(normalization_digest: str) -> dict | None:
    row = readonly_fetchone(
        "SELECT * FROM observation_sets WHERE normalization_digest = ?",
        (normalization_digest,),
    )
    return dict(row) if row is not None else None


def fetch_observation_set_digests_by_prefix(prefix: str, limit: int = 100) -> list[str]:
    """Resolve an abbreviated id. The caller validates that `prefix` is hex, so it carries no LIKE
    metacharacter — and every match is returned, because an ambiguous id must name its candidates
    rather than quietly pick one."""
    rows = readonly_fetchall(
        "SELECT normalization_digest FROM observation_sets "
        "WHERE normalization_digest LIKE ? ORDER BY normalization_digest LIMIT ?",
        (f"{prefix}%", limit),
    )
    return [row["normalization_digest"] for row in rows]


def fetch_scope_ids_by_prefix(prefix: str, limit: int = 100) -> list[str]:
    """Resolve an abbreviated scope id (LHP-INTEL-02B). A scope id is `sha256(canonical abs path)`
    for BOTH kinds of scope, so — unlike an entity id — it is uniformly a digest, and 02A's
    abbreviation rule applies to it unchanged. The caller validates that `prefix` is hex, so it
    carries no LIKE metacharacter, and every match is returned: an ambiguous id names its candidates
    rather than quietly picking one."""
    rows = readonly_fetchall(
        "SELECT DISTINCT scope_id FROM observation_sets "
        "WHERE scope_id LIKE ? ORDER BY scope_id LIMIT ?",
        (f"{prefix}%", limit),
    )
    return [row["scope_id"] for row in rows]


def fetch_scope_entity_ids(scope_id: str, limit: int = 50_000) -> list[str]:
    """Every distinct entity directly observed in one scope (LHP-INTEL-02B).

    An id projection rather than a row fetch, because the set algebra of an intersection runs on
    identities: full rows are then read only for the entities that survive it. Index-supported by
    `idx_normalized_entities_scope`, and bounded — the caller discloses a truncated scan rather than
    quietly returning a smaller intersection than the store actually holds.
    """
    rows = readonly_fetchall(
        "SELECT DISTINCT entity_id FROM normalized_entities "
        "WHERE scope_id = ? ORDER BY entity_id ASC LIMIT ?",
        (scope_id, limit),
    )
    return [row["entity_id"] for row in rows]


def fetch_normalized_entities(
    entity_type: str | None = None,
    scope_id: str | None = None,
    limit: int = 100,
    *,
    source_artifact_digest: str | None = None,
    entity_ids: Sequence[str] | None = None,
) -> list[dict]:
    clauses: list[str] = []
    params: list[object] = []
    for column, value in (
        ("entity_type", entity_type),
        ("scope_id", scope_id),
        ("source_artifact_digest", source_artifact_digest),
    ):
        if value:
            clauses.append(f"{column} = ?")
            params.append(value)
    if entity_ids:
        wanted = tuple(entity_ids)
        clauses.append(f"entity_id IN ({', '.join('?' for _ in wanted)})")
        params.extend(wanted)
    where = f"WHERE {' AND '.join(clauses)} " if clauses else ""
    params.append(limit)
    rows = readonly_fetchall(
        f"SELECT * FROM normalized_entities {where}"
        # Total, because the unique index is the triple: the same entity observed in two artifacts
        # is two rows, and two derivation versions of one artifact would be two more. Ordering by
        # anything less is a result set that can legally come back in a different order tomorrow.
        "ORDER BY entity_id ASC, source_artifact_digest ASC, derivation_version ASC LIMIT ?",
        tuple(params),
    )
    return [dict(row) for row in rows]


def fetch_normalized_relationships(
    subject_id: str | None = None,
    relationship_type: str | None = None,
    limit: int = 100,
    *,
    source_artifact_digest: str | None = None,
) -> list[dict]:
    clauses: list[str] = []
    params: list[object] = []
    for column, value in (
        ("subject_id", subject_id),
        ("relationship_type", relationship_type),
        ("source_artifact_digest", source_artifact_digest),
    ):
        if value:
            clauses.append(f"{column} = ?")
            params.append(value)
    where = f"WHERE {' AND '.join(clauses)} " if clauses else ""
    params.append(limit)
    rows = readonly_fetchall(
        f"SELECT * FROM normalized_relationships {where}"
        "ORDER BY relationship_id ASC, source_artifact_digest ASC, derivation_version ASC LIMIT ?",
        tuple(params),
    )
    return [dict(row) for row in rows]


def fetch_relationships_for_entities(
    entity_ids: Sequence[str], *, scope_ids: Sequence[str] | None = None, limit: int = 2_000
) -> list[dict]:
    """Every direct relationship in which any of these entities is the subject OR the object.

    One bounded query for a whole command, never one per occurrence. `object_id` has no index, so
    this is a covering-index scan of `normalized_relationships` — walked in `relationship_id` order,
    which is also the order it is returned in, so the ORDER BY costs nothing extra. Correctness does
    not depend on an index; only speed would, and the caller caps and discloses the scan.
    """
    if not entity_ids:
        return []

    entities = tuple(entity_ids)
    marks = ", ".join("?" for _ in entities)
    clauses = [f"(subject_id IN ({marks}) OR object_id IN ({marks}))"]
    params: list[object] = [*entities, *entities]
    if scope_ids:
        scopes = tuple(scope_ids)
        clauses.append(f"scope_id IN ({', '.join('?' for _ in scopes)})")
        params.extend(scopes)
    params.append(limit)

    rows = readonly_fetchall(
        f"SELECT * FROM normalized_relationships WHERE {' AND '.join(clauses)} "
        "ORDER BY relationship_id ASC, source_artifact_digest ASC, derivation_version ASC LIMIT ?",
        tuple(params),
    )
    return [dict(row) for row in rows]


def count_observation_sets_by_scope_type() -> dict[str, int]:
    rows = readonly_fetchall(
        "SELECT scope_type, COUNT(*) AS total FROM observation_sets GROUP BY scope_type"
    )
    return {row["scope_type"]: row["total"] for row in rows}


def count_normalized_entities() -> int:
    return readonly_fetchone("SELECT COUNT(*) AS total FROM normalized_entities")["total"]


def count_distinct_normalized_entities() -> int:
    """How many distinct things are known, as opposed to how many times they were observed."""
    return readonly_fetchone("SELECT COUNT(DISTINCT entity_id) AS total FROM normalized_entities")[
        "total"
    ]


def count_normalized_entities_by_type() -> dict[str, int]:
    rows = readonly_fetchall(
        "SELECT entity_type, COUNT(*) AS total FROM normalized_entities GROUP BY entity_type"
    )
    return {row["entity_type"]: row["total"] for row in rows}


def count_normalized_relationships() -> int:
    return readonly_fetchone("SELECT COUNT(*) AS total FROM normalized_relationships")["total"]


def count_normalized_relationships_by_type() -> dict[str, int]:
    rows = readonly_fetchall(
        "SELECT relationship_type, COUNT(*) AS total FROM normalized_relationships "
        "GROUP BY relationship_type"
    )
    return {row["relationship_type"]: row["total"] for row in rows}


def fetch_distinct_entity_ids_by_type(entity_type: str, limit: int = 10_000) -> list[str]:
    """Every distinct entity of one type, in identity order.

    Backed by `idx_normalized_entities_type`, so no new index was needed for the detector to
    enumerate what it reasons about (`EXPLAIN QUERY PLAN`: SEARCH USING INDEX
    idx_normalized_entities_type). Distinct, because one entity observed in five scopes is five rows
    and one identity.
    """
    rows = readonly_fetchall(
        "SELECT DISTINCT entity_id FROM normalized_entities WHERE entity_type = ? "
        "ORDER BY entity_id ASC LIMIT ?",
        (entity_type, limit),
    )
    return [row["entity_id"] for row in rows]


def fetch_observation_set_by_artifact(scope_id: str, source_artifact_digest: str) -> dict | None:
    """The observation set a scope recorded from one exact source artifact.

    Keyed by `(scope_id, source_artifact_digest)` because a scope re-observed after a change holds
    two artifacts, and an artifact digest alone does not say which scope looked at it. `scope_id` is
    indexed, so this is a lookup rather than a scan.
    """
    row = readonly_fetchone(
        "SELECT * FROM observation_sets WHERE scope_id = ? AND source_artifact_digest = ? "
        "ORDER BY normalization_digest ASC LIMIT 1",
        (scope_id, source_artifact_digest),
    )
    return dict(row) if row is not None else None


# -- pattern candidates (LHP-INTEL-03B) ----------------------------------------------------
#
# Two tables, and they are INSERT-ONLY. There is no `update_*` and no `delete_*` here, deliberately:
# a candidate is an immutable review object, and an operator who reviewed one version must be able
# to trust that it is still the thing they reviewed. New evidence produces a new VERSION sharing the
# candidate_key — never an edit to the row somebody already read.
#
# The tables are created LAZILY, on the first insert. `CREATE TABLE` is itself a write, so calling
# it eagerly would mean a dry run — or a read against a machine that has never detected anything —
# left a database behind. That would quietly undo the guarantee 01E and 02A were built for.

_PATTERN_TABLES = ("pattern_candidates", "pattern_candidate_dispositions")


def init_pattern_candidate_tables() -> None:
    execute(
        """
        CREATE TABLE IF NOT EXISTS pattern_candidates (
            candidate_id TEXT PRIMARY KEY,
            candidate_key TEXT NOT NULL,
            schema_version INTEGER NOT NULL,
            profile_id TEXT NOT NULL,
            detector_id TEXT NOT NULL,
            detector_version TEXT NOT NULL,
            candidate_type TEXT NOT NULL,
            artifact_digest TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL,
            candidate_json TEXT NOT NULL
        )
        """
    )
    execute(
        """
        CREATE TABLE IF NOT EXISTS pattern_candidate_dispositions (
            disposition_id TEXT PRIMARY KEY,
            candidate_id TEXT NOT NULL,
            candidate_artifact_digest TEXT NOT NULL,
            schema_version INTEGER NOT NULL,
            decision TEXT NOT NULL,
            operator_id TEXT NOT NULL,
            reason TEXT NOT NULL,
            review_method TEXT NOT NULL,
            operator_identity_authenticated INTEGER NOT NULL,
            artifact_digest TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL,
            disposition_json TEXT NOT NULL
        )
        """
    )
    # Composite, and in exactly the order `list` sorts by — so the default listing is served from
    # the index instead of a temp B-tree, and `WHERE candidate_key = ?` still uses it by the
    # leftmost-prefix rule. One index, two named queries.
    execute(
        "CREATE INDEX IF NOT EXISTS idx_pattern_candidates_key "
        "ON pattern_candidates (candidate_key, created_at, candidate_id)"
    )
    execute(
        "CREATE INDEX IF NOT EXISTS idx_pattern_candidates_profile "
        "ON pattern_candidates (profile_id)"
    )
    execute(
        "CREATE INDEX IF NOT EXISTS idx_pattern_candidates_detector "
        "ON pattern_candidates (detector_id)"
    )
    execute(
        "CREATE INDEX IF NOT EXISTS idx_pattern_candidates_type "
        "ON pattern_candidates (candidate_type)"
    )
    # ONE disposition per exact candidate version, and SQLite is what enforces it. A service-side
    # check alone would be a rule kept by whoever remembered to call it.
    #
    # There is deliberately NO index on `decision`. `--disposition` filters the decisions of the
    # candidates already being listed, so the lookup is always by candidate_id and no query ever
    # issues `WHERE decision = ?`. EXPLAIN QUERY PLAN confirmed it was dead weight; an index that
    # backs no named query is a guess about a query somebody might write one day.
    execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_pattern_disposition_candidate "
        "ON pattern_candidate_dispositions (candidate_id)"
    )


def pattern_candidate_tables_exist() -> bool:
    """True when the candidate store is present. A read: it never creates the database."""
    if not Path(DB_PATH).exists():
        return False
    rows = readonly_fetchall(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name IN (?, ?)",
        _PATTERN_TABLES,
    )
    return len(rows) == len(_PATTERN_TABLES)


def insert_pattern_candidate(candidate) -> bool:
    """Store one candidate version. Returns False when this exact version is already present.

    `to_dict()` validates, so a malformed or tampered candidate cannot reach the database — and the
    identity is the artifact digest, so an unchanged detection over unchanged facts reproduces the
    same row rather than a second one.
    """
    payload = candidate.to_dict()
    existing = fetchone(
        "SELECT 1 FROM pattern_candidates WHERE candidate_id = ?",
        (payload["candidate_id"],),
    )
    if existing is not None:
        return False

    execute(
        """
        INSERT INTO pattern_candidates (
            candidate_id, candidate_key, schema_version, profile_id, detector_id,
            detector_version, candidate_type, artifact_digest, created_at, candidate_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            payload["candidate_id"],
            payload["candidate_key"],
            payload["schema_version"],
            payload["profile_id"],
            payload["detector_id"],
            payload["detector_version"],
            payload["candidate_type"],
            payload["artifact_digest"],
            payload["created_at"],
            json.dumps(payload, sort_keys=True),
        ),
    )
    return True


def insert_pattern_disposition(disposition) -> None:
    """Record one operator decision about one exact candidate version.

    No return value and no "already present" branch: a second disposition on the same version is an
    error the caller must see, not a silent no-op. The UNIQUE index raises it.
    """
    payload = disposition.to_dict()
    execute(
        """
        INSERT INTO pattern_candidate_dispositions (
            disposition_id, candidate_id, candidate_artifact_digest, schema_version, decision,
            operator_id, reason, review_method, operator_identity_authenticated, artifact_digest,
            created_at, disposition_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            payload["disposition_id"],
            payload["candidate_id"],
            payload["candidate_artifact_digest"],
            payload["schema_version"],
            payload["decision"],
            payload["operator_id"],
            payload["reason"],
            payload["review_method"],
            int(payload["operator_identity_authenticated"]),
            payload["artifact_digest"],
            payload["created_at"],
            json.dumps(payload, sort_keys=True),
        ),
    )


def fetch_pattern_candidates(
    *,
    profile_id: str | None = None,
    detector_id: str | None = None,
    candidate_type: str | None = None,
    candidate_key: str | None = None,
    limit: int = 500,
) -> list[dict]:
    """Candidate rows in identity order — never in an order derived from their evidence.

    `(candidate_key, created_at, candidate_id)` is total: two versions of one key cannot share a
    candidate_id, because the id IS the content digest. Sorting by how many scopes a candidate
    recurs in would be a ranking, and rankings do not belong in a fact-review surface.
    """
    clauses: list[str] = []
    params: list[object] = []
    for column, value in (
        ("profile_id", profile_id),
        ("detector_id", detector_id),
        ("candidate_type", candidate_type),
        ("candidate_key", candidate_key),
    ):
        if value:
            clauses.append(f"{column} = ?")
            params.append(value)
    where = f"WHERE {' AND '.join(clauses)} " if clauses else ""
    params.append(limit)
    rows = readonly_fetchall(
        f"SELECT * FROM pattern_candidates {where}"
        "ORDER BY candidate_key ASC, created_at ASC, candidate_id ASC LIMIT ?",
        tuple(params),
    )
    return [dict(row) for row in rows]


def fetch_pattern_candidate(candidate_id: str) -> dict | None:
    row = readonly_fetchone(
        "SELECT * FROM pattern_candidates WHERE candidate_id = ?", (candidate_id,)
    )
    return dict(row) if row is not None else None


def fetch_pattern_candidate_ids_by_prefix(prefix: str, limit: int = 100) -> list[str]:
    """Resolve an abbreviated candidate id. The caller has already proven `prefix` is hex, so it
    carries no LIKE metacharacter — and every match comes back, because an ambiguous id must name
    its candidates rather than pick one."""
    rows = readonly_fetchall(
        "SELECT candidate_id FROM pattern_candidates WHERE candidate_id LIKE ? "
        "ORDER BY candidate_id ASC LIMIT ?",
        (f"pc_{prefix}%", limit),
    )
    return [row["candidate_id"] for row in rows]


def fetch_pattern_disposition(candidate_id: str) -> dict | None:
    row = readonly_fetchone(
        "SELECT * FROM pattern_candidate_dispositions WHERE candidate_id = ?",
        (candidate_id,),
    )
    return dict(row) if row is not None else None


def fetch_pattern_dispositions(candidate_ids: Sequence[str], limit: int = 500) -> list[dict]:
    if not candidate_ids:
        return []
    wanted = tuple(candidate_ids)
    rows = readonly_fetchall(
        f"SELECT * FROM pattern_candidate_dispositions "
        f"WHERE candidate_id IN ({', '.join('?' for _ in wanted)}) "
        "ORDER BY candidate_id ASC LIMIT ?",
        (*wanted, limit),
    )
    return [dict(row) for row in rows]


# -- public-source check ledger (LHP-INTEL-04B) --------------------------------------------------
# An append-only event log of "Lighthouse checked this public locator at this time, and this
# direct outcome occurred". Unlike the observation store it is NEVER deduplicated — a repeated
# identical outcome at a different time is a distinct row, because cadence is the evidence. There is
# no UPDATE and no DELETE for either table.

_PUBLIC_SOURCE_TABLES = ("public_source_check_runs", "public_source_checks")


def _create_public_source_tables(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS public_source_check_runs (
            run_id TEXT PRIMARY KEY,
            schema_version TEXT NOT NULL,
            started_at TEXT NOT NULL,
            completed_at TEXT NOT NULL,
            adapter_id TEXT NOT NULL,
            adapter_version TEXT NOT NULL,
            policy_digest TEXT NOT NULL,
            target_count INTEGER NOT NULL,
            completed_check_count INTEGER NOT NULL,
            artifact_digest TEXT NOT NULL UNIQUE,
            run_json TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS public_source_checks (
            check_id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL,
            source_id TEXT NOT NULL,
            locator_id TEXT NOT NULL,
            checked_at TEXT NOT NULL,
            outcome TEXT NOT NULL,
            http_status INTEGER,
            content_digest TEXT,
            adapter_id TEXT NOT NULL,
            adapter_version TEXT NOT NULL,
            artifact_digest TEXT NOT NULL UNIQUE,
            check_json TEXT NOT NULL
        )
        """
    )
    # Each index backs a named read; none backs a ranking. Verified with EXPLAIN QUERY PLAN.
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_public_source_checks_run ON public_source_checks (run_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_public_source_checks_source "
        "ON public_source_checks (source_id, checked_at)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_public_source_checks_locator "
        "ON public_source_checks (locator_id, checked_at)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_public_source_checks_content "
        "ON public_source_checks (content_digest)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_public_source_runs_completed "
        "ON public_source_check_runs (completed_at, run_id)"
    )


def init_public_source_tables() -> None:
    """Create the ledger tables. Normally created inside `store_check_run`'s transaction instead, so
    a failed run leaves nothing behind — this exists for explicit setup and tests."""
    conn = get_connection()
    try:
        with conn:
            _create_public_source_tables(conn)
    finally:
        conn.close()


def public_source_tables_exist() -> bool:
    """True when the ledger is present. A read: it never creates the database."""
    if not Path(DB_PATH).exists():
        return False
    rows = readonly_fetchall(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name IN (?, ?)",
        _PUBLIC_SOURCE_TABLES,
    )
    return len(rows) == len(_PUBLIC_SOURCE_TABLES)


def store_check_run(*, run: dict, checks: list[dict]) -> None:
    """Persist one completed run and all its checks in a SINGLE atomic transaction.

    The tables are created inside the same transaction, so a run that fails to persist leaves no
    table and no row — the store is exactly as it was found. `run`/`checks` are validated artifact
    dicts (`to_dict()` output); the full artifact is kept verbatim in the `*_json` column.
    """
    conn = get_connection()
    try:
        with conn:
            _create_public_source_tables(conn)
            conn.execute(
                """
                INSERT INTO public_source_check_runs (
                    run_id, schema_version, started_at, completed_at, adapter_id, adapter_version,
                    policy_digest, target_count, completed_check_count, artifact_digest, run_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run["run_id"],
                    run["schema_version"],
                    run["started_at"],
                    run["completed_at"],
                    run["adapter_id"],
                    run["adapter_version"],
                    run["policy_digest"],
                    run["target_count"],
                    run["completed_check_count"],
                    run["artifact_digest"],
                    json.dumps(run, sort_keys=True),
                ),
            )
            for check in checks:
                conn.execute(
                    """
                    INSERT INTO public_source_checks (
                        check_id, run_id, source_id, locator_id, checked_at, outcome, http_status,
                        content_digest, adapter_id, adapter_version, artifact_digest, check_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        check["check_id"],
                        check["run_id"],
                        check["source_id"],
                        check["locator_id"],
                        check["checked_at"],
                        check["outcome"],
                        check["http_status"],
                        check["content_digest"],
                        check["adapter_id"],
                        check["adapter_version"],
                        check["artifact_digest"],
                        json.dumps(check, sort_keys=True),
                    ),
                )
    finally:
        conn.close()


def fetch_public_source_runs(*, limit: int = 500) -> list[dict]:
    rows = readonly_fetchall(
        "SELECT * FROM public_source_check_runs ORDER BY completed_at DESC, run_id DESC LIMIT ?",
        (limit,),
    )
    return [dict(row) for row in rows]


def fetch_public_source_run(run_id: str) -> dict | None:
    row = readonly_fetchone("SELECT * FROM public_source_check_runs WHERE run_id = ?", (run_id,))
    return dict(row) if row is not None else None


def fetch_public_source_run_ids_by_prefix(prefix: str, limit: int = 100) -> list[str]:
    """Resolve an abbreviated run id. `prefix` is already proven hex, so it carries no LIKE
    metacharacter; every match returns, because an ambiguous id must name its runs, not pick one."""
    rows = readonly_fetchall(
        "SELECT run_id FROM public_source_check_runs WHERE run_id LIKE ? "
        "ORDER BY run_id ASC LIMIT ?",
        (f"psr_{prefix}%", limit),
    )
    return [row["run_id"] for row in rows]


def fetch_public_source_check(check_id: str) -> dict | None:
    row = readonly_fetchone("SELECT * FROM public_source_checks WHERE check_id = ?", (check_id,))
    return dict(row) if row is not None else None


def fetch_public_source_check_ids_by_prefix(prefix: str, limit: int = 100) -> list[str]:
    rows = readonly_fetchall(
        "SELECT check_id FROM public_source_checks WHERE check_id LIKE ? "
        "ORDER BY check_id ASC LIMIT ?",
        (f"psc_{prefix}%", limit),
    )
    return [row["check_id"] for row in rows]


def fetch_public_source_locator_index(*, limit: int = 1000) -> list[dict]:
    """Distinct (source_id, locator_id) pairs in the ledger — the set of locators to evaluate.

    Read-only and bounded. This is a ``SELECT DISTINCT`` scan whose correctness depends on no index;
    the caller treats a full page as possibly truncated and discloses that, rather than silently
    dropping a source. Returns nothing when the ledger has never been created.
    """
    if not public_source_tables_exist():
        return []
    rows = readonly_fetchall(
        "SELECT DISTINCT source_id, locator_id FROM public_source_checks "
        "ORDER BY source_id, locator_id LIMIT ?",
        (limit,),
    )
    return [dict(row) for row in rows]


def fetch_public_source_checks(
    *,
    run_id: str | None = None,
    source_id: str | None = None,
    locator_id: str | None = None,
    content_digest: str | None = None,
    ascending: bool = True,
    limit: int = 500,
) -> list[dict]:
    """Checks in temporal order, with the full check id as a stable tiebreak — never by count.

    The ordering is total (a check id is unique), so a repeated identical outcome stays visible and
    a recovery reads in sequence. Filters are ANDed; the service supplies exactly one.
    """
    clauses: list[str] = []
    params: list[object] = []
    for column, value in (
        ("run_id", run_id),
        ("source_id", source_id),
        ("locator_id", locator_id),
        ("content_digest", content_digest),
    ):
        if value:
            clauses.append(f"{column} = ?")
            params.append(value)
    where = f"WHERE {' AND '.join(clauses)} " if clauses else ""
    direction = "ASC" if ascending else "DESC"
    params.append(limit)
    rows = readonly_fetchall(
        f"SELECT * FROM public_source_checks {where}"
        f"ORDER BY checked_at {direction}, check_id {direction} LIMIT ?",
        tuple(params),
    )
    return [dict(row) for row in rows]


# -- Active profile state (LHP-V1-PROFILE-01C) ---------------------------------------------
# Singleton row: exactly one active profile, or the table is absent (no_active_profile).
# Created lazily inside the activation transaction — a read-only status check never creates it.

_ACTIVE_PROFILE_TABLE = "active_profile_state"


def active_profile_table_exists() -> bool:
    """True when the active-profile table is present. A read: never creates the database."""
    if not Path(DB_PATH).exists():
        return False
    rows = readonly_fetchall(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?",
        (_ACTIVE_PROFILE_TABLE,),
    )
    return len(rows) == 1


def fetch_active_profile_rows() -> list[dict]:
    """Every row in the active-profile table (expect 0 or 1). Read-only; never creates."""
    if not active_profile_table_exists():
        return []
    rows = readonly_fetchall(
        "SELECT id, state_json, artifact_digest FROM active_profile_state ORDER BY id"
    )
    return [dict(row) for row in rows]


def _activate_active_profile_atomic(
    state_json: str,
    artifact_digest: str,
    *,
    profile_id: str,
    profile_version: int,
    profile_sha256: str,
) -> str:
    """Insert-or-keep the singleton active-profile row inside one BEGIN IMMEDIATE transaction.

    Idempotency key is ``(profile_id, profile_version, profile_sha256)`` — not the full
    artifact digest, because ``activated_at`` is digest-covered and a repeat confirmation
    must not rewrite the original activation time. A different identity raises
    ``unsupported_profile_switch``. Concurrent callers serialize on the immediate lock;
    the CHECK (id = 1) constraint makes a second distinct row unrepresentable.
    """
    path = Path(DB_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, isolation_level=None)  # manual transactions
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS active_profile_state (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                state_json TEXT NOT NULL,
                artifact_digest TEXT NOT NULL UNIQUE
            )
            """
        )
        existing = conn.execute(
            "SELECT state_json, artifact_digest FROM active_profile_state WHERE id = 1"
        ).fetchone()
        if existing is not None:
            try:
                prior = json.loads(existing[0])
            except (TypeError, json.JSONDecodeError) as exc:
                conn.execute("ROLLBACK")
                raise RuntimeError(f"corrupt_active_profile_state: {exc}") from exc
            same_identity = (
                prior.get("active_profile_id") == profile_id
                and prior.get("active_profile_version") == profile_version
                and prior.get("active_profile_sha256") == profile_sha256
            )
            if same_identity:
                conn.execute("COMMIT")
                return "already_present"
            conn.execute("ROLLBACK")
            raise RuntimeError(
                "unsupported_profile_switch: an active profile already exists "
                "with a different identity"
            )
        conn.execute(
            "INSERT INTO active_profile_state (id, state_json, artifact_digest) VALUES (1, ?, ?)",
            (state_json, artifact_digest),
        )
        conn.execute("COMMIT")
        return "inserted"
    except BaseException:
        with contextlib.suppress(sqlite3.Error):
            conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()
