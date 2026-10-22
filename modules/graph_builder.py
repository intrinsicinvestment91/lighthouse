from __future__ import annotations

from modules.callsigns import enrich_callsign
from modules.correlations import CorrelationRecord
from modules.graph import GraphEdge, GraphNode


def _extract_callsign_sightings(evidence: dict) -> list[dict]:
    """Return {callsign, facility, signal} dicts from flight and adsb evidence rows."""
    sightings: list[dict] = []
    for row in evidence.get("flight") or []:
        cs_raw = (row.get("callsign") or "").strip()
        if cs_raw:
            sightings.append(
                {
                    "callsign": cs_raw,
                    "facility": (row.get("facility") or "").strip() or None,
                    "signal": "flight",
                }
            )
    for row in evidence.get("adsb") or []:
        cs_raw = (row.get("callsign") or "").strip()
        if cs_raw:
            sightings.append(
                {
                    "callsign": cs_raw,
                    "facility": (row.get("facility") or "").strip() or None,
                    "signal": "adsb",
                }
            )
    return sightings


def _build_callsign_graph_elements(
    sightings: list[dict],
    event_id: str,
    temporal_context: dict,
) -> tuple[list[GraphNode], list[GraphEdge]]:
    """Build callsign/facility/prefix nodes and edges; deduplicates by node_id."""
    nodes: dict[str, GraphNode] = {}
    edges: dict[str, GraphEdge] = {}

    by_callsign: dict[str, list[dict]] = {}
    for s in sightings:
        enrichment = enrich_callsign(s["callsign"])
        normalized = enrichment.normalized_callsign
        if not normalized:
            continue
        if normalized not in by_callsign:
            by_callsign[normalized] = []
        by_callsign[normalized].append({**s, "_enrichment": enrichment})

    for normalized_cs, cs_sightings in sorted(by_callsign.items()):
        enrichment = cs_sightings[0]["_enrichment"]
        temporal = temporal_context.get(normalized_cs) or {}

        props: dict = {
            "normalized_callsign": normalized_cs,
            "military_style": enrichment.military_style,
        }
        if enrichment.prefix:
            props["known_prefix"] = enrichment.prefix
        if temporal.get("first_seen"):
            props["first_seen"] = temporal["first_seen"]
        if temporal.get("last_seen"):
            props["last_seen"] = temporal["last_seen"]
        if temporal.get("prior_observations") is not None:
            props["observation_count"] = temporal["prior_observations"]

        cs_node_id = f"callsign:{normalized_cs}"
        nodes[cs_node_id] = GraphNode(
            node_id=cs_node_id,
            node_type="callsign",
            label=normalized_cs,
            properties=props,
        )

        # participated_in: callsign → correlation event
        p_edge_id = f"{cs_node_id}->event:{event_id}"
        edges[p_edge_id] = GraphEdge(
            edge_id=p_edge_id,
            source_id=cs_node_id,
            target_id=f"event:{event_id}",
            relation="participated_in",
        )

        # observed_via: callsign → signal (one per unique signal, deduped)
        for signal in sorted({s["signal"] for s in cs_sightings if s["signal"]}):
            edge_id = f"{cs_node_id}->signal:{signal}"
            edges[edge_id] = GraphEdge(
                edge_id=edge_id,
                source_id=cs_node_id,
                target_id=f"signal:{signal}",
                relation="observed_via",
            )

        # observed_near: callsign → facility (one per unique facility, deduped)
        for facility in sorted({s["facility"] for s in cs_sightings if s["facility"]}):
            fac_node_id = f"facility:{facility}"
            if fac_node_id not in nodes:
                nodes[fac_node_id] = GraphNode(
                    node_id=fac_node_id,
                    node_type="facility",
                    label=facility,
                )
            edge_id = f"{cs_node_id}->{fac_node_id}"
            edges[edge_id] = GraphEdge(
                edge_id=edge_id,
                source_id=cs_node_id,
                target_id=fac_node_id,
                relation="observed_near",
            )

        # has_prefix: only for known military prefixes (deterministic, low-noise)
        if enrichment.military_style and enrichment.prefix:
            pfx_node_id = f"prefix:{enrichment.prefix}"
            if pfx_node_id not in nodes:
                nodes[pfx_node_id] = GraphNode(
                    node_id=pfx_node_id,
                    node_type="callsign_prefix",
                    label=enrichment.prefix,
                    properties={"category": enrichment.category or ""},
                )
            pfx_edge_id = f"{cs_node_id}->{pfx_node_id}"
            edges[pfx_edge_id] = GraphEdge(
                edge_id=pfx_edge_id,
                source_id=cs_node_id,
                target_id=pfx_node_id,
                relation="has_prefix",
            )

    return list(nodes.values()), list(edges.values())


def build_graph_from_correlation(record: CorrelationRecord) -> dict:
    entity_node = GraphNode(
        node_id=f"entity:{record.entity}",
        node_type="entity",
        label=record.entity,
    )
    event_node = GraphNode(
        node_id=f"event:{record.event_id}",
        node_type="event",
        label=record.event_type,
        properties={"score": record.score},
    )

    nodes: list[GraphNode] = [entity_node, event_node]
    edges: list[GraphEdge] = [
        GraphEdge(
            edge_id=f"{entity_node.node_id}->{event_node.node_id}",
            source_id=entity_node.node_id,
            target_id=event_node.node_id,
            relation="involved_in",
            weight=record.score / 10.0,
        )
    ]

    unique_signals = sorted(set(record.signals or []))
    for signal in unique_signals:
        signal_node = GraphNode(
            node_id=f"signal:{signal}",
            node_type="signal",
            label=signal,
        )
        nodes.append(signal_node)
        edges.append(
            GraphEdge(
                edge_id=f"{signal_node.node_id}->{event_node.node_id}",
                source_id=signal_node.node_id,
                target_id=event_node.node_id,
                relation="contributed_to",
            )
        )

    # Build persistent callsign graph entities from evidence
    evidence = record.evidence or {}
    sightings = _extract_callsign_sightings(evidence)
    if sightings:
        temporal_context = evidence.get("temporal_context") or {}
        cs_nodes, cs_edges = _build_callsign_graph_elements(
            sightings, record.event_id, temporal_context
        )
        nodes.extend(cs_nodes)
        edges.extend(cs_edges)

    for node in nodes:
        node.validate()
    for edge in edges:
        edge.validate()

    return {
        "nodes": [node.to_dict() for node in nodes],
        "edges": [edge.to_dict() for edge in edges],
    }
