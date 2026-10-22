"""
Lighthouse MCP Server — exposes the pipeline as a first-class MCP server.

Transport: stdio (default).  Start with:
    lighthouse-mcp
    # or directly:
    python -m portal.mcp_server

For development with the MCP inspector:
    make mcp-dev
"""

import hashlib
import hmac
import logging
import os

try:
    from mcp.server.fastmcp import Context, FastMCP
except ModuleNotFoundError:
    # Test/CI fallback when optional mcp package is unavailable.
    # Supports only the subset used by unit tests (tool/resource registration and invocation).
    import json
    from dataclasses import dataclass

    Context = object  # type: ignore[assignment]

    @dataclass
    class _TextResult:
        text: str

    @dataclass
    class _ResourceResult:
        content: str

    class FastMCP:  # type: ignore[override]
        def __init__(self, name: str, instructions: str = "") -> None:
            self.name = name
            self.instructions = instructions
            self._tools: dict[str, callable] = {}
            self._resources: dict[str, callable] = {}

        def tool(self):
            def _decorator(fn):
                self._tools[fn.__name__] = fn
                return fn

            return _decorator

        def resource(self, uri: str):
            def _decorator(fn):
                self._resources[uri] = fn
                return fn

            return _decorator

        async def call_tool(self, name: str, arguments: dict):
            fn = self._tools[name]
            result = await fn(**arguments)
            return [_TextResult(text=json.dumps(result))]

        async def read_resource(self, uri: str):
            fn = self._resources[uri]
            result = await fn()
            return [_ResourceResult(content=result)]

        def run(self, transport: str = "stdio") -> None:
            raise RuntimeError(
                "MCP runtime unavailable (missing mcp package). "
                f"Cannot run transport={transport!r}."
            )


logger = logging.getLogger(__name__)

mcp = FastMCP(
    name="lighthouse",
    instructions=(
        "Lighthouse is an OSINT archival pipeline for public-domain aerospace and energy data. "
        "Use the available tools to query patents, flight observations, researcher activity, "
        "velocity anomalies, archived pages, and Dead Man's Switch registrations."
    ),
)


# ---------------------------------------------------------------------------
# Phase 1: health ping
# ---------------------------------------------------------------------------


@mcp.tool()
async def ping() -> dict:
    """Return server status. Use this to verify the MCP server is reachable."""
    import time

    from config import app_cfg, narrative_cfg, nostr_cfg

    return {
        "status": "ok",
        "server": "lighthouse-mcp",
        "timestamp": int(time.time()),
        "nostr_configured": bool(nostr_cfg.private_key_hex),
        "llm_configured": bool(narrative_cfg.anthropic_api_key),
        "db_path": app_cfg.state_db_path,
    }


# ---------------------------------------------------------------------------
# Phase 3: config resources (read-only, no side effects)
# ---------------------------------------------------------------------------


@mcp.resource("config://patent-classes")
async def resource_patent_classes() -> str:
    """IPC/CPC classification codes the pipeline currently monitors."""
    import json

    from config import patent_cfg

    return json.dumps(patent_cfg.classes, indent=2)


@mcp.resource("config://assignees")
async def resource_assignees() -> str:
    """Assignees of interest used to flag patents (partial-match, case-insensitive)."""
    import json

    from config import patent_cfg

    return json.dumps(patent_cfg.assignees_of_interest, indent=2)


@mcp.resource("config://facilities")
async def resource_facilities() -> str:
    """Facilities monitored by the flight tracker, with lat/lon and radius."""
    import json

    from config import flight_cfg

    return json.dumps(flight_cfg.facilities, indent=2)


@mcp.resource("config://archive-targets")
async def resource_archive_targets() -> str:
    """Agency newsroom targets for the Wayback Archiver, with keywords and CSS selectors."""
    import json

    from config import archive_cfg

    return json.dumps(archive_cfg.targets, indent=2)


@mcp.resource("state://schema")
async def resource_state_schema() -> str:
    """Full SQLite schema of data/state.db (all 7 tables)."""
    conn = _open_db()
    rows = conn.execute(
        "SELECT name, sql FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall()
    return "\n\n".join(f"-- {name}\n{sql};" for name, sql in rows if sql)


# ---------------------------------------------------------------------------
# Phase 2: read tools (no side effects)
# ---------------------------------------------------------------------------


def _open_db():
    """Return an initialised DB connection, creating all tables if absent."""
    from config import app_cfg
    from modules.state_db import init_db

    return init_db(app_cfg.state_db_path)


def _rows_as_dicts(conn, sql: str, params: tuple = ()) -> list[dict]:
    cur = conn.execute(sql, params)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row, strict=False)) for row in cur.fetchall()]


