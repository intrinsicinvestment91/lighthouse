"""Deterministic, rule-based explanations for graph traversal results."""

from __future__ import annotations


def _signal_phrase(raw_label: str) -> str:
    normalized = (raw_label or "").strip().lower()
    mapping = {
        "sec": "SEC filing",
        "adsb": "ADSB activity",
        "flight": "flight activity",
        "narrative": "narrative shift",
    }
    return mapping.get(normalized, raw_label or "unknown signal")


def _join_items(items: list[str]) -> str:
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    if len(items) == 2:
        return f"{items[0]} and {items[1]}"
    return f"{', '.join(items[:-1])}, and {items[-1]}"


def _format_score(score: object) -> str | None:
    if isinstance(score, int | float):
        return f"{score:.2f}".rstrip("0").rstrip(".")
    return None


def explain_graph_result(graph: dict) -> str:
    """Return a deterministic, human-readable explanation of graph nodes and edges."""
    nodes = graph.get("nodes") or []
    edges = graph.get("edges") or []

    if not nodes and not edges:
        return "Graph has no nodes or edges to explain."

    nodes_by_id = {
        node.get("node_id"): node
        for node in nodes
        if isinstance(node, dict) and node.get("node_id")
    }

    event_to_entities: dict[str, set[str]] = {}
    event_to_signals: dict[str, set[str]] = {}

    for edge in edges:
        if not isinstance(edge, dict):
            continue
        source_id = edge.get("source_id")
        target_id = edge.get("target_id")
        relation = edge.get("relation")
        if not source_id or not target_id or not relation:
            continue

        source_node = nodes_by_id.get(source_id) or {}
        target_node = nodes_by_id.get(target_id) or {}
        source_type = source_node.get("node_type")
        target_type = target_node.get("node_type")

        if relation == "involved_in" and source_type == "entity" and target_type == "event":
            event_to_entities.setdefault(target_id, set()).add(
                source_node.get("label") or source_id
            )
        elif relation == "contributed_to" and source_type == "signal" and target_type == "event":
            event_to_signals.setdefault(target_id, set()).add(
                _signal_phrase(source_node.get("label") or "")
            )

    explained_events = sorted(set(event_to_entities) | set(event_to_signals))
    if not explained_events:
        return (
            f"Graph contains {len(nodes)} node(s) and {len(edges)} edge(s). "
            "No explainable entity-event relationships were found."
        )

    lines: list[str] = []
    for event_id in explained_events:
        event_node = nodes_by_id.get(event_id) or {}
        event_props = event_node.get("properties") or {}
        event_score = _format_score(event_props.get("score"))

        entities = sorted(event_to_entities.get(event_id, set()))
        if entities:
            subject = "Entity" if len(entities) == 1 else "Entities"
            entity_text = _join_items(entities)
            sentence = f"{subject} {entity_text} is involved in event {event_id}"
            if len(entities) > 1:
                sentence = f"{subject} {entity_text} are involved in event {event_id}"
        else:
            sentence = f"Event {event_id} is present in the graph"

        if event_score is not None:
            sentence += f" with score {event_score}"
        lines.append(f"{sentence}.")

        signals = sorted(event_to_signals.get(event_id, set()))
        if signals:
            lines.append(f"Signals contributing to this event include {_join_items(signals)}.")

    return " ".join(lines)
