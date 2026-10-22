from __future__ import annotations

import json
import logging
from typing import Any

from modules import db
from modules.correlations import CORRELATION_SCHEMA_VERSION, CorrelationRecord
from modules.graph import GraphEdge, GraphNode
from modules.graph_builder import build_graph_from_correlation

logger = logging.getLogger(__name__)


def correlation_row_to_record(row: dict[str, Any]) -> CorrelationRecord:
    """Convert a correlation_events row dict into a validated CorrelationRecord."""
    signals = row.get("signals")
    if signals is None:
        raw = row.get("signals_json")
        signals = json.loads(raw) if raw else []

    evidence = row.get("evidence")
    if evidence is None:
        raw = row.get("evidence_json")
        evidence = json.loads(raw) if raw else {}
    if not isinstance(evidence, dict):
        evidence = {}

    event_id = (row.get("event_id") or "").strip()
    if not event_id:
        event_id = f"correlation:{row.get('id', '')}"

    event_type = row.get("event_type") or "correlation"

    entity = (row.get("entity") or "").strip()
    if not entity:
        entities = row.get("entities")
        if not entities:
            raw = row.get("entities_json")
            entities = json.loads(raw) if raw else []
        entity = entities[0] if entities else "unknown"

    observed_at = row.get("observed_at")
    if observed_at is None:
        observed_at = row.get("detected_at") or row.get("created_at")
    if observed_at is not None and not isinstance(observed_at, str):
        observed_at = str(observed_at)

    record = CorrelationRecord(
        event_id=event_id,
        event_type=event_type,
        entity=entity,
        score=float(row.get("score", 0)),
        signals=list(signals) if signals else [],
        evidence=evidence,
        observed_at=observed_at,
        schema_version=row.get("schema_version", CORRELATION_SCHEMA_VERSION),
        verification_status=row.get("verification_status", "unverified"),
        source_trust_score=float(row.get("source_trust_score", 0.0)),
        is_single_source=bool(row.get("is_single_source", False)),
        witness_hash=row.get("witness_hash"),
        witness_signature=row.get("witness_signature"),
    )
    record.validate()
    return record


def persist_graph(
    graph: dict[str, list[dict[str, Any]]],
    dry_run: bool = False,
) -> dict[str, int]:
    """Validate and optionally upsert graph nodes and edges."""
    nodes = graph.get("nodes", [])
    edges = graph.get("edges", [])

    for node_data in nodes:
        GraphNode.from_dict(node_data)
    for edge_data in edges:
        GraphEdge.from_dict(edge_data)

    if not dry_run:
        db.init_graph_tables()
        for node_data in nodes:
            db.upsert_graph_node(GraphNode.from_dict(node_data))
        for edge_data in edges:
            db.upsert_graph_edge(GraphEdge.from_dict(edge_data))

    return {"nodes": len(nodes), "edges": len(edges)}


def backfill_correlation_graphs(
    limit: int = 100,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Backfill graph nodes/edges from historical correlation_events rows."""
    logger.info(
        "Starting graph backfill (limit=%d, dry_run=%s)",
        limit,
        dry_run,
    )

    rows = db.fetch_correlations(limit=limit)
    success_count = 0
    failed_count = 0
    total_nodes = 0
    total_edges = 0
    errors: list[dict[str, str]] = []

    for row in rows:
        correlation_key = (row.get("event_id") or "").strip() or f"correlation:{row.get('id', '')}"
        try:
            record = correlation_row_to_record(row)
            graph = build_graph_from_correlation(record)
            counts = persist_graph(graph, dry_run=dry_run)
            success_count += 1
            total_nodes += counts["nodes"]
            total_edges += counts["edges"]
        except Exception as exc:
            failed_count += 1
            error_msg = str(exc)
            errors.append({"correlation": correlation_key, "error": error_msg})
            logger.warning(
                "Graph backfill failed for correlation %s: %s",
                correlation_key,
                error_msg,
            )

    result = {
        "dry_run": dry_run,
        "requested_limit": limit,
        "correlations_seen": len(rows),
        "correlations_backfilled": success_count,
        "correlations_failed": failed_count,
        "nodes": total_nodes,
        "edges": total_edges,
        "errors": errors,
    }

    logger.info(
        "Graph backfill complete: seen=%d backfilled=%d failed=%d nodes=%d edges=%d dry_run=%s",
        result["correlations_seen"],
        result["correlations_backfilled"],
        result["correlations_failed"],
        result["nodes"],
        result["edges"],
        dry_run,
    )
    return result