@mcp.tool()
async def query_patents(
    ipc_class: str | None = None,
    since_days: int | None = None,
    flagged_only: bool = False,
) -> list[dict]:
    """
    Return patent records from the most recent pipeline run.

    Args:
        ipc_class: Filter by IPC class prefix (e.g. "G21B", "C22C"). Case-insensitive.
        since_days: Only include patents filed within this many days of today.
        flagged_only: If True, return only patents flagged as matching watched assignees.
    """
    import json
    import time
    from pathlib import Path

    from config import patent_cfg

    out_dir = Path(patent_cfg.output_dir)
    files = sorted(out_dir.glob("patents_*.json"), reverse=True)
    if not files:
        return []

    records: list[dict] = json.loads(files[0].read_text())

    if flagged_only:
        records = [r for r in records if r.get("_flagged")]

    if ipc_class:
        prefix = ipc_class.upper()
        records = [r for r in records if (r.get("ipc") or "").upper().startswith(prefix)]

    if since_days is not None:
        cutoff_ts = time.time() - since_days * 86400
        # patent "date" is ISO-8601 string — compare as string (YYYY-MM-DD sorts correctly)
        import datetime

        cutoff_date = datetime.datetime.fromtimestamp(cutoff_ts).strftime("%Y-%m-%d")
        records = [r for r in records if (r.get("date") or "") >= cutoff_date]

    return records


@mcp.tool()
async def query_flights(
    facility: str | None = None,
    since_hours: int = 24,
) -> list[dict]:
    """
    Return unusual flight observations from the state database.

    Args:
        facility: Filter by facility name substring (e.g. "DARPA", "Helion"). Case-insensitive.
        since_hours: Only return observations from the last N hours (default 24).
    """
    import time

    conn = _open_db()
    cutoff = int(time.time()) - since_hours * 3600
    sql = "SELECT * FROM flight_observations WHERE observed_at >= ?"
    params: tuple = (cutoff,)

    if facility:
        sql += " AND LOWER(facility) LIKE ?"
        params += (f"%{facility.lower()}%",)

    sql += " ORDER BY observed_at DESC"
    return _rows_as_dicts(conn, sql, params)


@mcp.tool()
async def query_researchers(
    name: str | None = None,
    silent_only: bool = False,
) -> list[dict]:
    """
    Return arXiv researcher records from the state database.

    Args:
        name: Filter by researcher name substring. Case-insensitive.
        silent_only: If True, return only researchers who have triggered a silence alert.
    """
    conn = _open_db()
    sql = (
        "SELECT name, institution, last_pub_date, first_silence_at, notified"
        " FROM researchers WHERE 1=1"
    )
    params: tuple = ()

    if name:
        sql += " AND LOWER(name) LIKE ?"
        params += (f"%{name.lower()}%",)

    if silent_only:
        sql += " AND first_silence_at IS NOT NULL"

    sql += " ORDER BY name"
    return _rows_as_dicts(conn, sql, params)


@mcp.tool()
async def query_velocity(
    ipc_class: str | None = None,
) -> list[dict]:
    """
    Return weekly patent filing counts from the state database.

    Args:
        ipc_class: Filter by exact IPC class (e.g. "G21B"). If omitted, returns all classes.
    """
    conn = _open_db()
    if ipc_class:
        sql = "SELECT * FROM patent_velocity WHERE ipc_class = ? ORDER BY week DESC"
        params: tuple = (ipc_class.upper(),)
    else:
        sql = "SELECT * FROM patent_velocity ORDER BY ipc_class, week DESC"
        params = ()

    return _rows_as_dicts(conn, sql, params)


@mcp.tool()
async def list_archived_pages(
    agency: str | None = None,
    keyword: str | None = None,
) -> list[dict]:
    """
    Return archived government pages from the most recent archiver run.

    Args:
        agency: Filter by agency name (e.g. "DoD", "NASA", "DoE"). Case-insensitive.
        keyword: Only include pages whose matched_keywords list contains this string.
                 Case-insensitive substring match.
    """
    import json
    from pathlib import Path

    from config import archive_cfg

    out_dir = Path(archive_cfg.output_dir)
    files = sorted(out_dir.glob("manifest_*.json"), reverse=True)
    if not files:
        return []

    pages: list[dict] = json.loads(files[0].read_text())

    if agency:
        agency_lower = agency.lower()
        pages = [p for p in pages if (p.get("agency") or "").lower() == agency_lower]

    if keyword:
        kw_lower = keyword.lower()
        pages = [
            p
            for p in pages
            if any(kw_lower in k.lower() for k in (p.get("matched_keywords") or []))
        ]

    # Omit local_path — not useful to an agent, and path strings leak host layout
    for p in pages:
        p.pop("local_path", None)

    return pages


@mcp.tool()
async def get_dms_registrations(
    active_only: bool = True,
) -> list[dict]:
    """
    Return Dead Man's Switch registrations from the state database.
    encrypted_message is omitted from results.

    Args:
        active_only: If True (default), return only active (non-revoked) registrations.
    """
    import time

    conn = _open_db()
    sql = (
        "SELECT id, display_name, interval_hours, last_checkin_at, nostr_pubkey, active "
        "FROM dms_registrations"
    )
    params: tuple = ()

    if active_only:
        sql += " WHERE active = 1"

    sql += " ORDER BY last_checkin_at DESC"
    rows = _rows_as_dicts(conn, sql, params)

    now = int(time.time())
    for r in rows:
        r["next_deadline"] = r["last_checkin_at"] + r["interval_hours"] * 3600
        r["overdue"] = now > r["next_deadline"]

    return rows


@mcp.tool()
async def query_correlations(
    min_score: float | None = None,
    since_days: int | None = None,
    limit: int = 10,
) -> list[dict]:
    """
    Return cross-source correlation events from the correlation_events table.

    Args:
        min_score: Only return events with score >= this value (1.0–10.0).
        since_days: Rolling time window in days.
        limit: Maximum results to return (default 10).
    """
    from modules import db

    db.init_correlation_table()
    return db.fetch_correlations(min_score=min_score, since_days=since_days, limit=limit)


@mcp.tool()
async def explain_correlation(event_id: int | str) -> dict:
    """
    Return the full evidence breakdown for a single correlation event.

    Args:
        event_id: Correlation row id (int) or deterministic event_id (str).
    """
    from modules import db

    db.init_correlation_table()
    result = db.fetch_correlation_by_id(event_id)
    if result is None:
        return {"error": f"No correlation event found with id={event_id}"}
    return result


@mcp.tool()
async def query_alerts(
    source: str | None = None,
    min_score: float | None = None,
    label: str | None = None,
    entity: str | None = None,
    limit: int = 10,
    since_days: int | None = None,
) -> list[dict]:
    """
    Return scored alerts from any signal source.

    Filter by source (velocity_tracker | sec_sentinel | patent_sentinel |
    arxiv_sentinel | narrative_analyzer | flight_tracker | adsb_sentinel),
    min_score (0.0–1.0), label (low | medium | high), entity name substring,
    or since_days (rolling time window). Returns alert dicts with score,
    score_label, and provenance fields.
    """
    from modules import db

    db.init_alerts_table()
    if entity:
        rows = db.find_related_alerts(entity, limit=limit, since_days=since_days)
    elif source:
        rows = db.fetch_alerts_by_source(source, limit=limit, since_days=since_days)
    elif min_score is not None:
        rows = db.fetch_alerts_min_score(min_score, limit=limit, since_days=since_days)
    elif label:
        rows = db.fetch_alerts_by_label(label, limit=limit, since_days=since_days)
    else:
        rows = db.fetch_alerts(limit=limit, since_days=since_days)
    return [row["alert"] for row in rows]


@mcp.tool()
async def get_entity_graph(
    entity: str,
    depth: int = 1,
    limit: int = 100,
) -> dict:
    """
    Return graph nodes and edges connected to an entity.

    Args:
        entity: Entity label, e.g. "Tesla".
        depth: Traversal depth.
        limit: Maximum nodes/edges to return.
    """
    from modules import db

    limit = max(1, min(limit, 100))
    depth = max(0, min(depth, 3))

    db.init_graph_tables()

    node_id = f"entity:{entity}"
    return db.traverse_graph(node_id=node_id, depth=depth, limit=limit)


@mcp.tool()
async def explain_entity_graph(
    entity: str,
    depth: int = 1,
    limit: int = 100,
) -> dict:
    """
    Return a human-readable explanation of the graph connected to an entity.

    Fetches the entity's graph (same as get_entity_graph) and produces a
    deterministic natural-language summary of entity-event relationships and
    contributing signals. No LLM is used.

    Args:
        entity: Entity label, e.g. "Tesla".
        depth: Traversal depth (max 3).
        limit: Maximum nodes/edges to traverse (max 100).
    """
    from modules import db
    from modules.graph_explainer import explain_graph_result

    limit = max(1, min(limit, 100))
    depth = max(0, min(depth, 3))

    db.init_graph_tables()
    node_id = f"entity:{entity}"
    graph = db.traverse_graph(node_id=node_id, depth=depth, limit=limit)
    explanation = explain_graph_result(graph)

    return {
        "entity": entity,
        "node_count": len(graph.get("nodes") or []),
        "edge_count": len(graph.get("edges") or []),
        "explanation": explanation,
    }


@mcp.tool()
async def get_graph_neighbors(
    node_id: str,
    limit: int = 50,
) -> list[dict]:
    """
    Return graph neighbor nodes for a node id.

    Args:
        node_id: Graph node id, e.g. "entity:Tesla".
        limit: Maximum neighbors to return.
    """
    from modules import db

    limit = max(1, min(limit, 100))

    db.init_graph_tables()
    return db.fetch_graph_neighbors_nodes(node_id=node_id, limit=limit)


@mcp.tool()
async def get_graph_edges_between(
    source_id: str,
    target_id: str,
) -> list[dict]:
    """
    Return graph edges between two nodes in either direction.

    Args:
        source_id: Source graph node id.
        target_id: Target graph node id.
    """
    from modules import db

    db.init_graph_tables()
    return db.fetch_graph_edges_between(source_id=source_id, target_id=target_id)


# ---------------------------------------------------------------------------
# Phase 4: pipeline trigger tools (guarded — dry_run defaults True)
# ---------------------------------------------------------------------------

_SINGLE_MODULES = [
    "patents",
    "velocity",
    "arxiv",
    "archive",
    "narrative",
    "flight",
    "nostr",
    "dms-check",
]


def _expected_approval_token() -> str | None:
    """Return expected approval token when a human gate secret is configured."""
    secret = os.environ.get("LIGHTHOUSE_APPROVAL_SECRET", "").strip()
    if not secret:
        return None
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()[:24]


def _is_human_approved(approval_token: str | None) -> bool:
    """Fail-closed human-approval gate for live (non-dry-run) MCP actions.

    Returns True ONLY when a non-empty approval secret is configured AND the
    supplied token matches the derived expected token via a timing-safe
    comparison. Approval is denied on any uncertainty:

    - approval secret missing / empty / whitespace-only
    - approval token missing / empty / whitespace-only / malformed / invalid
    - approval configuration unreadable
    - any exception raised during validation

    Failure denies. Uncertainty denies. Secrets and tokens are never logged.
    """
    try:
        expected = _expected_approval_token()
        if not expected:
            logger.warning("Approval denied: no approval secret configured.")
            return False
        if not isinstance(approval_token, str) or not approval_token.strip():
            logger.warning("Approval denied: missing or malformed approval token.")
            return False
        if hmac.compare_digest(approval_token, expected):
            return True
        logger.warning("Approval denied: approval token did not match.")
        return False
    except Exception:
        logger.warning("Approval denied: approval validation failed.")
        return False


def _load_latest_patents() -> list[dict]:
    """Load patent records from the most recent JSON output file, or []."""
    import json
    from pathlib import Path

    from config import patent_cfg

    files = sorted(Path(patent_cfg.output_dir).glob("patents_*.json"), reverse=True)
    return json.loads(files[0].read_text()) if files else []


@mcp.tool()
async def run_module(
    module_name: str,
    dry_run: bool = True,
    since_days: int = 7,
    approval_token: str | None = None,
    ctx: Context | None = None,
) -> dict:
    """
    Run a single pipeline module and return a result summary.
    dry_run defaults True — pass dry_run=False to write to IPFS or publish to Nostr.

    Args:
        module_name: One of: patents, velocity, arxiv, archive, narrative, flight, nostr, dms-check.
        dry_run: If True (default), fetches/filters but skips IPFS pinning and Nostr publishing.
        since_days: Look-back window in days (patents module only, default 7).
    """

    if module_name not in _SINGLE_MODULES:
        return {"error": f"Unknown module '{module_name}'. Valid: {_SINGLE_MODULES}"}
    if not dry_run and not _is_human_approved(approval_token):
        return {
            "error": "Human approval required for non-dry-run execution. "
            "Set LIGHTHOUSE_APPROVAL_SECRET and provide approval_token.",
        }

    async def _log(msg: str) -> None:
        if ctx is not None:
            await ctx.info(msg)
        logger.info(msg)

    await _log(f"[MCP] run_module={module_name} dry_run={dry_run}")
    conn = _open_db()
    result: dict = {"module": module_name, "dry_run": dry_run}

    try:
        if module_name == "patents":
            from modules.patent_sentinel import run_patent_sentinel

            patents = await run_patent_sentinel(since_days=since_days, dry_run=dry_run)
            result["total"] = len(patents)
            result["flagged"] = sum(1 for p in patents if p.get("_flagged"))

        elif module_name == "velocity":
            from modules.velocity_tracker import run_velocity_tracker

            patents = _load_latest_patents()
            await _log(f"[MCP] velocity: loaded {len(patents)} patents from last run")
            anomalies = await run_velocity_tracker(conn, patents, dry_run=dry_run)
            result["anomalies"] = len(anomalies)

        elif module_name == "arxiv":
            from modules.arxiv_sentinel import run_arxiv_sentinel

            alerts = await run_arxiv_sentinel(conn, dry_run=dry_run)
            result["silence_alerts"] = len(alerts)

        elif module_name == "archive":
            from modules.wayback_archiver import run_wayback_archiver

            archived = await run_wayback_archiver(dry_run=dry_run)
            result["pages_archived"] = len(archived)

        elif module_name == "narrative":
            from modules.narrative_analyzer import run_narrative_analyzer

            archived = []  # no archiver data without a pipeline run
            await _log("[MCP] narrative standalone: no archived pages — analysing 0 pages")
            alerts = await run_narrative_analyzer(conn, archived, dry_run=dry_run)
            result["shift_alerts"] = len(alerts)

        elif module_name == "flight":
            from modules.flight_tracker import run_flight_tracker

            obs = await run_flight_tracker(conn, dry_run=dry_run)
            result["observations"] = len(obs)

        elif module_name == "nostr":
            from config import nostr_cfg

            if not nostr_cfg.private_key_hex:
                result["skipped"] = "NOSTR_PRIVATE_KEY_HEX not set"
            else:
                from modules.nostr_relay import run_nostr_bot

                await _log("[MCP] nostr standalone: no pipeline events — publishing 0 notes")
                if not dry_run:
                    await run_nostr_bot([])
                result["events_published"] = 0

        elif module_name == "dms-check":
            from config import dms_cfg
            from portal.dms_checker import check_and_broadcast

            await check_and_broadcast(db_path=dms_cfg.db_path, dry_run=dry_run)
            result["status"] = "completed"

    except Exception as exc:
        logger.error("[MCP] run_module %s failed: %s", module_name, exc, exc_info=True)
        result["error"] = str(exc)

    return result


@mcp.tool()
async def run_pipeline(
    since_days: int = 7,
    dry_run: bool = True,
    approval_token: str | None = None,
    ctx: Context | None = None,
) -> dict:
    """
    Run the full Lighthouse pipeline:
    patents → velocity → arxiv → archive → narrative → flight → nostr.
    dry_run defaults True — pass dry_run=False to write to IPFS and publish to Nostr.

    Args:
        since_days: Look-back window for the patent query (default 7).
        dry_run: If True (default), fetches/filters but skips IPFS pinning and Nostr publishing.
    """
    if not dry_run and not _is_human_approved(approval_token):
        return {
            "error": "Human approval required for non-dry-run execution. "
            "Set LIGHTHOUSE_APPROVAL_SECRET and provide approval_token.",
        }

    STEPS = 7

    async def _log(step: int, msg: str) -> None:
        if ctx is not None:
            await ctx.report_progress(step, STEPS, message=msg)
            await ctx.info(msg)
        logger.info(msg)

    await _log(0, f"[MCP] run_pipeline since_days={since_days} dry_run={dry_run}")
    conn = _open_db()
    summary: dict = {"dry_run": dry_run, "since_days": since_days}

    patents: list = []
    archived: list = []

    # 1 — Patent Sentinel
    try:
        await _log(1, "[MCP] Module 1: Patent Sentinel")
        from modules.patent_sentinel import run_patent_sentinel

        patents = await run_patent_sentinel(since_days=since_days, dry_run=dry_run)
        flagged = sum(1 for p in patents if p.get("_flagged"))
        summary["patents"] = {"total": len(patents), "flagged": flagged}
    except Exception as exc:
        logger.error("[MCP] Patent Sentinel failed: %s", exc, exc_info=True)
        summary["patents"] = {"error": str(exc)}

    # 2 — Velocity Tracker
    try:
        await _log(2, "[MCP] Module 2: Velocity Tracker")
        from modules.velocity_tracker import run_velocity_tracker

        velocity_anomalies = await run_velocity_tracker(conn, patents, dry_run=dry_run)
        summary["velocity"] = {"anomalies": len(velocity_anomalies)}
    except Exception as exc:
        logger.error("[MCP] Velocity Tracker failed: %s", exc, exc_info=True)
        summary["velocity"] = {"error": str(exc)}
        velocity_anomalies = []

    # 3 — arXiv Sentinel
    try:
        await _log(3, "[MCP] Module 3: arXiv Sentinel")
        from modules.arxiv_sentinel import run_arxiv_sentinel

        researcher_alerts = await run_arxiv_sentinel(conn, dry_run=dry_run)
        summary["arxiv"] = {"silence_alerts": len(researcher_alerts)}
    except Exception as exc:
        logger.error("[MCP] arXiv Sentinel failed: %s", exc, exc_info=True)
        summary["arxiv"] = {"error": str(exc)}
        researcher_alerts = []

    # 4 — Wayback Archiver
    try:
        await _log(4, "[MCP] Module 4: Wayback Archiver")
        from modules.wayback_archiver import run_wayback_archiver

        archived = await run_wayback_archiver(dry_run=dry_run)
        summary["archive"] = {"pages_archived": len(archived)}
    except Exception as exc:
        logger.error("[MCP] Wayback Archiver failed: %s", exc, exc_info=True)
        summary["archive"] = {"error": str(exc)}

    # 5 — Narrative Analyzer
    try:
        await _log(5, "[MCP] Module 5: Narrative Analyzer")
        from modules.narrative_analyzer import run_narrative_analyzer

        narrative_alerts = await run_narrative_analyzer(conn, archived, dry_run=dry_run)
        summary["narrative"] = {"shift_alerts": len(narrative_alerts)}
    except Exception as exc:
        logger.error("[MCP] Narrative Analyzer failed: %s", exc, exc_info=True)
        summary["narrative"] = {"error": str(exc)}
        narrative_alerts = []

    # 6 — Flight Tracker
    try:
        await _log(6, "[MCP] Module 6: Flight Tracker")
        from modules.flight_tracker import run_flight_tracker

        flight_alerts = await run_flight_tracker(conn, dry_run=dry_run)
        summary["flight"] = {"observations": len(flight_alerts)}
    except Exception as exc:
        logger.error("[MCP] Flight Tracker failed: %s", exc, exc_info=True)
        summary["flight"] = {"error": str(exc)}
        flight_alerts = []

    # 7 — Nostr Bot
    try:
        await _log(7, "[MCP] Module 7: Nostr Bot")
        from config import nostr_cfg

        if not nostr_cfg.private_key_hex:
            summary["nostr"] = {"skipped": "NOSTR_PRIVATE_KEY_HEX not set"}
        else:
            import datetime
            from datetime import UTC

            from modules.nostr_relay import (
                _load_private_key,
                _pubkey_hex,
                build_archive_note,
                build_narrative_shift_note,
                build_patent_note,
                build_researcher_note,
                build_sar_zone_note,
                build_velocity_note,
                run_nostr_bot,
            )

            priv = _load_private_key(nostr_cfg.private_key_hex)
            pubkey = _pubkey_hex(priv)
            nostr_events = []

            for p in (p for p in patents if p.get("_flagged")):
                nostr_events.append(
                    build_patent_note(
                        pubkey=pubkey,
                        patent_id=p["id"] or "",
                        title=p["title"] or "",
                        assignee=p["assignee"] or "",
                        filing_date=p["date"] or "",
                        ipc_class=p["ipc"] or "",
                        source=p["source"],
                    )
                )
            for page in archived:
                if page.ipfs_cid:
                    nostr_events.append(
                        build_archive_note(
                            pubkey=pubkey,
                            agency=page.agency,
                            url=page.url,
                            ipfs_cid=page.ipfs_cid,
                            snapshot_ts=page.snapshot_ts,
                            matched_keywords=page.matched_keywords,
                        )
                    )
            for anomaly in velocity_anomalies:
                nostr_events.append(
                    build_velocity_note(
                        pubkey=pubkey,
                        ipc_class=anomaly["ipc_class"],
                        anomaly_type=anomaly["type"],
                        week=anomaly["week"],
                        zscore=anomaly["zscore"],
                        context=f"count={anomaly['count']}, mean={anomaly['mean']}",
                    )
                )
            for alert in researcher_alerts:
                nostr_events.append(
                    build_researcher_note(
                        pubkey=pubkey,
                        name=alert["name"],
                        institution=alert["institution"],
                        last_pub_date=alert["last_pub_date"] or "unknown",
                        silent_days=alert["silent_days"],
                    )
                )
            for shift in narrative_alerts:
                nostr_events.append(
                    build_narrative_shift_note(
                        pubkey=pubkey,
                        agency=shift["agency"],
                        url=shift["url"],
                        urgency_delta=shift["urgency_delta"],
                        specificity_delta=shift["specificity_delta"],
                        summary=shift["summary"],
                    )
                )
            for obs in flight_alerts:
                nostr_events.append(
                    build_sar_zone_note(
                        pubkey=pubkey,
                        incident_date=datetime.datetime.now(UTC).strftime("%Y-%m-%d"),
                        agency=f"OpenSky/{obs['facility']}",
                        bounding_box=obs["bounding_box"],
                        source_url="https://opensky-network.org",
                        description=(
                            f"Unusual aircraft {obs['callsign']} "
                            f"(ICAO24: {obs['icao24']}) near {obs['facility']}"
                        ),
                    )
                )

            if dry_run:
                summary["nostr"] = {"dry_run": True, "events_queued": len(nostr_events)}
            else:
                await run_nostr_bot(nostr_events)
                summary["nostr"] = {"events_published": len(nostr_events)}
    except Exception as exc:
        logger.error("[MCP] Nostr Bot failed: %s", exc, exc_info=True)
        summary["nostr"] = {"error": str(exc)}

    return summary


# ---------------------------------------------------------------------------
# Phase 5: Nostr publish tools
# ---------------------------------------------------------------------------

_NOTE_TYPES = [
    "archive",
    "patent",
    "transcript",
    "sar_zone",
    "velocity",
    "researcher",
    "narrative_shift",
    "dms_broadcast",
]

# Required payload fields per note type — validated before any signing or network call
_REQUIRED_FIELDS: dict[str, list[str]] = {
    "archive": ["agency", "url", "ipfs_cid", "snapshot_ts", "matched_keywords"],
    "patent": ["patent_id", "title", "assignee", "filing_date", "ipc_class", "source"],
    "transcript": ["committee", "hearing_date", "transcript_url", "summary"],
    "sar_zone": ["incident_date", "agency", "bounding_box", "source_url", "description"],
    "velocity": ["ipc_class", "anomaly_type", "week", "zscore", "context"],
    "researcher": ["name", "institution", "last_pub_date", "silent_days"],
    "narrative_shift": ["agency", "url", "urgency_delta", "specificity_delta", "summary"],
    "dms_broadcast": ["display_name", "encrypted_message", "registration_id"],
}

_PREVIEW_PUBKEY = "0" * 64  # placeholder for unsigned previews


def _build_note(note_type: str, payload: dict, pubkey: str):
    """Dispatch payload to the correct builder. Caller must validate fields first."""
    from modules.nostr_relay import (
        build_archive_note,
        build_dms_broadcast_note,
        build_narrative_shift_note,
        build_patent_note,
        build_researcher_note,
        build_sar_zone_note,
        build_transcript_note,
        build_velocity_note,
    )

    builders = {
        "archive": lambda: build_archive_note(
            pubkey=pubkey,
            agency=payload["agency"],
            url=payload["url"],
            ipfs_cid=payload["ipfs_cid"],
            snapshot_ts=payload["snapshot_ts"],
            matched_keywords=payload["matched_keywords"],
        ),
        "patent": lambda: build_patent_note(
            pubkey=pubkey,
            patent_id=payload["patent_id"],
            title=payload["title"],
            assignee=payload["assignee"],
            filing_date=payload["filing_date"],
            ipc_class=payload["ipc_class"],
            source=payload["source"],
            ipfs_cid=payload.get("ipfs_cid"),
        ),
        "transcript": lambda: build_transcript_note(
            pubkey=pubkey,
            committee=payload["committee"],
            hearing_date=payload["hearing_date"],
            transcript_url=payload["transcript_url"],
            summary=payload["summary"],
            ipfs_cid=payload.get("ipfs_cid"),
        ),
        "sar_zone": lambda: build_sar_zone_note(
            pubkey=pubkey,
            incident_date=payload["incident_date"],
            agency=payload["agency"],
            bounding_box=payload["bounding_box"],
            source_url=payload["source_url"],
            description=payload["description"],
        ),
        "velocity": lambda: build_velocity_note(
            pubkey=pubkey,
            ipc_class=payload["ipc_class"],
            anomaly_type=payload["anomaly_type"],
            week=payload["week"],
            zscore=float(payload["zscore"]),
            context=payload["context"],
        ),
        "researcher": lambda: build_researcher_note(
            pubkey=pubkey,
            name=payload["name"],
            institution=payload["institution"],
            last_pub_date=payload["last_pub_date"],
            silent_days=int(payload["silent_days"]),
        ),
        "narrative_shift": lambda: build_narrative_shift_note(
            pubkey=pubkey,
            agency=payload["agency"],
            url=payload["url"],
            urgency_delta=float(payload["urgency_delta"]),
            specificity_delta=float(payload["specificity_delta"]),
            summary=payload["summary"],
        ),
        "dms_broadcast": lambda: build_dms_broadcast_note(
            pubkey=pubkey,
            display_name=payload["display_name"],
            encrypted_message=payload["encrypted_message"],
            registration_id=payload["registration_id"],
        ),
    }
    return builders[note_type]()


def _validate_payload(note_type: str, payload: dict) -> str | None:
    """Return an error string if validation fails, else None."""
    if note_type not in _NOTE_TYPES:
        return f"Unknown note_type '{note_type}'. Valid: {_NOTE_TYPES}"
    missing = [f for f in _REQUIRED_FIELDS[note_type] if f not in payload]
    if missing:
        return f"Missing required payload fields for '{note_type}': {missing}"
    return None


@mcp.tool()
async def build_and_preview_note(
    note_type: str,
    payload: dict,
) -> dict:
    """
    Build a Nostr note and return its unsigned content for agent review — no signing, no broadcast.
    Call this before publish_note to inspect what will be sent.

    Args:
        note_type: One of: archive, patent, transcript, sar_zone, velocity,
                   researcher, narrative_shift, dms_broadcast.
        payload: Fields required by the chosen note type. Use the required_fields
                 key in the response from an error call to see what's needed.

    Required payload fields per note_type:
        archive:         agency, url, ipfs_cid, snapshot_ts, matched_keywords (list)
        patent:          patent_id, title, assignee, filing_date, ipc_class, source [, ipfs_cid]
        transcript:      committee, hearing_date, transcript_url, summary [, ipfs_cid]
        sar_zone:        incident_date, agency, bounding_box (dict: north/south/east/west),
                         source_url, description
        velocity:        ipc_class, anomaly_type, week, zscore, context
        researcher:      name, institution, last_pub_date, silent_days
        narrative_shift: agency, url, urgency_delta, specificity_delta, summary
        dms_broadcast:   display_name, encrypted_message, registration_id
    """
    err = _validate_payload(note_type, payload)
    if err:
        return {"error": err, "required_fields": _REQUIRED_FIELDS.get(note_type, {})}

    note = _build_note(note_type, payload, pubkey=_PREVIEW_PUBKEY)
    return {
        "note_type": note_type,
        "content": note.content,
        "tags": note.tags,
        "kind": note.kind,
        "preview_pubkey": _PREVIEW_PUBKEY,
        "unsigned": True,
    }


@mcp.tool()
async def publish_note(
    note_type: str,
    payload: dict,
    dry_run: bool = True,
    approval_token: str | None = None,
) -> dict:
    """
    Build, sign, and broadcast a Nostr note to all configured relays.
    dry_run defaults True — pass dry_run=False to actually broadcast.
    Use build_and_preview_note first to confirm the content before publishing.

    Args:
        note_type: One of: archive, patent, transcript, sar_zone, velocity,
                   researcher, narrative_shift, dms_broadcast.
        payload: Fields required by the chosen note type (same as build_and_preview_note).
        dry_run: If True (default), signs the note but does not broadcast. Returns the
                 signed event JSON for inspection.
    """
    err = _validate_payload(note_type, payload)
    if err:
        return {"error": err, "required_fields": _REQUIRED_FIELDS.get(note_type, {})}

    from config import nostr_cfg

    if not nostr_cfg.private_key_hex:
        return {"error": "NOSTR_PRIVATE_KEY_HEX is not set — cannot sign or publish"}

    from modules.nostr_relay import _load_private_key, _pubkey_hex, broadcast

    priv = _load_private_key(nostr_cfg.private_key_hex)
    pubkey = _pubkey_hex(priv)

    note = _build_note(note_type, payload, pubkey=pubkey)
    note.sign(priv)

    signed_event = note.to_wire()

    if dry_run:
        return {
            "dry_run": True,
            "note_type": note_type,
            "event_id": signed_event["id"],
            "content": signed_event["content"],
            "tags": signed_event["tags"],
            "relays": nostr_cfg.relays,
        }

    if not _is_human_approved(approval_token):
        return {
            "error": "Human approval required for live publish. "
            "Set LIGHTHOUSE_APPROVAL_SECRET and provide approval_token.",
        }

    relay_results = await broadcast(note)
    ok_count = sum(1 for v in relay_results.values() if v)
    logger.info(
        "[MCP] publish_note %s → %d/%d relays ok", note.id[:12], ok_count, len(relay_results)
    )

    return {
        "dry_run": False,
        "note_type": note_type,
        "event_id": signed_event["id"],
        "content": signed_event["content"],
        "relays": relay_results,
        "published_to": ok_count,
        "total_relays": len(relay_results),
    }


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def cli_main() -> None:
    import sys

    transport = os.environ.get("LIGHTHOUSE_MCP_TRANSPORT", "stdio")
    host = os.environ.get("LIGHTHOUSE_MCP_HOST", "0.0.0.0")
    port = int(os.environ.get("LIGHTHOUSE_MCP_PORT", "8001"))

    argv = sys.argv[1:]
    i = 0
    while i < len(argv):
        if argv[i] == "--transport" and i + 1 < len(argv):
            transport = argv[i + 1]
            i += 2
        elif argv[i] == "--host" and i + 1 < len(argv):
            host = argv[i + 1]
            i += 2
        elif argv[i] == "--port" and i + 1 < len(argv):
            port = int(argv[i + 1])
            i += 2
        else:
            i += 1

    if transport == "sse":
        import asyncio

        import uvicorn
        from starlette.applications import Starlette
        from starlette.requests import Request
        from starlette.responses import JSONResponse
        from starlette.routing import Mount, Route

        async def health_endpoint(request: Request) -> JSONResponse:
            return JSONResponse({"status": "ok", "service": "lighthouse-mcp"})

        mcp_app = mcp.sse_app()
        app = Starlette(
            routes=[
                Route("/health", health_endpoint, methods=["GET", "HEAD"]),
                Mount("/", mcp_app),
            ]
        )
        config = uvicorn.Config(app, host=host, port=port, log_level="info")
        server = uvicorn.Server(config)
        asyncio.run(server.serve())
    else:
        mcp.run(transport=transport)  # type: ignore[arg-type]


if __name__ == "__main__":
    cli_main()
