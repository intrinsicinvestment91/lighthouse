"""
Project Lighthouse — orchestration entry point.

Run all modules in sequence:
  1. Patent Sentinel    → fetch new filings, flag watched assignees
  2. Velocity Tracker   → detect filing-rate anomalies per IPC class
  3. arXiv Sentinel     → flag researchers who have stopped publishing
  4. Wayback Archiver   → crawl agency newsrooms, pin to IPFS
  5. Narrative Analyzer → detect tone shifts in press releases via LLM
  6. Flight Tracker     → flag unusual aircraft near key facilities
  7. Nostr Bot          → broadcast signed metadata notes to relays

Usage:
    python main.py [--since-days N] [--dry-run] [--module MODULE]
    lighthouse [options]

MODULE may be patents, velocity, arxiv, sec, archive, narrative, flight,
adsb, correlator, targeted, nostr, dms-check, or all.
"""

import argparse
import asyncio
import json
import logging
import sys
import uuid
from datetime import UTC, datetime

from config import app_cfg, dms_cfg, nostr_cfg
from modules import db
from modules.nostr_relay import (
    _load_private_key,
    _pubkey_hex,
    broadcast_node_heartbeat,
    build_correlation_note,
    run_nostr_bot,
)
from modules.patent_sentinel import run_patent_sentinel
from modules.proxy_rotator import load_proxy_rotator
from modules.state_db import init_db
from modules.validator import validate_config
from modules.wayback_archiver import run_wayback_archiver

# ---------------------------------------------------------------------------
# Logging — inject run_id into every log record
# ---------------------------------------------------------------------------


class _RunIdFilter(logging.Filter):
    def __init__(self, run_id: str) -> None:
        super().__init__()
        self.run_id = run_id

    def filter(self, record: logging.LogRecord) -> bool:
        record.run_id = self.run_id
        return True


def _configure_logging(run_id: str) -> None:
    handler = logging.StreamHandler()
    handler.addFilter(_RunIdFilter(run_id))
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)-8s [%(run_id)s] %(name)s: %(message)s")
    )
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)


logger = logging.getLogger("lighthouse.main")


def _is_publishable_correlation(event: dict, dry_run: bool) -> bool:
    if dry_run:
        return False
    score = float(event.get("score", 0))
    signals = event.get("signals") or []
    verification_status = event.get("verification_status", "unverified")
    # Single-source events are never published regardless of score
    if verification_status == "single_source":
        return False
    return score >= 7.0 and len(set(signals)) >= 2


def _correlation_summary(event: dict) -> str:
    entity = ", ".join(event.get("entities") or ["unknown entity"])
    score = float(event.get("score", 0))
    signals = sorted(set(event.get("signals") or []))
    return f"{entity} correlated across {', '.join(signals)} (score {score:.1f}/10)."


# ---------------------------------------------------------------------------
# Target-aware module selection
# ---------------------------------------------------------------------------

ALL_PIPELINE_MODULES = {
    "patents",
    "velocity",
    "arxiv",
    "sec",
    "archive",
    "narrative",
    "flight",
    "adsb",
    "correlator",
    "nostr",
}

TARGET_SIGNAL_TO_MODULES = {
    "sec": {"sec"},
    "patent": {"patents", "velocity"},
    "arxiv": {"arxiv"},
    "flight": {"flight"},
    "adsb": {"adsb"},
    "narrative": {"narrative"},
    "archive": {"archive"},
}


def targeted_modules_from_routes(routes: dict) -> set[str]:
    modules: set[str] = set()
    for signal in routes:
        mapped = TARGET_SIGNAL_TO_MODULES.get(signal)
        if mapped:
            modules.update(mapped)
    return modules


def load_target_routes() -> dict:
    from modules.signal_router import build_signal_routes_from_file
    from modules.targets import DEFAULT_TARGETS_PATH

    try:
        return build_signal_routes_from_file(DEFAULT_TARGETS_PATH)
    except (ValueError, OSError) as exc:
        logger.error("Targeted mode: failed to load targets: %s", exc)
        raise SystemExit(1) from exc


def target_entities_for_signal(routes: dict | None, signal: str) -> list[str] | None:
    if routes is None:
        return None
    route = routes.get(signal)
    if route is None or not route.entities:
        return None
    return list(route.entities)


def target_keywords_for_signal(routes: dict | None, signal: str) -> list[str] | None:
    if routes is None:
        return None
    route = routes.get(signal)
    if route is None or not route.keywords:
        return None
    return list(route.keywords)


def target_regions_for_signal(routes: dict | None, signal: str) -> list[str] | None:
    if routes is None:
        return None
    route = routes.get(signal)
    if route is None or not route.regions:
        return None
    return list(route.regions)


def resolve_requested_modules(
    module_arg: str,
    routes: dict | None = None,
) -> set[str]:
    if module_arg == "all":
        return ALL_PIPELINE_MODULES.copy()
    if module_arg == "targeted":
        from modules.signal_router import route_summary

        if routes is None:
            routes = load_target_routes()

        modules = targeted_modules_from_routes(routes)
        if not modules:
            logger.warning("Targeted mode: no signal modules selected from routes")
            return set()

        modules.add("correlator")
        summary = route_summary(routes)
        logger.info("Targeted mode active: modules=%s", sorted(modules))
        logger.info(
            "Target routes: %d signals, %d targets",
            summary["route_count"],
            len(summary["targets"]),
        )
        return modules
    return {module_arg}


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------


async def main(args: argparse.Namespace) -> None:
    target_routes = load_target_routes() if args.module == "targeted" else None
    requested_modules = resolve_requested_modules(args.module, routes=target_routes)
    sec_target_entities = target_entities_for_signal(target_routes, "sec")
    arxiv_target_entities = target_entities_for_signal(target_routes, "arxiv")
    arxiv_target_keywords = target_keywords_for_signal(target_routes, "arxiv")
    patent_target_entities = target_entities_for_signal(target_routes, "patent")
    patent_target_keywords = target_keywords_for_signal(target_routes, "patent")
    flight_target_entities = target_entities_for_signal(target_routes, "flight")
    flight_target_keywords = target_keywords_for_signal(target_routes, "flight")
    flight_target_regions = target_regions_for_signal(target_routes, "flight")
    adsb_target_entities = target_entities_for_signal(target_routes, "adsb")
    adsb_target_keywords = target_keywords_for_signal(target_routes, "adsb")
    adsb_target_regions = target_regions_for_signal(target_routes, "adsb")
    narrative_target_entities = target_entities_for_signal(target_routes, "narrative")
    narrative_target_keywords = target_keywords_for_signal(target_routes, "narrative")
    archive_target_entities = target_entities_for_signal(target_routes, "archive")
    archive_target_keywords = target_keywords_for_signal(target_routes, "archive")
    dry_run = args.dry_run

    if dry_run:
        logger.info("DRY-RUN mode — IPFS pinning and Nostr publishing are disabled.")

    await validate_config()

    db_conn = init_db(app_cfg.state_db_path)
    rotator = load_proxy_rotator()

    # B3 — emit a kind:30078 heartbeat so mesh operators know this node is alive
    if nostr_cfg.private_key_hex:
        try:
            import time as _time

            priv = _load_private_key(nostr_cfg.private_key_hex)
            pubkey = _pubkey_hex(priv)
            modules_active = (
                sorted(requested_modules) if args.module != "all" else sorted(ALL_PIPELINE_MODULES)
            )
            if not dry_run:
                await broadcast_node_heartbeat(
                    pubkey=pubkey,
                    region=nostr_cfg.node_region,
                    modules_active=modules_active,
                )
                db_conn.execute(
                    "INSERT OR REPLACE INTO seen (key, first_seen_at) VALUES ('heartbeat:last', ?)",
                    (int(_time.time()),),
                )
                db_conn.commit()
            else:
                logger.info("DRY-RUN: would broadcast heartbeat for pubkey %s", pubkey[:12])
        except Exception as exc:
            logger.warning("Heartbeat failed (non-fatal): %s", exc)

    patents: list = []
    archived: list = []
    velocity_anomalies: list = []
    researcher_alerts: list = []
    sec_alerts: list = []
    narrative_alerts: list = []
    flight_alerts: list = []
    adsb_alerts: list = []

    # --- Module 1: Patent Sentinel ---
    if "patents" in requested_modules:
        logger.info("=== Module 1: Patent Sentinel ===")
        try:
            patents = await run_patent_sentinel(
                since_days=args.since_days,
                dry_run=dry_run,
                rotator=rotator,
                target_entities=patent_target_entities,
                target_keywords=patent_target_keywords,
            )
            flagged = [p for p in patents if p.get("_flagged")]
            logger.info("Total patents: %d  Flagged: %d", len(patents), len(flagged))
        except Exception as exc:
            logger.error("Patent Sentinel failed: %s", exc, exc_info=True)
            patents = []

    # --- Module 2: Velocity Tracker ---
    if "velocity" in requested_modules:
        logger.info("=== Module 2: Velocity Tracker ===")
        try:
            from modules.velocity_tracker import run_velocity_tracker

            velocity_anomalies = await run_velocity_tracker(db_conn, patents, dry_run=dry_run)
            logger.info("Velocity anomalies: %d", len(velocity_anomalies))
        except Exception as exc:
            logger.error("Velocity Tracker failed: %s", exc, exc_info=True)

    # --- Module 3: arXiv Sentinel ---
    if "arxiv" in requested_modules:
        logger.info("=== Module 3: arXiv Sentinel ===")
        try:
            from modules.arxiv_sentinel import run_arxiv_sentinel

            researcher_alerts = await run_arxiv_sentinel(
                db_conn,
                dry_run=dry_run,
                rotator=rotator,
                target_entities=arxiv_target_entities,
                target_keywords=arxiv_target_keywords,
            )
            logger.info("Researcher silence alerts: %d", len(researcher_alerts))
        except Exception as exc:
            logger.error("arXiv Sentinel failed: %s", exc, exc_info=True)

    # --- Module 3b: SEC EDGAR Sentinel ---
    if "sec" in requested_modules:
        logger.info("=== Module 3b: SEC EDGAR Sentinel ===")
        try:
            from modules.sec_sentinel import run_sec_sentinel

            sec_alerts = await run_sec_sentinel(
                db_conn,
                since_days=args.since_days,
                dry_run=dry_run,
                rotator=rotator,
                target_entities=sec_target_entities,
            )
            logger.info("SEC material filings: %d", len(sec_alerts))
        except Exception as exc:
            logger.error("SEC Sentinel failed: %s", exc, exc_info=True)

    # --- Module 4: Wayback Archiver ---
    if "archive" in requested_modules:
        logger.info("=== Module 4: Wayback Archiver ===")
        try:
            archived = await run_wayback_archiver(
                dry_run=dry_run,
                rotator=rotator,
                target_entities=archive_target_entities,
                target_keywords=archive_target_keywords,
            )
            logger.info("Pages archived to IPFS: %d", len(archived))
        except Exception as exc:
            logger.error("Wayback Archiver failed: %s", exc, exc_info=True)
            archived = []

    # --- Module 5: Narrative Analyzer ---
    if "narrative" in requested_modules:
        logger.info("=== Module 5: Narrative Analyzer ===")
        try:
            from modules.narrative_analyzer import run_narrative_analyzer

            narrative_alerts = await run_narrative_analyzer(
                db_conn,
                archived,
                dry_run=dry_run,
                target_entities=narrative_target_entities,
                target_keywords=narrative_target_keywords,
            )
            logger.info("Narrative shift alerts: %d", len(narrative_alerts))
        except Exception as exc:
            logger.error("Narrative Analyzer failed: %s", exc, exc_info=True)

    # --- Module 6: Flight Tracker ---
    if "flight" in requested_modules:
        logger.info("=== Module 6: Flight Tracker ===")
        try:
            from modules.flight_tracker import run_flight_tracker

            flight_alerts = await run_flight_tracker(
                db_conn,
                dry_run=dry_run,
                rotator=rotator,
                target_entities=flight_target_entities,
                target_keywords=flight_target_keywords,
                target_regions=flight_target_regions,
            )
            logger.info("Unusual flight observations: %d", len(flight_alerts))
        except Exception as exc:
            logger.error("Flight Tracker failed: %s", exc, exc_info=True)

    # --- Module 6b: ADSB-Exchange Sentinel ---
    if "adsb" in requested_modules:
        logger.info("=== Module 6b: ADSB-Exchange Sentinel ===")
        try:
            from modules.adsb_sentinel import run_adsb_sentinel

            adsb_alerts = await run_adsb_sentinel(
                db_conn,
                dry_run=dry_run,
                rotator=rotator,
                target_entities=adsb_target_entities,
                target_keywords=adsb_target_keywords,
                target_regions=adsb_target_regions,
            )
            logger.info("ADSB unusual observations: %d", len(adsb_alerts))
        except Exception as exc:
            logger.error("ADSB Sentinel failed: %s", exc, exc_info=True)

    # --- Module A3: Correlator ---
    correlation_events: list = []
    if "correlator" in requested_modules:
        logger.info("=== Module A3: Correlator ===")
        try:
            from modules.correlator import run_correlator

            correlation_events = await run_correlator(
                db_conn,
                sec_alerts=sec_alerts,
                narrative_alerts=narrative_alerts,
                flight_alerts=flight_alerts,
                adsb_alerts=adsb_alerts,
                dry_run=dry_run,
            )
            logger.info("Correlation events: %d", len(correlation_events))
        except Exception as exc:
            logger.error("Correlator failed: %s", exc, exc_info=True)

    # --- Probationary Promotion ---
    if "correlator" in requested_modules and not dry_run:
        try:
            import time as _time

            candidates = db.fetch_probationary_correlations()
            promoted = 0
            for corr in candidates:
                if corr.get("verification_status") == "single_source":
                    continue
                if db.promote_correlation(corr["event_id"], int(_time.time())):
                    promoted += 1
            if promoted:
                logger.info("[Promotion] promoted %d correlation(s) from probation", promoted)
        except Exception as exc:
            logger.error("Probationary promotion failed: %s", exc, exc_info=True)

    # --- Correlation publishing (strict gate, correlation-only) ---
    if "correlator" in requested_modules or "nostr" in requested_modules:
        if not nostr_cfg.private_key_hex:
            logger.warning("NOSTR_PRIVATE_KEY_HEX not set — skipping correlation publishing")
        elif not nostr_cfg.publish_correlations:
            logger.info("NOSTR_PUBLISH_CORRELATIONS=false — skipping correlation publishing")
        else:
            logger.info("=== Nostr Correlation Publisher ===")
            try:
                priv = _load_private_key(nostr_cfg.private_key_hex)
                pubkey = _pubkey_hex(priv)
                nostr_events = []
                for corr in correlation_events:
                    if not _is_publishable_correlation(corr, dry_run=dry_run):
                        continue
                    nostr_events.append(
                        build_correlation_note(
                            pubkey=pubkey,
                            event_type=corr["event_type"],
                            entities=corr["entities"],
                            score=corr["score"],
                            signals=corr["signals"],
                            summary=_correlation_summary(corr),
                        )
                    )

                if dry_run:
                    logger.info(
                        "DRY-RUN: would publish %d high-signal correlation events",
                        len(nostr_events),
                    )
                elif not nostr_events:
                    logger.info("No publishable high-signal correlation events")
                else:
                    await run_nostr_bot(nostr_events)
            except Exception as exc:
                logger.error("Correlation publisher failed: %s", exc, exc_info=True)

    # --- DMS Check (standalone, not part of 'all') ---
    if "dms-check" in requested_modules:
        logger.info("=== DMS Checker ===")
        try:
            from portal.dms_checker import check_and_broadcast

            await check_and_broadcast(db_path=dms_cfg.db_path, dry_run=dry_run)
        except Exception as exc:
            logger.error("DMS Checker failed: %s", exc, exc_info=True)


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------


def handle_status_command(_args: argparse.Namespace) -> None:
    from pathlib import Path

    from config import app_cfg, narrative_cfg, nostr_cfg
    from modules.state_db import SCHEMA_VERSION

    db.init_alerts_table()

    db_path = Path(app_cfg.state_db_path)
    if db_path.exists():
        size_kb = db_path.stat().st_size / 1024
        db_info = f"{db_path} ({size_kb:.1f} KB)"
    else:
        db_info = f"{db_path} (not found)"

    rows = db.fetchall(
        "SELECT source, COUNT(*) as count, MAX(created_at) as last_seen "
        "FROM alerts GROUP BY source ORDER BY source"
    )

    try:
        hb_row = db.fetchone("SELECT first_seen_at FROM seen WHERE key = 'heartbeat:last'")
        hb_ts = (
            datetime.fromtimestamp(hb_row["first_seen_at"]).strftime("%Y-%m-%d %H:%M:%S")
            if hb_row
            else "never"
        )
    except Exception:
        hb_ts = "never"

    print(f"DB:            {db_info}")
    print(f"Schema:        v{SCHEMA_VERSION}")
    print(f"NOSTR key:     {'set' if nostr_cfg.private_key_hex else 'not set'}")
    print(f"ANTHROPIC key: {'set' if narrative_cfg.anthropic_api_key else 'not set'}")
    print(f"Last heartbeat:{hb_ts:>21}")
    print()
    print("Alerts by source:")
    if rows:
        for row in rows:
            print(
                f"  {row['source']:<28} {row['count']:>5} total"
                f"   last: {(row['last_seen'] or 'never')[:19]}"
            )
    else:
        print("  (no alerts yet)")


def handle_correlations_command(args: argparse.Namespace) -> None:
    db.init_correlation_table()
    results = db.fetch_correlations(
        min_score=args.min_score,
        since_days=getattr(args, "since_days", None),
        limit=args.limit,
    )
    if getattr(args, "json", False):
        import json as _json

        print(_json.dumps(results, indent=2))
    else:
        for corr in results:
            print(
                f"[{corr['score']:.1f}] {corr['event_type']} — "
                f"{', '.join(corr['entities'])} — {corr['detected_at'][:19]}"
            )


def _format_callsign_timestamp(ts: int) -> str:
    return datetime.fromtimestamp(ts, UTC).strftime("%Y-%m-%d %H:%M:%S UTC")


def handle_callsigns_command(args: argparse.Namespace) -> None:
    db.init_callsign_history_table()

    if args.callsign:
        rows = db.fetch_callsign_history(args.callsign, limit=args.limit)
        if getattr(args, "json", False):
            print(json.dumps(rows, indent=2))
            return
        print(f"History for {args.callsign.upper()}")
        for row in rows:
            facility = row.get("facility") or "-"
            icao = row.get("icao_hex") or "-"
            military = bool(row.get("military_style"))
            print(
                f"{_format_callsign_timestamp(row['observed_at'])} "
                f"{row['source']} {facility} {icao} military_style={military}"
            )
        return

    rows = db.fetch_callsign_summary(limit=args.limit)
    if getattr(args, "json", False):
        print(json.dumps(rows, indent=2))
        return

    print("Callsign Summary")
    for row in rows:
        count = row["count"]
        label = "observation" if count == 1 else "observations"
        last_seen = _format_callsign_timestamp(row["last_seen"])
        print(f"{row['normalized_callsign']} — {count} {label}, last seen {last_seen}")


def handle_alerts_command(args: argparse.Namespace) -> None:
    since = getattr(args, "since_days", None)

    if args.entity:
        alerts = db.find_related_alerts(args.entity, limit=args.limit, since_days=since)
    elif args.source:
        alerts = db.fetch_alerts_by_source(args.source, limit=args.limit, since_days=since)
    elif args.min_score is not None:
        alerts = db.fetch_alerts_min_score(args.min_score, limit=args.limit, since_days=since)
    elif args.label:
        alerts = db.fetch_alerts_by_label(args.label, limit=args.limit, since_days=since)
    else:
        alerts = db.fetch_alerts(limit=args.limit, since_days=since)

    if getattr(args, "json", False):
        import json as _json

        print(_json.dumps([a["alert"] for a in alerts], indent=2))
    else:
        for alert in alerts:
            print(alert["alert"])


def handle_targets_command(args: argparse.Namespace) -> None:
    from modules.signal_router import build_signal_routes, route_summary
    from modules.targets import DEFAULT_TARGETS_PATH, load_targets, validate_targets

    path = args.path or DEFAULT_TARGETS_PATH
    validation = validate_targets(path)

    if not validation["valid"]:
        if getattr(args, "json", False):
            print(json.dumps(validation, indent=2))
        else:
            print("Target validation: FAIL")
            for error in validation.get("errors", []):
                print(f"Error: {error}")
            for warning in validation.get("warnings", []):
                print(f"Warning: {warning}")
        raise SystemExit(1)

    config = load_targets(path)
    routes = build_signal_routes(config)
    summary = route_summary(routes)

    if getattr(args, "json", False):
        payload: dict = {
            "validation": validation,
            "summary": summary,
            "targets": [target.to_dict() for target in config.targets],
        }
        if getattr(args, "show_routes", False):
            payload["routes"] = {signal: route.to_dict() for signal, route in routes.items()}
        print(json.dumps(payload, indent=2))
        return

    print("Target validation: PASS")
    print(f"Targets: {validation['target_count']} total / {validation['enabled_count']} enabled")
    print(f"Routes: {summary['route_count']} signals")
    print(f"Signals: {', '.join(summary['signals'])}")
    if summary["entities"]:
        print(f"Entities: {', '.join(summary['entities'])}")

    if getattr(args, "show_routes", False):
        for signal in sorted(routes.keys()):
            route = routes[signal]
            print(f"\n{signal}:")
            print(f"  targets: {', '.join(route.targets)}")
            print(f"  priority: {route.priority}")
            if route.entities:
                print(f"  entities: {', '.join(route.entities)}")
            if route.keywords:
                print(f"  keywords: {', '.join(route.keywords)}")
            if route.regions:
                print(f"  regions: {', '.join(route.regions)}")


def handle_profiles_command(args: argparse.Namespace) -> None:
    """Read-only: list / show / validate bundled Intelligence Profiles. Never activates."""
    from modules.profile_registry import (
        get_profile,
        list_bundled_profiles,
        validate_all_bundled,
    )

    as_json = getattr(args, "json", False)

    if getattr(args, "validate", False):
        result = validate_all_bundled()
        if as_json:
            print(json.dumps(result, indent=2))
        else:
            print(f"Profile validation: {'PASS' if result['valid'] else 'FAIL'}")
            print(f"Profiles: {result['profile_count']}")
            for error in result["errors"]:
                print(f"Error: {error}")
            for warning in result["warnings"]:
                print(f"Warning: {warning}")
        if not result["valid"]:
            raise SystemExit(1)
        return

    show = getattr(args, "show", None)
    if show:
        try:
            profile = get_profile(show)
        except KeyError as exc:
            print(f"Error: {exc}")
            raise SystemExit(1) from exc
        if as_json:
            print(json.dumps(profile.to_dict(), indent=2))
        else:
            print(f"{profile.profile_id} v{profile.profile_version} [{profile.status}]")
            print(f"  {profile.display_name}")
            print(f"  mission: {profile.mission}")
            print(f"  private: {profile._is_private()}")
            print(f"  public_publishing: {profile._public_publishing_enabled()}")
            print(f"  dms_eligible: {profile.dead_mans_switch_policy.get('eligible', False)}")
        return

    profiles = list_bundled_profiles()
    if as_json:
        print(json.dumps([p.to_dict() for p in profiles], indent=2))
        return
    print(f"Bundled intelligence profiles: {len(profiles)}")
    for profile in profiles:
        privacy = "private" if profile._is_private() else "public-analysis"
        print(f"  {profile.profile_id} v{profile.profile_version}  [{profile.status}]  {privacy}")
        print(f"      {profile.display_name}")
    print(
        "\nNote: exactly one profile may be active at a time. "
        "Initial activation is available for repository_capability_discovery v2 through "
        "profile-activate. Switching and deactivation remain unimplemented."
    )


def handle_backup_readiness_command(args: argparse.Namespace) -> None:
    """Read-only backup-readiness inspection. Performs no mutation and triggers no backup."""
    from modules.backup_readiness import DATA_BOUNDARY, inspect_backup_readiness

    result = inspect_backup_readiness(
        boundary=getattr(args, "boundary", None) or DATA_BOUNDARY,
        data_dir=getattr(args, "data_dir", None),
    )

    if getattr(args, "json", False):
        print(json.dumps(result, indent=2))
        return

    print(f"Backup readiness: {result['status']}")
    print(f"Boundary: {result['boundary']}")
    print(f"Authoritative state complete: {result['authoritative_state_complete']}")
    print(f"Backup boundary valid: {result['backup_boundary_valid']}")
    print(f"Restore procedure documented: {result['restore_procedure_documented']}")
    if result["blocking_reasons"]:
        print("Blocking:")
        for reason in result["blocking_reasons"]:
            print(f"  - {reason}")
    if result["warnings"]:
        print("Warnings:")
        for warning in result["warnings"]:
            print(f"  - {warning}")
    print(f"\n{result['note']}")


def handle_backup_manifest_command(args: argparse.Namespace) -> None:
    """Generate a read-only backup manifest of authoritative state. Copies NO data."""
    from pathlib import Path

    from modules.backup_manifest import BackupError, generate_manifest

    try:
        manifest = generate_manifest(
            data_dir=args.data_dir,
            db_path=getattr(args, "db_path", None),
            boundary=getattr(args, "boundary", None) or "/data",
            include_reconstructable=getattr(args, "include_reconstructable", False),
        )
        Path(args.output).write_text(json.dumps(manifest.to_dict(), indent=2), encoding="utf-8")
    except BackupError as exc:
        print(f"backup-manifest failed: {exc}")
        raise SystemExit(1) from exc

    summary = manifest.summary()
    if getattr(args, "json", False):
        print(
            json.dumps(
                {
                    "manifest_generated": True,
                    "output": str(args.output),
                    "eligibility": summary["readiness_result"],
                    "authoritative_entries": summary["authoritative_entries"],
                    "total_entries": summary["total_entries"],
                    "total_bytes": summary["total_bytes"],
                },
                indent=2,
            )
        )
        return
    print(f"Manifest generated: {args.output}")
    print(f"Eligibility (boundary): {summary['readiness_result']}")
    print(f"Authoritative entries: {summary['authoritative_entries']}")
    print(f"Total entries: {summary['total_entries']}  total bytes: {summary['total_bytes']}")
    print("This wrote a manifest only — no data was copied and no backup was performed.")
    print("A manifest does NOT authorize profile activation.")


def handle_backup_create_command(args: argparse.Namespace) -> None:
    """Create an independently-verified backup at an explicit destination, or fail closed."""
    from modules.backup_create import create_backup
    from modules.backup_manifest import BackupError

    try:
        report = create_backup(
            data_dir=args.data_dir,
            destination=args.destination,
            db_path=getattr(args, "db_path", None),
            boundary=getattr(args, "boundary", None) or "/data",
            include_reconstructable=getattr(args, "include_reconstructable", False),
        )
    except BackupError as exc:
        print(f"backup-create failed: {exc}")
        raise SystemExit(1) from exc

    if getattr(args, "json", False):
        print(json.dumps(report, indent=2))
        return
    print(f"Backup created: {report['destination']}")
    print(f"Eligibility: {report['eligibility']}")
    print(f"Manifest generated: {report['manifest_generated']}")
    print(f"Backup copied: {report['backup_copied']}")
    print(f"Backup verified: {report['backup_verified']}")
    print(f"Restore drill completed: {report['restore_drill_completed']}")
    print(f"Production restore performed: {report['production_restore_performed']}")
    print(f"Profile activation authorized: {report['profile_activation_authorized']}")
    print(report["note"])


def handle_backup_verify_command(args: argparse.Namespace) -> None:
    """Independently verify an existing backup. Exits nonzero if verification does not pass."""
    from modules.backup_verify import VerificationError, verify_backup

    try:
        report = verify_backup(args.backup)
    except VerificationError as exc:
        print(f"backup-verify failed: {exc}")
        raise SystemExit(1) from exc

    if getattr(args, "json", False):
        print(json.dumps(report, indent=2))
    else:
        print(f"Backup verification: {report['status']}")
        print(f"Backup root: {report['backup_root']}")
        if report["blocking_failures"]:
            print("Blocking:")
            for reason in report["blocking_failures"]:
                print(f"  - {reason}")
        print("Verification confirms integrity only — it does NOT authorize profile activation.")
    if report["status"] != "verified":
        raise SystemExit(1)


def handle_restore_drill_command(args: argparse.Namespace) -> None:
    """Perform an isolated restore drill into a new non-production destination. Fails closed."""
    from modules.backup_verify import RestoreDrillError, restore_drill

    try:
        report = restore_drill(
            args.backup,
            args.destination,
            source_data_root=getattr(args, "source_data_root", None),
        )
    except RestoreDrillError as exc:
        print(f"restore-drill failed: {exc}")
        raise SystemExit(1) from exc

    output = getattr(args, "output", None)
    if output:
        from pathlib import Path

        Path(output).write_text(json.dumps(report, indent=2), encoding="utf-8")

    if getattr(args, "json", False):
        print(json.dumps(report, indent=2))
    else:
        print(f"Restore drill: {report['status']}")
        print(f"Restore destination: {report['restore_destination']}")
        print(f"Manifest valid: {report['manifest_valid']}")
        print(f"Payload verified: {report['payload_verified']}")
        print(f"Restored digests verified: {report['restored_digests_verified']}")
        print(f"SQLite integrity verified: {report['sqlite_integrity_verified']}")
        print(f"Source unchanged: {report['source_unchanged']}")
        print(f"Production restore performed: {report['production_restore_performed']}")
        print(f"Profile activation authorized: {report['profile_activation_authorized']}")
        print("Unproven by this drill:")
        for item in report["unproven_guarantees"]:
            print(f"  - {item}")
        if output:
            print(f"Written to: {output}")
    if report["status"] != "completed":
        raise SystemExit(1)


def handle_profile_transition_preview_command(args: argparse.Namespace) -> None:
    """Generate a read-only profile-transition preview artifact. Never activates or switches.

    This produces evidence for a future operator/Gateway review only. It writes no DB state,
    contacts no Gateway, executes no collector, and never authorizes activation. Exits nonzero
    only when inputs cannot be resolved (unknown profile, unreadable evidence report).
    """
    from modules.profile_transition import (
        ProfileTransitionError,
        generate_transition_preview_from_registry,
    )

    try:
        preview = generate_transition_preview_from_registry(
            target_profile_id=args.target_profile,
            current_profile_id=getattr(args, "current_profile", None),
            manifest_path=getattr(args, "backup_manifest", None),
            verification_report_path=getattr(args, "verification_report", None),
            restore_report_path=getattr(args, "restore_report", None),
        )
    except ProfileTransitionError as exc:
        print(f"profile-transition-preview failed: {exc}")
        raise SystemExit(1) from exc

    report = preview.to_dict()
    output = getattr(args, "output", None)
    if output:
        from pathlib import Path

        Path(output).write_text(json.dumps(report, indent=2), encoding="utf-8")

    if getattr(args, "json", False):
        print(json.dumps(report, indent=2))
        return

    print(f"Transition preview: {preview.transition_status}")
    print(f"Preview id: {preview.preview_id}")
    print(f"Target profile: {preview.target_profile_id} v{preview.target_profile_version}")
    current = preview.current_profile_id or (
        "not supplied — preview does not read active-profile state"
    )
    print(f"Current profile: {current}")
    print(f"Artifact digest: {preview.artifact_digest}")
    if output:
        print(f"Written to: {output}")
    if preview.blocking_reasons:
        print("Blocking / incomplete:")
        for reason in preview.blocking_reasons:
            print(f"  - {reason}")
    if preview.warnings:
        print("Warnings:")
        for warning in preview.warnings:
            print(f"  - {warning}")
    print("Required operator approvals (future Gateway authority):")
    for item in preview.required_operator_approvals:
        print(f"  - {item}")
    print(
        "\nThis is a review artifact only. It performs NO activation, NO switch, and does NOT "
        "authorize profile activation. Operator approval remains a future Gateway capability."
    )


def handle_profile_activate_command(args: argparse.Namespace) -> None:
    """Backup-gated initial activation of repository_capability_discovery v2 only.

    Requires exact typed confirmation and the verified backup/restore evidence ladder.
    Does not switch, deactivate, or contact the Gateway. Diagnostics go to stderr when --json.
    """
    from modules.profile_activation import (
        RCD_PROFILE_ID,
        RCD_PROFILE_VERSION,
        REQUIRED_CONFIRMATION,
        ActivationRefused,
        activate_repository_capability_discovery,
    )

    target = getattr(args, "target_profile", None)
    version = getattr(args, "target_version", None)
    if target != RCD_PROFILE_ID or version != RCD_PROFILE_VERSION:
        print(
            f"unsupported activation target: only {RCD_PROFILE_ID} v{RCD_PROFILE_VERSION} "
            "may be activated",
            file=sys.stderr,
        )
        raise SystemExit(1)

    try:
        result = activate_repository_capability_discovery(
            confirmation=args.confirm,
            transition_preview=args.transition_preview,
            backup_root=args.backup_root,
            backup_manifest=args.backup_manifest,
            verification_report=args.verification_report,
            restore_report=args.restore_report,
            data_dir=getattr(args, "data_dir", None),
            dry_run=bool(getattr(args, "dry_run", False)),
        )
    except ActivationRefused as exc:
        print(f"profile-activate refused: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    except Exception as exc:  # noqa: BLE001 — fail closed
        print(f"profile-activate failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    if getattr(args, "json", False):
        print(json.dumps(result, indent=2))
        return

    print(f"Profile activation: {result['status']}")
    print(f"Active profile: {result['active_profile_id']} v{result['active_profile_version']}")
    print(f"Digest: {result['active_profile_sha256']}")
    print(f"Source: {result['activation_source']}")
    print(f"Activated at: {result['activated_at']}")
    print(f"Confirmation required: {REQUIRED_CONFIRMATION}")
    print("Authority pins: all false (no execution/publication/Gateway authority).")


def handle_active_profile_command(args: argparse.Namespace) -> None:
    """Read-only active-profile status. Never creates a table or database."""
    from modules.active_profile_store import (
        CorruptActiveProfileStateError,
        read_active_profile_state,
    )

    try:
        state = read_active_profile_state()
    except CorruptActiveProfileStateError as exc:
        print(f"active-profile unreadable: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    report = state.to_dict()
    if getattr(args, "json", False):
        print(json.dumps(report, indent=2))
        return

    print(f"Activation state: {state.activation_state}")
    if state.activation_state == "active":
        print(f"Active profile: {state.active_profile_id} v{state.active_profile_version}")
        print(f"Digest: {state.active_profile_sha256}")
        print(f"Source: {state.activation_source}")
        print(f"Activated at: {state.activated_at}")
    else:
        print("No profile is active.")
    print(f"Artifact digest: {state.artifact_digest}")


def handle_graph_command(args: argparse.Namespace) -> None:
    db.init_graph_tables()
    entity = args.entity.strip()
    # Try entity, callsign, facility prefixes in order; fall back to entity
    candidates = [
        f"entity:{entity}",
        f"callsign:{entity.upper()}",
        f"facility:{entity}",
    ]
    node_id = f"entity:{entity}"
    for candidate in candidates:
        if db.fetch_graph_node(candidate):
            node_id = candidate
            break
    result = db.traverse_graph(
        node_id=node_id,
        depth=args.depth,
        limit=args.limit,
    )

    if args.json:
        print(json.dumps(result))
        return

    print("Nodes:")
    for node in result["nodes"]:
        print(f"- {node['node_id']} ({node['node_type']})")

    print("\nEdges:")
    for edge in result["edges"]:
        print(f"- {edge['source_id']} -> {edge['target_id']} [{edge['relation']}]")


def handle_temporal_command(args: argparse.Namespace) -> None:
    from modules import temporal_analysis

    use_json = getattr(args, "json", False)
    db.init_callsign_history_table()

    if args.callsign:
        history = db.fetch_callsign_activity(args.callsign, limit=500)
        metrics = temporal_analysis.callsign_recurrence_metrics(history)
        recent = temporal_analysis.callsign_recent_activity(history)
        windows = temporal_analysis.callsign_activity_windows(history)

        if use_json:
            print(json.dumps({"metrics": metrics, "recent": recent, "windows": windows}, indent=2))
            return

        label = metrics["callsign"] or args.callsign.upper()
        print(f"Callsign: {label}")
        print(f"Total observations: {metrics['total_observations']}")
        print(f"First seen:         {metrics['first_seen'] or 'never'}")
        print(f"Last seen:          {metrics['last_seen'] or 'never'}")
        print(f"Active days:        {metrics['active_days']}")
        print(f"Avg per day:        {metrics['avg_per_day']}")
        print(f"Facilities seen:    {metrics['facilities_seen']}")
        if metrics["signal_types_seen"]:
            print(f"Signal types:       {', '.join(metrics['signal_types_seen'])}")
        if metrics["most_common_facility"]:
            print(f"Most common facility: {metrics['most_common_facility']}")
        if metrics["most_common_signal"]:
            print(f"Most common signal:   {metrics['most_common_signal']}")

        print(f"\nRecent activity (last {recent['window_days']} days):")
        print(f"  Observations: {recent['recent_observations']}")
        if recent["recent_facilities"]:
            print(f"  Facilities:   {', '.join(recent['recent_facilities'])}")
        if recent["recent_signals"]:
            print(f"  Signals:      {', '.join(recent['recent_signals'])}")

        if windows:
            print("\nActivity by day:")
            for w in windows:
                print(f"  {w['date']}  {w['count']:>3} observation(s)")
        return

    if args.facility:
        observations = db.fetch_facility_activity(args.facility, limit=500)
        summaries = temporal_analysis.facility_activity_summary(observations)
        summary = next((s for s in summaries if s["facility"] == args.facility), None) or {
            "facility": args.facility,
            "total_observations": 0,
            "unique_callsigns": 0,
            "first_seen": None,
            "last_seen": None,
            "recurring_callsigns": [],
            "most_active_callsign": None,
        }
        windows = temporal_analysis.callsign_activity_windows(observations)

        if use_json:
            print(json.dumps({"summary": summary, "windows": windows}, indent=2))
            return

        print(f"Facility: {args.facility}")
        print(f"Total observations: {summary['total_observations']}")
        print(f"Unique callsigns:   {summary['unique_callsigns']}")
        print(f"First seen:         {summary['first_seen'] or 'never'}")
        print(f"Last seen:          {summary['last_seen'] or 'never'}")
        if summary["most_active_callsign"]:
            print(f"Most active callsign: {summary['most_active_callsign']}")
        if summary["recurring_callsigns"]:
            print(f"Recurring callsigns: {', '.join(summary['recurring_callsigns'])}")

        if windows:
            print("\nActivity by day:")
            for w in windows:
                print(f"  {w['date']}  {w['count']:>3} observation(s)")
        return

    recent_days = getattr(args, "recent", None) or 7
    observations = db.fetch_recent_activity(since_days=recent_days, limit=500)

    from collections import defaultdict

    cs_histories: dict = defaultdict(list)
    for obs in observations:
        cs = obs.get("normalized_callsign")
        if cs:
            cs_histories[cs].append(obs)

    callsign_metrics = []
    for _cs, history in cs_histories.items():
        m = temporal_analysis.callsign_recurrence_metrics(history)
        callsign_metrics.append(m)
    callsign_metrics.sort(key=lambda m: m["total_observations"], reverse=True)

    facility_summaries = temporal_analysis.facility_activity_summary(observations)

    if use_json:
        print(
            json.dumps(
                {
                    "window_days": recent_days,
                    "total_observations": len(observations),
                    "callsigns": callsign_metrics,
                    "facilities": facility_summaries,
                },
                indent=2,
            )
        )
        return

    print(f"Recent activity (last {recent_days} days)")
    print(f"Total observations: {len(observations)}")

    if callsign_metrics:
        print("\nMost active callsigns:")
        for m in callsign_metrics[:10]:
            print(f"  {m['callsign']}  —  {m['total_observations']} observation(s)")

    if facility_summaries:
        print("\nMost active facilities:")
        for s in facility_summaries[:10]:
            fac = s["facility"] or "(unknown)"
            print(
                f"  {fac}  —  {s['total_observations']} observation(s),"
                f" {s['unique_callsigns']} unique callsign(s)"
            )


def handle_tempo_command(args: argparse.Namespace) -> None:
    from modules import tempo_analysis

    use_json = getattr(args, "json", False)
    db.init_callsign_history_table()

    if args.callsign:
        history = db.fetch_callsign_activity(args.callsign, limit=500)
        metrics = tempo_analysis.callsign_operational_tempo(history)
        clusters = tempo_analysis.callsign_temporal_clusters(history)

        if use_json:
            print(json.dumps({"metrics": metrics, "clusters": clusters}, indent=2))
            return

        label = metrics["callsign"] or args.callsign.upper()
        print(f"Callsign: {label}")
        print(f"Total observations: {metrics['total_observations']}")
        print(f"Active days:        {metrics['active_days']}")
        if metrics["avg_interval_hours"] is not None:
            print(f"Avg interval:       {metrics['avg_interval_hours']:.1f} hours")
            print(f"Shortest interval:  {metrics['shortest_interval_hours']:.1f} hours")
            print(f"Longest interval:   {metrics['longest_interval_hours']:.1f} hours")
        if metrics["weekday_distribution"]:
            top = sorted(metrics["weekday_distribution"].items(), key=lambda x: -x[1])
            print(f"Busiest weekday:    {top[0][0]} ({top[0][1]} observation(s))")
        if metrics["hour_distribution"]:
            top_h = sorted(metrics["hour_distribution"].items(), key=lambda x: -x[1])
            print(f"Busiest hour:       {top_h[0][0]}:00 ({top_h[0][1]} observation(s))")
        if metrics["recurring_facilities"]:
            print(f"Recurring facilities: {', '.join(metrics['recurring_facilities'])}")

        if clusters["same_day"]:
            print(f"\nSame-day recurrence: {len(clusters['same_day'])} date(s)")
        if clusters["same_hour"]:
            top_hrs = [f"{c['hour']}:00 ×{c['count']}" for c in clusters["same_hour"][:3]]
            print(f"Same-hour clusters:  {', '.join(top_hrs)}")
        if clusters["weekly_windows"]:
            print(f"Active weeks:        {len(clusters['weekly_windows'])}")
        return

    if args.facility:
        observations = db.fetch_facility_activity(args.facility, limit=500)
        summary = tempo_analysis.facility_operational_tempo(observations)

        if use_json:
            print(json.dumps(summary, indent=2))
            return

        print(f"Facility: {args.facility}")
        print(f"Total observations:     {summary['total_observations']}")
        print(f"Unique callsigns:       {summary['unique_callsigns']}")
        print(f"Recurrence concentration: {summary['recurrence_concentration']:.0%}")
        if summary["most_active_callsign"]:
            print(f"Most active callsign: {summary['most_active_callsign']}")
        if summary["recurring_callsigns"]:
            print(f"Recurring callsigns:  {', '.join(summary['recurring_callsigns'])}")
        if summary["busiest_weekdays"]:
            top = summary["busiest_weekdays"][0]
            print(f"Busiest weekday:      {top[0]} ({top[1]} observation(s))")
        if summary["busiest_hours"]:
            top_h = max(summary["busiest_hours"], key=lambda x: x[1])
            print(f"Busiest hour:         {top_h[0]}:00 ({top_h[1]} observation(s))")
        return

    # --recurring (default)
    min_obs = getattr(args, "min_observations", 5)
    min_days = getattr(args, "min_days", 3)
    limit = getattr(args, "limit", 50)
    rows = db.fetch_recurring_callsigns_summary(
        min_observations=min_obs,
        min_days=min_days,
        limit=limit,
    )

    if use_json:
        print(json.dumps(rows, indent=2))
        return

    if not rows:
        print(f"No callsigns with >= {min_obs} observations across >= {min_days} days")
        return

    print(f"Recurring callsigns (min {min_obs} obs, {min_days} days):")
    for row in rows:
        military = " [military]" if row.get("any_military") else ""
        first_ts = row.get("first_ts")
        last_ts = row.get("last_ts")
        first = datetime.fromtimestamp(first_ts, UTC).strftime("%Y-%m-%d") if first_ts else "?"
        last = datetime.fromtimestamp(last_ts, UTC).strftime("%Y-%m-%d") if last_ts else "?"
        print(
            f"  {row['normalized_callsign']:<12}{military}  "
            f"{row['obs_count']:>4} obs  {row['day_count']:>3} days  "
            f"{first} – {last}"
        )


def handle_graph_backfill_command(args: argparse.Namespace) -> None:
    from modules.graph_backfill import backfill_correlation_graphs

    result = backfill_correlation_graphs(limit=args.limit, dry_run=args.dry_run)

    if args.json:
        print(json.dumps(result))
        return

    print("Graph backfill complete")
    print(f"Correlations seen: {result['correlations_seen']}")
    print(f"Backfilled: {result['correlations_backfilled']}")
    print(f"Failed: {result['correlations_failed']}")
    print(f"Nodes: {result['nodes']}")
    print(f"Edges: {result['edges']}")
    print(f"Dry run: {str(result['dry_run']).lower()}")


def handle_observe_command(args: argparse.Namespace) -> None:
    """Observe the declared local sources. Read-only toward them; fact-only into the database.

    This is a local observation command, not an agent capability. It observes exactly what the
    operator declared in `data/observation_sources.json`, inside exactly the roots configured in
    `LIGHTHOUSE_OBSERVATION_ROOTS` — it discovers nothing, schedules nothing, and never decides that
    what it found is interesting. Significance is not this layer's question.
    """
    from config import get_observation_cfg
    from modules.observation_intake import ObservationIntakeError, run_observation_intake
    from modules.observation_sources import ObservationSourceError, load_sources

    cfg = get_observation_cfg()

    try:
        config_sources = load_sources(cfg.sources_path).enabled_sources()
    except ObservationSourceError as exc:
        print(f"Cannot read the observation source list: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    selected = list(config_sources)
    if args.source:
        # A filter over what was declared — never a way to name a new path. Otherwise the boundary
        # would be one careless flag away from being whatever the caller typed.
        selected = [source for source in config_sources if source.path == args.source]
        if not selected:
            print(
                f"{args.source} is not a declared observation source "
                f"(add it to {cfg.sources_path} first)",
                file=sys.stderr,
            )
            raise SystemExit(1)

    try:
        run = run_observation_intake(
            sources=selected,
            allowed_roots=cfg.allowed_roots,
            dry_run=args.dry_run,
        )
    except ObservationIntakeError as exc:
        print(f"Refusing to observe: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    result = run.to_dict()

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"Observation intake ({'dry run' if run.dry_run else 'live'})")
        print(f"Allowed roots: {', '.join(run.allowed_roots)}")
        print(f"Sources: {result['sources']}")
        for outcome in run.outcomes:
            detail = outcome.reason or (
                f"{outcome.entities} entities, {outcome.relationships} relationships"
            )
            print(f"  [{outcome.status}] {outcome.source_type} {outcome.path} — {detail}")
        print(
            f"Stored: {result['stored']}  Already present: {result['already_present']}  "
            f"Previewed: {result['previewed']}  Failed: {result['failed']}"
        )

    if result["failed"]:
        raise SystemExit(1)


def handle_observations_command(args: argparse.Namespace) -> None:
    """Inspect what Lighthouse has observed. Read-only, and enforced as such by the store itself.

    This answers "show me what Lighthouse knows". It has no answer to "what matters", because
    nothing in this system does yet — significance is a later layer's question, and a command that
    started summarizing is how that layer would arrive by accident.
    """
    from modules.observation_query import (
        ObservationQueryError,
        entity_occurrences,
        list_observations,
        observation_stats,
        search_observations,
        shared_entities,
        show_observation,
    )

    action = args.observations_command
    if action is None:
        print("Choose one of: list, show, search, stats, where, shared", file=sys.stderr)
        raise SystemExit(1)

    try:
        if action == "list":
            result = list_observations(limit=args.limit)
        elif action == "search":
            result = search_observations(
                scope_type=args.scope_type,
                sensor=args.sensor,
                observation_type=args.observation_type,
                digest=args.digest,
                since=args.since,
                until=args.until,
                limit=args.limit,
            )
        elif action == "show":
            result = show_observation(args.id)
        elif action == "where":
            result = entity_occurrences(args.entity_id, limit=args.limit)
        elif action == "shared":
            result = shared_entities(args.scope_id or [], limit=args.limit)
        else:
            result = observation_stats()
    except ObservationQueryError as exc:
        # stderr, so that `--json` stdout stays parseable even when the answer is an error.
        print(f"{exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    if args.json:
        # No `sort_keys`: the query layer already emits its keys in a fixed order, and that ordering
        # is a guarantee (`stats` is sorted by name, never by count). Letting the serializer sort
        # would hide whether the guarantee actually holds.
        print(json.dumps(result, indent=2))
        return

    _print_observations(action, result)


def _print_observations(action: str, result: dict) -> None:
    if action == "where":
        entity = result["entity"]
        print(f"{entity['entity_id']}  ({entity['entity_type']})")
        print(f"{result['occurrence_count']} occurrence(s)\n")
        for occurrence in result["occurrences"]:
            print(
                f"  {occurrence['scope_type']:<10} {occurrence['scope_label']}  "
                f"[{occurrence['scope_id'][:12]}]  {occurrence['scope_path']}"
            )
            print(f"    observed at  {occurrence['observed_at']}")
            print(f"    artifact     {occurrence['source_artifact_digest'][:12]}")
            for supporting in occurrence["supporting_observations"]:
                print(
                    f"    via          {supporting['observation_type']:<30} "
                    f"[{supporting['sensor']}] (confidence {supporting['confidence']})"
                )
            for relationship in occurrence["relationships"]:
                print(f"    linked by    {relationship['relationship_type']}")
        _warn_if_truncated(result)
        return

    if action == "shared":
        print(f"{result['shared_entity_count']} entity(ies) observed in every requested scope\n")
        for scope in result["scopes"]:
            print(
                f"  scope  {scope['scope_type']:<10} {scope['scope_label']}  "
                f"[{scope['scope_id'][:12]}]  {scope['scope_path']}"
            )
        print()
        for entity in result["shared_entities"]:
            print(f"  {entity['entity_type']:<12} {entity['entity_id']}")
            for occurrence in entity["occurrences"]:
                print(
                    f"    in {occurrence['scope_label']:<20} "
                    f"artifact {occurrence['source_artifact_digest'][:12]}  "
                    f"[{', '.join(occurrence['sensors'])}]"
                )
        _warn_if_truncated(result)
        return

    if action == "stats":
        for key in ("observation_sets", "repositories", "documents"):
            print(f"{key.replace('_', ' ').capitalize():<20} {result[key]}")
        print(f"{'Entities':<20} {result['entities']} ({result['distinct_entities']} distinct)")
        print(f"{'Relationships':<20} {result['relationships']}")
        print("\nEntity types")
        for name, total in result["entity_types"].items():
            print(f"  {name:<16} {total}")
        return

    if action == "show":
        print(f"Observation {result['id']}")
        print(f"  scope        {result['scope_type']} — {result['scope_path']}")
        print(f"  sensors      {', '.join(result['sensors'])}")
        print(f"  observed at  {result['observed_at']}")
        print(f"  stored at    {result['stored_at']}")
        print(f"  artifact     {result['artifact_digest']}")
        print(f"  derivation   {result['derivation_version']}")
        print(f"\nObservations ({result['observation_count']})")
        for observation in result["observations"]:
            value = observation["value"]
            confidence = observation["confidence"]
            print(
                f"  {observation['observation_type']:<34} "
                f"[{observation['source']}] {value!r} (confidence {confidence})"
            )
        if result["undetermined"]:
            print(f"\nUndetermined ({len(result['undetermined'])})")
            for name in result["undetermined"]:
                print(f"  {name}")
        print(f"\nEntities ({result['entity_count']})")
        for entity in result["entities"]:
            print(f"  {entity['entity_type']:<12} {entity['value']}")
        print(f"\nRelationships ({result['relationship_count']})")
        for relationship in result["relationships"]:
            print(
                f"  {relationship['relationship_type']:<20} "
                f"{relationship['subject_id']} -> {relationship['object_id']}"
            )
        return

    print(f"{result['count']} observation(s)")
    for record in result["observations"]:
        print(
            f"  {record['id'][:12]}  {record['scope_type']:<10} "
            f"{record['observation_count']:>3} obs  "
            f"[{', '.join(record['sensors'])}]  {record['scope_path']}"
        )
    _warn_if_truncated(result, " Narrow it with --scope-type, --digest or --since.")


def _warn_if_truncated(result: dict, hint: str = "") -> None:
    """A bounded scan that hit its bound says so, rather than passing a partial answer off as a
    complete one. On stderr, so that `--json` stdout stays parseable."""
    if not result.get("scan_truncated"):
        return
    print(
        f"\nNote: a bounded scan hit its limit, so this answer may be incomplete.{hint}",
        file=sys.stderr,
    )


def handle_patterns_command(args: argparse.Namespace) -> None:
    """Run one declared rule over stored facts, and let an operator decide what to do about it.

    This is the first Lighthouse surface that presents something other than a raw fact — so it is
    the first that could tell an operator what to think. It does not. A candidate says *"these
    facts satisfy one declared rule; look at this"*, and every candidate carries the unknowns and
    limitations that say what the recurrence does **not** prove. Nothing here scores, ranks,
    promotes, publishes, executes, or activates a profile.
    """
    from modules.pattern_candidate_service import (
        ObservationQueryError,
        PatternServiceError,
        list_candidates,
        record_disposition,
        run_detection,
        show_candidate,
    )

    action = args.patterns_command
    if action is None:
        print("Choose one of: detect, list, show, review", file=sys.stderr)
        raise SystemExit(1)

    try:
        if action == "detect":
            result = run_detection(detector=args.detector, dry_run=args.dry_run).to_dict()
        elif action == "list":
            result = list_candidates(
                profile_id=args.profile_id,
                detector_id=args.detector_id,
                candidate_type=args.candidate_type,
                candidate_key=args.candidate_key,
                disposition=args.disposition,
                all_versions=args.all_versions,
                limit=args.limit,
            )
        elif action == "show":
            result = show_candidate(args.id)
        else:
            result = record_disposition(
                candidate_id=args.id,
                operator_id=args.operator_id,
                decision=args.decision,
                reason=args.reason,
            )
    except (PatternServiceError, ObservationQueryError) as exc:
        # Both families, because a detector reads facts: a tampered artifact is refused by the FACT
        # boundary, and that refusal must arrive as a fail-closed error rather than as a traceback.
        # (Found in adversarial review — catching only the candidate layer let the fact layer's
        # `TamperedObservationError` escape, dumping raw internals and corrupting `--json` stdout.)
        # stderr, so `--json` stdout stays parseable even when the answer is an error.
        print(f"{exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    if args.json:
        print(json.dumps(result, indent=2))
        return

    _print_patterns(action, result)


SCIENTIFIC_DETECTOR = "scientific_source_disappearance_review"


def _print_patterns(action: str, result: dict) -> None:
    if action == "detect":
        if result["detector_id"] == SCIENTIFIC_DETECTOR:
            _print_scientific_detect(result)
        else:
            _print_repository_detect(result)
        return
    if action == "show":
        if result["candidate"]["detector_id"] == SCIENTIFIC_DETECTOR:
            _print_scientific_show(result)
        else:
            _print_repository_show(result)
        return
    if action == "review":
        decided = result["disposition"]
        print(f"Recorded {decided['decision']} for {result['candidate_id']}")
        print(f"  operator {decided['operator_id']} (identity not authenticated)")
        print(f"  reason   {decided['reason']}")
        print(
            "\nThis is a local review note. It does not verify the candidate, create a finding, "
            "authorize publication, or contact any other system."
        )
        return

    _print_candidate_list(result)


def _print_repository_detect(result: dict) -> None:
    mode = "dry run" if result["dry_run"] else "live"
    print(f"Pattern detection ({mode})")
    print(f"Detector: {result['detector_id']} {result['detector_version']}")
    ownership = (
        "active profile; detector admitted"
        if result.get("profile_activated")
        else "declared owner; not activated"
    )
    print(f"Profile:  {result['profile_id']} ({ownership})")
    print(
        f"Rule:     {result['rule_id']} "
        f"(min_distinct_repository_scopes="
        f"{result['rule_parameters']['min_distinct_repository_scopes']})"
    )
    print(f"Examined: {result['entities_examined']} content identities\n")

    if not result["candidates"]:
        print("No content identity recurs across two or more repositories.")
    for candidate in result["candidates"]:
        scopes = ", ".join(
            f"{scope['scope_label']}"
            + ("" if scope["coverage_state"] == "complete" else f" [{scope['coverage_state']}]")
            for scope in candidate["scopes"]
        )
        print(f"  [{candidate['status']}] {candidate['candidate_id']}")
        print(f"      {candidate['subject']['entity_id']}")
        print(
            f"      observed in {candidate['distinct_repository_scope_count']} "
            f"repositories: {scopes}"
        )
        print(f"      state: {candidate['candidate_state']}")

    for skip in result["skipped"]:
        print(f"  [skipped] {skip['entity_id']} — {skip['reason']}", file=sys.stderr)

    print(
        f"\nStored: {result['stored']}  Already present: {result['already_present']}  "
        f"Previewed: {result['previewed']}"
    )
    if result["candidates"]:
        _print_candidate_caveat()


def _print_scientific_detect(result: dict) -> None:
    mode = "dry run" if result["dry_run"] else "live"
    print(f"Pattern detection ({mode})")
    print(f"Detector: {result['detector_id']} {result['detector_version']}")
    ownership = (
        "active profile; detector admitted"
        if result.get("profile_activated")
        else "declared owner; not activated"
    )
    print(f"Profile:  {result['profile_id']} ({ownership})")
    print(f"Rule:     {result['rule_id']} (minimum 7 days since last seen)")
    print(f"Examined: {result['evaluated_locator_count']} admitted public locators\n")
    if result["locator_scan_truncated"]:
        print(
            "  (locator scan truncated — not every ledger locator was evaluated)", file=sys.stderr
        )

    if not result["candidates"]:
        print("No admitted public source satisfies the preservation review rule.")
    for candidate in result["candidates"]:
        days = (candidate["elapsed_seconds"] or 0) // (24 * 3600)
        print(f"  [{candidate['status']}] {candidate['candidate_id']}")
        print(f"      {candidate['subject']['entity_id']}")
        print(f"      previously available at {candidate['locator'].get('canonical_locator')}")
        print(f"      latest check returned not found, {days} days later")
        print(f"      outage context: {candidate['outage_context_status']}")
        if candidate["counterevidence_count"]:
            print(f"      {candidate['counterevidence_count']} known exact copy/copies recorded")
        print(f"      state: {candidate['candidate_state']}")

    print(
        f"\nStored: {result['stored']}  Already present: {result['already_present']}  "
        f"Previewed: {result['previewed']}"
    )
    # Reason codes for locators that produced no candidate — machine tokens on stderr, never a claim
    # about any source. The load-bearing distinction: these describe why a CANDIDATE was declined.
    print(
        "Declined (by reason): "
        f"blocked_broad_failure_context={result['blocked_broad_failure_context']} "
        f"suppressed_by_recovery={result['suppressed_by_recovery']} "
        f"insufficient_history={result['insufficient_history']} "
        f"insufficient_elapsed_time={result['insufficient_elapsed_time']} "
        f"no_prior_success={result['no_prior_success']} "
        f"latest_outcome_not_triggering={result['latest_outcome_not_triggering']} "
        f"invalid_evidence={result['invalid_evidence']}",
        file=sys.stderr,
    )
    if result["candidates"]:
        _print_scientific_caveat()


def _print_repository_show(result: dict) -> None:
    candidate = result["candidate"]
    print(f"{candidate['candidate_id']}")
    print(f"  key        {candidate['candidate_key']}")
    print(f"  state      {candidate['candidate_state']}")
    print(f"  profile    {candidate['profile_id']} (declared owner; not activated)")
    print(f"  detector   {candidate['detector_version']}")
    print(f"  subject    {candidate['subject']['entity_id']}")
    print(f"  created    {candidate['created_at']}")

    print(f"\nEvidence — observed in {candidate['distinct_repository_scope_count']} repositories")
    for occurrence in candidate["supporting_occurrences"]:
        print(f"  {occurrence['scope_label']}  ({occurrence['scope_path']})")
        print(
            f"      coverage {occurrence['coverage_state']}  "
            f"observed {occurrence['observed_at']}  "
            f"sensors {', '.join(occurrence['sensors'])}"
        )
        for relationship in occurrence["relationships"]:
            subject = relationship["subject_id"]
            path = subject.split(":", 2)[-1] if subject.startswith("file:") else subject
            print(f"      {relationship['relationship_type']}  {path}")

    _print_show_footer(result)


def _print_scientific_show(result: dict) -> None:
    candidate = result["candidate"]
    evidence = candidate["evidence"]
    temporal = evidence["temporal_rule"]
    outage = evidence["outage_context"]
    print(f"{candidate['candidate_id']}")
    print(f"  key        {candidate['candidate_key']}")
    print(f"  state      {candidate['candidate_state']}")
    print(f"  profile    {candidate['profile_id']} (declared owner; not activated)")
    print(f"  detector   {candidate['detector_version']}")
    print(f"  subject    {candidate['subject']['entity_id']}")
    print(f"  created    {candidate['created_at']}")

    days = (temporal["elapsed_seconds"] or 0) // (24 * 3600)
    minimum = (temporal["minimum_elapsed_seconds"] or 0) // (24 * 3600)
    print("\nPreservation review candidate")
    print(f"  locator              {evidence['locator']['canonical_locator']}")
    print(f"  previously available {temporal['prior_success_checked_at']}")
    print(
        f"  latest not found     {temporal['triggering_not_found_checked_at']} "
        f"(HTTP {evidence['triggering_not_found']['http_status']})"
    )
    print(f"  elapsed              {days} days (minimum {minimum})")
    print(
        f"  outage context       {outage['outage_context_status']} "
        f"({outage['unrelated_check_count']} unrelated, "
        f"{outage['infrastructure_failure_count']} infrastructure failures)"
    )

    if candidate["counterevidence"]:
        print("\nKnown exact copies (counterevidence)")
        for item in candidate["counterevidence"]:
            if item["type"] == "current_public_exact_mirror":
                print(f"  current public copy    {item['canonical_locator']}")
            elif item["type"] == "historical_public_exact_copy":
                print(
                    f"  historical public copy {item['canonical_locator']} "
                    f"(current availability not established)"
                )
            else:
                label = item.get("scope_label") or item["scope_id"][:12]
                print(f"  stored local copy      {label} ({item['scope_type']})")

    _print_show_footer(result)


def _print_show_footer(result: dict) -> None:
    print("\nNot assessed")
    for statement in result["unknown_statements"]:
        print(f"  - {statement}")
    print("\nLimitations")
    for statement in result["limitation_statements"]:
        print(f"  - {statement}")

    decided = result["disposition"]
    if decided is None:
        print("\nNo operator decision has been recorded for this version.")
    else:
        print(
            f"\nOperator decision: {decided['decision']} "
            f"by {decided['operator_id']} at {decided['created_at']}"
        )
        print(f"  reason: {decided['reason']}")
        print("  (a local review note — not a finding, and not authorization for anything)")


def _print_candidate_list(result: dict) -> None:
    print(f"{result['count']} candidate(s)")
    for candidate in result["candidates"]:
        decided = candidate["disposition"]
        decision = decided["decision"] if decided else "—"
        if candidate["detector_id"] == SCIENTIFIC_DETECTOR:
            locator = candidate.get("locator", {}).get("canonical_locator", "")
            detail = f"preservation review: {locator}"
        else:
            scopes = ", ".join(scope["scope_label"] for scope in candidate["scopes"])
            detail = f"{candidate['distinct_repository_scope_count']} repos: {scopes}"
        print(
            f"  {candidate['candidate_id'][:15]}  {candidate['candidate_state']:<16} "
            f"{decision:<22} {detail}"
        )
    if result["candidates"]:
        _print_candidate_caveat()


def _print_candidate_caveat() -> None:
    """The sentence that has to be in front of the operator every time, not buried in `show`."""
    print(
        "\nA candidate means these facts satisfy one declared review rule. Exact byte identity "
        "\ndoes not prove copying, independent implementation, shared capability or novelty, and "
        "\nit establishes no significance. Operator review is required."
    )


def _print_scientific_caveat() -> None:
    """The scientific-preservation caveat: a review prompt, never a claim a source is gone."""
    print(
        "\nA preservation review candidate means a previously available public source's latest "
        "\ncheck returned not found after the declared interval. It does not establish that the "
        "\nsource permanently disappeared, was removed, or matters scientifically; known copies "
        "\nand limitations are shown for operator review."
    )


def handle_public_sources_command(args: argparse.Namespace) -> None:
    """Check admitted public HTTPS locators, and inspect the append-only history of those checks.

    This is the one surface in the fact-first pipeline that reaches the network, so it is a
    deliberate, named command — never hidden inside `--module`. It records a direct factual outcome
    for every attempt and says nothing about what that history means: a 404 is `not_found`, never a
    disappearance; a timeout is `timeout`, never an absence. It creates no candidate, activates no
    profile, and contacts no Gateway.
    """
    from config import get_public_source_cfg
    from modules.observed_source_exporter import ExportError, export_observed_source_reference
    from modules.public_source_check import PublicSourceCheckError
    from modules.public_source_check_service import (
        PublicSourceServiceError,
        list_runs,
        list_source_checks,
        run_checks,
        show_check,
        show_run,
    )

    action = args.public_sources_command
    if action is None:
        print("Choose one of: check, runs, history, show, export-osr", file=sys.stderr)
        raise SystemExit(1)

    try:
        if action == "check":
            cfg = get_public_source_cfg()
            result = run_checks(
                targets_path=args.targets_file,
                allowed_hosts=cfg.allowed_hosts,
                dry_run=args.dry_run,
            ).to_dict()
        elif action == "runs":
            result = show_run(args.id) if args.id else list_runs(limit=args.limit)
        elif action == "history":
            result = list_source_checks(
                source_id=args.source_id,
                locator=args.locator,
                locator_id=args.locator_id,
                content_digest=args.content_digest,
                limit=args.limit,
            )
        elif action == "export-osr":
            result = export_observed_source_reference(
                args.check_id,
                intended_consumer=args.consumer,
            )
        else:  # show
            result = show_check(args.id)
    except (PublicSourceServiceError, PublicSourceCheckError, ExportError) as exc:
        # stderr, so `--json` stdout stays parseable even when the answer is an error.
        print(f"{exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    if args.json:
        print(json.dumps(result, indent=2))
        return

    _print_public_sources(action, result)


def _print_public_sources(action: str, result: dict) -> None:
    if action == "check":
        mode = "dry run" if result["dry_run"] else "live"
        print(f"Public-source check ({mode})")
        print(f"Adapter: {result['adapter_version']}  Policy: {result['policy_version']}")
        print(f"Checked {result['completed_check_count']} of {result['target_count']} targets\n")
        for check in result["checks"]:
            status = f" [{check['http_status']}]" if check["http_status"] is not None else ""
            digest = f"  content:{check['content_digest'][:12]}…" if check["content_digest"] else ""
            print(f"  {check['outcome']}{status}  {check['final_locator']}{digest}")
        counts = ", ".join(f"{k}={v}" for k, v in sorted(result["outcome_counts"].items()))
        print(f"\nOutcomes: {counts}")
        print(
            "\nEach line is a direct fact about one attempt. A not_found is an HTTP 404 or "
            "410, not a\nclaim that a source is gone; a timeout is not an absence. "
            "No candidate was created."
        )
        return

    if action == "runs" and "runs" in result:
        print(f"{result['count']} check run(s)")
        for run_row in result["runs"]:
            counts = ", ".join(f"{k}={v}" for k, v in sorted(run_row["outcome_counts"].items()))
            print(
                f"  {run_row['run_id'][:16]}  {run_row['completed_at']}  "
                f"{run_row['completed_check_count']} checks  {counts}"
            )
        return

    if "run" in result and "checks" in result:  # runs --id
        run_row = result["run"]
        print(f"{run_row['run_id']}")
        print(f"  started   {run_row['started_at']}")
        print(f"  completed {run_row['completed_at']}")
        print(f"  checks    {run_row['completed_check_count']} of {run_row['target_count']}")
        for check in result["checks"]:
            print(f"    {check['outcome']}  {check['final_locator']}")
        return

    if "check" in result:  # show --id
        check = result["check"]
        print(f"{check['check_id']}")
        print(f"  outcome   {check['outcome']}")
        print(f"  locator   {check['canonical_locator']}")
        if check["final_locator"] != check["canonical_locator"]:
            print(f"  final     {check['final_locator']}")
        if check["http_status"] is not None:
            print(f"  status    {check['http_status']}")
        if check["content_digest"]:
            print(f"  content   content:{check['content_digest']}")
        print(f"  checked   {check['checked_at']}")
        return

    if "reference" in result:  # export-osr
        ref = result["reference"]
        print(f"{ref['reference_id']}")
        print(f"  consumer  {ref['intended_consumer']}")
        print(f"  use       {ref['intended_use']}")
        print(f"  check     {ref['check_id']}")
        print(f"  content   {ref['content_identity']}")
        print(f"  checked   {ref['checked_at']}")
        print(
            "\nThis reference carries locator metadata and content identity only — not the body "
            "and not authority to retain, derive, publish, or execute."
        )
        return

    if "checks" in result:  # history
        print(f"{result['count']} check(s)")
        for check in result["checks"]:
            status = f" [{check['http_status']}]" if check["http_status"] is not None else ""
            print(f"  {check['checked_at']}  {check['outcome']}{status}  {check['final_locator']}")
        return


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Project Lighthouse — OSINT archival pipeline",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--since-days",
        type=int,
        default=7,
        help="Look back this many days when querying patent APIs",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch and filter but skip IPFS pinning and Nostr publishing",
    )
    parser.add_argument(
        "--module",
        choices=[
            "patents",
            "velocity",
            "arxiv",
            "sec",
            "archive",
            "narrative",
            "flight",
            "adsb",
            "correlator",
            "nostr",
            "dms-check",
            "targeted",
            "all",
        ],
        default="all",
        help="Run only the specified module (default: all)",
    )

    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser(
        "status",
        help="Show pipeline health: alert counts per source, DB size, key configuration",
    )

    correlations_parser = subparsers.add_parser(
        "correlations",
        help="Query cross-source correlation events",
    )
    correlations_parser.add_argument("--limit", type=int, default=10)
    correlations_parser.add_argument("--min-score", type=float, dest="min_score", default=None)
    correlations_parser.add_argument(
        "--since", type=int, dest="since_days", metavar="DAYS", default=None
    )
    correlations_parser.add_argument("--json", action="store_true")

    alerts_parser = subparsers.add_parser(
        "alerts",
        help="Query stored alerts",
    )
    alerts_parser.add_argument(
        "--limit",
        type=int,
        default=10,
        help="Maximum alerts to return",
    )
    alerts_parser.add_argument(
        "--entity",
        type=str,
        help="Filter alerts by related entity",
    )
    alerts_parser.add_argument(
        "--source",
        type=str,
        help="Filter alerts by source",
    )
    alerts_parser.add_argument(
        "--min-score",
        type=float,
        help="Filter alerts by minimum score",
    )
    alerts_parser.add_argument(
        "--label",
        type=str,
        help="Filter alerts by score label",
    )
    alerts_parser.add_argument(
        "--since",
        type=int,
        dest="since_days",
        metavar="DAYS",
        help="Only show alerts from the last N days",
    )
    alerts_parser.add_argument(
        "--json",
        action="store_true",
        help="Output alerts as a JSON array",
    )

    graph_parser = subparsers.add_parser(
        "graph",
        help="Explore graph nodes and edges for an entity",
    )
    graph_parser.add_argument("--entity", type=str, required=True)
    graph_parser.add_argument("--depth", type=int, default=1)
    graph_parser.add_argument("--limit", type=int, default=100)
    graph_parser.add_argument("--json", action="store_true")

    graph_backfill_parser = subparsers.add_parser(
        "graph-backfill",
        help="Backfill graph nodes/edges from historical correlations",
    )
    graph_backfill_parser.add_argument("--limit", type=int, default=100)
    graph_backfill_parser.add_argument("--dry-run", action="store_true", dest="dry_run")
    graph_backfill_parser.add_argument("--json", action="store_true")

    targets_parser = subparsers.add_parser(
        "targets",
        help="Inspect target profiles and signal routing plan (read-only)",
    )
    targets_parser.add_argument(
        "--json",
        action="store_true",
        help="Output targets and route summary as JSON",
    )
    targets_parser.add_argument(
        "--path",
        type=str,
        default=None,
        help="Path to targets JSON file (default: data/targets.json)",
    )
    targets_parser.add_argument(
        "--show-routes",
        action="store_true",
        dest="show_routes",
        help="Include per-signal route details in output",
    )

    temporal_parser = subparsers.add_parser(
        "temporal",
        help="Inspect temporal activity patterns for callsigns and facilities",
    )
    temporal_grp = temporal_parser.add_mutually_exclusive_group()
    temporal_grp.add_argument(
        "--callsign",
        type=str,
        default=None,
        help="Show recurrence metrics for a specific callsign",
    )
    temporal_grp.add_argument(
        "--facility",
        type=str,
        default=None,
        help="Show activity summary for a specific facility",
    )
    temporal_grp.add_argument(
        "--recent",
        type=int,
        metavar="DAYS",
        default=None,
        help="Show most active callsigns and facilities in the last N days (default 7)",
    )
    temporal_parser.add_argument(
        "--json",
        action="store_true",
        help="Output as JSON",
    )

    tempo_parser = subparsers.add_parser(
        "tempo",
        help="Inspect operational tempo and mission recurrence for callsigns and facilities",
    )
    tempo_grp = tempo_parser.add_mutually_exclusive_group()
    tempo_grp.add_argument(
        "--callsign",
        type=str,
        default=None,
        help="Show operational tempo for a specific callsign",
    )
    tempo_grp.add_argument(
        "--facility",
        type=str,
        default=None,
        help="Show operational tempo for a specific facility",
    )
    tempo_grp.add_argument(
        "--recurring",
        action="store_true",
        default=False,
        help="List recurring callsigns exceeding observation and day thresholds",
    )
    tempo_parser.add_argument(
        "--min-observations",
        type=int,
        default=5,
        dest="min_observations",
        help="Minimum observations for --recurring filter (default: 5)",
    )
    tempo_parser.add_argument(
        "--min-days",
        type=int,
        default=3,
        dest="min_days",
        help="Minimum active days for --recurring filter (default: 3)",
    )
    tempo_parser.add_argument(
        "--limit",
        type=int,
        default=50,
        help="Maximum rows to return for --recurring (default: 50)",
    )
    tempo_parser.add_argument(
        "--json",
        action="store_true",
        help="Output as JSON",
    )

    callsigns_parser = subparsers.add_parser(
        "callsigns",
        help="Query persisted callsign observation history",
    )
    callsigns_parser.add_argument(
        "--limit",
        type=int,
        default=20,
        help="Maximum rows to return",
    )
    callsigns_parser.add_argument(
        "--callsign",
        type=str,
        default=None,
        help="Show history for a single callsign",
    )
    callsigns_parser.add_argument(
        "--json",
        action="store_true",
        help="Output as JSON",
    )

    profiles_parser = subparsers.add_parser(
        "profiles",
        help="List, show, or validate bundled intelligence profiles (read-only)",
    )
    profiles_parser.add_argument(
        "--show",
        type=str,
        default=None,
        metavar="PROFILE_ID",
        help="Show a single profile by id",
    )
    profiles_parser.add_argument(
        "--validate",
        action="store_true",
        help="Validate all bundled profiles and report an aggregate",
    )
    profiles_parser.add_argument(
        "--json",
        action="store_true",
        help="Output as JSON",
    )

    backup_readiness_parser = subparsers.add_parser(
        "backup-readiness",
        help="Inspect backup readiness against the /data boundary (read-only)",
    )
    backup_readiness_parser.add_argument(
        "--boundary",
        type=str,
        default="/data",
        help="Backup boundary path authoritative state must resolve under (default: /data)",
    )
    backup_readiness_parser.add_argument(
        "--data-dir",
        type=str,
        default=None,
        dest="data_dir",
        help="Override the inspected data directory (default: resolved from LIGHTHOUSE_DATA_DIR)",
    )
    backup_readiness_parser.add_argument(
        "--json",
        action="store_true",
        help="Output as JSON",
    )

    # --- Verified backup manifest + isolated restore drill (LHP-PROFILE-02) ---------------
    backup_manifest_parser = subparsers.add_parser(
        "backup-manifest",
        help="Generate a read-only backup manifest of authoritative state (copies NO data)",
    )
    backup_manifest_parser.add_argument(
        "--data-dir", required=True, dest="data_dir", help="Source data root to inventory"
    )
    backup_manifest_parser.add_argument(
        "--output", required=True, help="Path to write the manifest JSON"
    )
    backup_manifest_parser.add_argument(
        "--db-path", default=None, dest="db_path", help="Override the SQLite state DB path"
    )
    backup_manifest_parser.add_argument(
        "--boundary", default="/data", help="Backup boundary authoritative state must resolve under"
    )
    backup_manifest_parser.add_argument(
        "--include-reconstructable",
        action="store_true",
        dest="include_reconstructable",
        help="Also record reconstructable module output dirs (distinguished, non-required)",
    )
    backup_manifest_parser.add_argument("--json", action="store_true", help="Output as JSON")

    backup_create_parser = subparsers.add_parser(
        "backup-create",
        help="Create an independently-verified backup at an explicit destination",
    )
    backup_create_parser.add_argument(
        "--data-dir", required=True, dest="data_dir", help="Source data root to back up"
    )
    backup_create_parser.add_argument(
        "--destination", required=True, help="New/empty backup root (must not overlap the source)"
    )
    backup_create_parser.add_argument(
        "--db-path", default=None, dest="db_path", help="Override the SQLite state DB path"
    )
    backup_create_parser.add_argument(
        "--boundary", default="/data", help="Backup boundary authoritative state must resolve under"
    )
    backup_create_parser.add_argument(
        "--include-reconstructable",
        action="store_true",
        dest="include_reconstructable",
        help="Also back up reconstructable module output dirs",
    )
    backup_create_parser.add_argument("--json", action="store_true", help="Output as JSON")

    backup_verify_parser = subparsers.add_parser(
        "backup-verify",
        help="Independently verify an existing backup (read-only; nonzero exit on failure)",
    )
    backup_verify_parser.add_argument("--backup", required=True, help="Backup root to verify")
    backup_verify_parser.add_argument("--json", action="store_true", help="Output as JSON")

    restore_drill_parser = subparsers.add_parser(
        "restore-drill",
        help="Restore a backup into an isolated non-production destination and re-verify it",
    )
    restore_drill_parser.add_argument("--backup", required=True, help="Backup root to restore from")
    restore_drill_parser.add_argument(
        "--destination",
        required=True,
        help="New/empty isolated restore destination (never /data or beneath it)",
    )
    restore_drill_parser.add_argument(
        "--source-data-root",
        default=None,
        dest="source_data_root",
        help="Optional source data root, to reject a destination that overlaps it",
    )
    restore_drill_parser.add_argument(
        "--output",
        default=None,
        help="Optional path to write the restore-drill report JSON",
    )
    restore_drill_parser.add_argument("--json", action="store_true", help="Output as JSON")

    # --- Profile transition preview (LHP-PROFILE-03; read-only evidence artifact) ----------
    transition_parser = subparsers.add_parser(
        "profile-transition-preview",
        help="Generate a read-only profile-transition preview artifact (NO activation)",
    )
    transition_parser.add_argument(
        "--target-profile",
        required=True,
        dest="target_profile",
        help="Bundled profile_id the transition would move TO",
    )
    transition_parser.add_argument(
        "--current-profile",
        default=None,
        dest="current_profile",
        help=(
            "Optional operator-supplied bundled profile ID for comparison; "
            "transition preview does not derive it from active-profile state"
        ),
    )
    transition_parser.add_argument(
        "--backup-manifest",
        default=None,
        dest="backup_manifest",
        help="Path to a backup manifest.json (LHP-PROFILE-02) — evidence reference only",
    )
    transition_parser.add_argument(
        "--verification-report",
        default=None,
        dest="verification_report",
        help=(
            "Path to a typed lighthouse backup-verify report (status verified) bound to "
            "the supplied manifest. Not sufficient for profile-activate if the file is "
            "backup-create's embedded reports/verification.json (status passed); that "
            "embed also cannot make this preview ready_for_review"
        ),
    )
    transition_parser.add_argument(
        "--restore-report",
        default=None,
        dest="restore_report",
        help="Path to a restore-drill report — evidence reference only",
    )
    transition_parser.add_argument(
        "--output", default=None, help="Optional path to write the preview artifact JSON"
    )
    transition_parser.add_argument("--json", action="store_true", help="Output as JSON")

    # LHP-V1-PROFILE-01C — backup-gated initial RCD activation only (no switch).
    activate_parser = subparsers.add_parser(
        "profile-activate",
        help=(
            "Activate repository_capability_discovery v2 locally after verified backup/restore "
            "evidence (initial activation only; no switch)"
        ),
    )
    activate_parser.add_argument(
        "--target-profile",
        required=True,
        dest="target_profile",
        help="Must be repository_capability_discovery",
    )
    activate_parser.add_argument(
        "--target-version",
        required=True,
        type=int,
        dest="target_version",
        help="Must be 2",
    )
    activate_parser.add_argument(
        "--confirm",
        required=True,
        help="Exact typed confirmation: ACTIVATE repository_capability_discovery v2",
    )
    activate_parser.add_argument(
        "--transition-preview",
        required=True,
        dest="transition_preview",
        help="Path to a ready_for_review profile-transition-preview JSON",
    )
    activate_parser.add_argument(
        "--backup-root",
        required=True,
        dest="backup_root",
        help="Absolute path to the verified backup directory (must contain manifest.json/payload/)",
    )
    activate_parser.add_argument(
        "--backup-manifest",
        required=True,
        dest="backup_manifest",
        help="Path to the verified backup manifest.json",
    )
    activate_parser.add_argument(
        "--verification-report",
        required=True,
        dest="verification_report",
        help=(
            "Path to output from lighthouse backup-verify (status verified). "
            "backup-create's embedded reports/verification.json (status passed) is not sufficient"
        ),
    )
    activate_parser.add_argument(
        "--restore-report",
        required=True,
        dest="restore_report",
        help="Path to output from lighthouse restore-drill (status completed)",
    )
    activate_parser.add_argument(
        "--data-dir",
        default=None,
        dest="data_dir",
        help="Configured data snapshot root (defaults to LIGHTHOUSE_DATA_DIR)",
    )
    activate_parser.add_argument(
        "--dry-run",
        action="store_true",
        dest="dry_run",
        help="Validate the ceremony but write nothing",
    )
    activate_parser.add_argument("--json", action="store_true", help="Output as JSON")

    active_profile_parser = subparsers.add_parser(
        "active-profile",
        help="Show the active intelligence profile state (read-only; creates nothing)",
    )
    active_profile_parser.add_argument("--json", action="store_true", help="Output as JSON")

    # Local observation intake (LHP-INTEL-01E). Deliberately NOT a `--module` value: every one of
    # those is an async network collector that emits alerts. This reads the operator's own disk,
    # emits facts, and never touches the network — so it belongs with the other local commands.
    observe_parser = subparsers.add_parser(
        "observe",
        help=(
            "Observe the local repositories and documents declared in "
            "data/observation_sources.json (read-only; requires LIGHTHOUSE_OBSERVATION_ROOTS)"
        ),
    )
    observe_parser.add_argument(
        "--source",
        default=None,
        help="Observe only this declared source (must already be listed in the source file)",
    )
    observe_parser.add_argument(
        "--dry-run",
        action="store_true",
        dest="dry_run",
        help="Observe and normalize, but write nothing to the database",
    )
    observe_parser.add_argument("--json", action="store_true", help="Output as JSON")

    # `observations` — the read side of the observation layer (LHP-INTEL-02A). Read-only, and
    # enforced as such by the store: every query runs through a `mode=ro` SQLite connection.
    #
    # No `choices=` on the filter flags below, deliberately. The closed vocabularies live in
    # `observation_query`, which rejects an unknown value with a message that names the known ones —
    # one gate, shared by the CLI and by every consumer that comes after it.
    observations_parser = subparsers.add_parser(
        "observations",
        help="Inspect the observations Lighthouse has recorded (read-only; never writes)",
    )
    observation_actions = observations_parser.add_subparsers(dest="observations_command")

    observations_list = observation_actions.add_parser(
        "list", help="List the recorded observations, newest first"
    )
    observations_list.add_argument("--limit", type=int, default=None, help="Maximum rows to return")
    observations_list.add_argument("--json", action="store_true", help="Output as JSON")

    observations_show = observation_actions.add_parser(
        "show", help="Show one observation in full, with its provenance, entities and relationships"
    )
    observations_show.add_argument(
        "--id",
        required=True,
        help=(
            "The observation's SHA-256 digest, or an unambiguous prefix of at least 8 hex "
            "characters (as with git object ids; an ambiguous prefix is an error, never a guess)"
        ),
    )
    observations_show.add_argument("--json", action="store_true", help="Output as JSON")

    observations_search = observation_actions.add_parser(
        "search", help="Filter the recorded observations (exact matching only — no fuzzy search)"
    )
    observations_search.add_argument(
        "--scope-type", dest="scope_type", default=None, help="repository | document"
    )
    observations_search.add_argument(
        "--sensor",
        default=None,
        help="The sensor that established the facts: filesystem | git_metadata | text_extractor",
    )
    observations_search.add_argument(
        "--observation-type",
        dest="observation_type",
        default=None,
        help="An observation type, e.g. git.branch or text.character_count",
    )
    observations_search.add_argument(
        "--digest", default=None, help="Full 64-character observation digest"
    )
    observations_search.add_argument(
        "--since", default=None, help="Only observations observed at or after this ISO-8601 time"
    )
    observations_search.add_argument(
        "--until", default=None, help="Only observations observed at or before this ISO-8601 time"
    )
    observations_search.add_argument(
        "--limit", type=int, default=None, help="Maximum rows to return"
    )
    observations_search.add_argument("--json", action="store_true", help="Output as JSON")

    observations_stats = observation_actions.add_parser(
        "stats", help="Count what has been recorded (counts only — nothing is ranked)"
    )
    observations_stats.add_argument("--json", action="store_true", help="Output as JSON")

    # Cross-scope fact discovery (LHP-INTEL-02B). Exact lookup and exact set intersection over facts
    # that are already stored — never similarity, never inference, and never a judgement about which
    # of them matters.
    observations_where = observation_actions.add_parser(
        "where",
        help="Show every scope in which one exact entity was directly observed",
    )
    observations_where.add_argument(
        "--entity-id",
        dest="entity_id",
        required=True,
        help=(
            "The complete canonical entity id, e.g. technology:docker, language:Python, "
            "repository:<digest>. Exact only — an entity id is not a digest, so it has no prefix "
            "form (copy one from `observations show`)"
        ),
    )
    observations_where.add_argument(
        "--limit", type=int, default=None, help="Maximum occurrences to return"
    )
    observations_where.add_argument("--json", action="store_true", help="Output as JSON")

    observations_shared = observation_actions.add_parser(
        "shared",
        help="Show the exact entities directly observed in EVERY requested scope (intersection)",
    )
    observations_shared.add_argument(
        "--scope-id",
        dest="scope_id",
        action="append",
        metavar="SCOPE_ID",
        help=(
            "Repeat for each scope (at least two). A scope's SHA-256 id, or an unambiguous prefix "
            "of at least 8 hex characters; `observations list` prints them"
        ),
    )
    observations_shared.add_argument(
        "--limit", type=int, default=None, help="Maximum shared entities to return"
    )
    observations_shared.add_argument("--json", action="store_true", help="Output as JSON")

    # -- patterns (LHP-INTEL-03B) — the first candidate/review loop. It detects, presents and
    # records a decision. It does not score, rank, promote, publish, execute, or activate.
    patterns_parser = subparsers.add_parser(
        "patterns",
        help="Detect and review pattern candidates over stored facts (no scoring, no findings)",
    )
    pattern_actions = patterns_parser.add_subparsers(dest="patterns_command")

    patterns_detect = pattern_actions.add_parser(
        "detect",
        help="Run one declared detector over stored facts (repository recurrence by default)",
    )
    patterns_detect.add_argument(
        "--detector",
        default="repository_exact_content_recurrence",
        choices=["repository_exact_content_recurrence", "scientific_source_disappearance_review"],
        help=(
            "Which detector to run. repository_exact_content_recurrence (default) reads local "
            "observations; scientific_source_disappearance_review reads the public-source check "
            "ledger. Neither performs any network access."
        ),
    )
    patterns_detect.add_argument(
        "--dry-run",
        action="store_true",
        help="Build candidates in memory and write nothing at all — not a row, not a table",
    )
    patterns_detect.add_argument("--json", action="store_true", help="Output as JSON")

    patterns_list = pattern_actions.add_parser(
        "list", help="List candidates — latest version per candidate by default"
    )
    patterns_list.add_argument("--profile-id", default=None, help="Filter by owning profile")
    patterns_list.add_argument("--detector-id", default=None, help="Filter by detector")
    patterns_list.add_argument("--candidate-type", default=None, help="Filter by candidate type")
    patterns_list.add_argument("--candidate-key", default=None, help="Filter by candidate key")
    patterns_list.add_argument(
        "--disposition",
        default=None,
        help="Filter by operator decision: accepted_for_followup | dismissed | needs_more_evidence",
    )
    patterns_list.add_argument(
        "--all-versions",
        action="store_true",
        help="Show every stored version, not only the latest per candidate",
    )
    patterns_list.add_argument("--limit", type=int, default=None, help="Maximum candidates")
    patterns_list.add_argument("--json", action="store_true", help="Output as JSON")

    patterns_show = pattern_actions.add_parser(
        "show", help="Show one candidate in full: evidence, unknowns, limitations, decision"
    )
    patterns_show.add_argument(
        "--id",
        required=True,
        help=(
            "The full candidate id (pc_<sha256>) or an unambiguous hex prefix of at least 8 "
            "characters; an ambiguous prefix is an error that names every match"
        ),
    )
    patterns_show.add_argument("--json", action="store_true", help="Output as JSON")

    patterns_review = pattern_actions.add_parser(
        "review",
        help=(
            "Record one local operator decision about one exact candidate version. Not a finding, "
            "not an approval, and not authorization to publish or execute"
        ),
    )
    patterns_review.add_argument("--id", required=True, help="Candidate id or unambiguous prefix")
    patterns_review.add_argument("--operator-id", required=True, help="Who is deciding")
    patterns_review.add_argument(
        "--decision",
        required=True,
        help="accepted_for_followup | dismissed | needs_more_evidence",
    )
    patterns_review.add_argument("--reason", required=True, help="Why — recorded verbatim")
    patterns_review.add_argument("--json", action="store_true", help="Output as JSON")

    public_sources_parser = subparsers.add_parser(
        "public-sources",
        help="Check admitted public HTTPS locators and inspect the append-only check history",
    )
    public_source_actions = public_sources_parser.add_subparsers(dest="public_sources_command")

    ps_check = public_source_actions.add_parser(
        "check",
        help="Check each declared target once (HTTPS-only, allowlisted) and record the outcome",
    )
    ps_check.add_argument(
        "--targets-file",
        required=True,
        help="Path to a public_source_targets.v1 JSON work list of locators to check",
    )
    ps_check.add_argument(
        "--dry-run",
        action="store_true",
        help="Perform the checks but write nothing at all — not a row, not a table",
    )
    ps_check.add_argument("--json", action="store_true", help="Output as JSON")

    ps_runs = public_source_actions.add_parser(
        "runs", help="List completed check runs, or show one in full with --id"
    )
    ps_runs.add_argument(
        "--id",
        default=None,
        help="Show one run in full (psr_<sha256> or an unambiguous hex prefix of >=8 characters)",
    )
    ps_runs.add_argument("--limit", type=int, default=None, help="Maximum runs")
    ps_runs.add_argument("--json", action="store_true", help="Output as JSON")

    ps_history = public_source_actions.add_parser(
        "history",
        help="Chronological check history for one source, locator, or exact content digest",
    )
    ps_history_selector = ps_history.add_mutually_exclusive_group(required=True)
    ps_history_selector.add_argument("--source-id", default=None, help="public-source:<sha256>")
    ps_history_selector.add_argument(
        "--locator", default=None, help="An https:// locator (canonicalized to its id)"
    )
    ps_history_selector.add_argument("--locator-id", default=None, help="public-locator:<sha256>")
    ps_history_selector.add_argument(
        "--content-digest", default=None, help="A 64-char content body digest (content:<sha256>)"
    )
    ps_history.add_argument("--limit", type=int, default=None, help="Maximum checks")
    ps_history.add_argument("--json", action="store_true", help="Output as JSON")

    ps_show = public_source_actions.add_parser("show", help="Show one check in full")
    ps_show.add_argument(
        "--id",
        required=True,
        help="The full check id (psc_<sha256>) or an unambiguous hex prefix of >=8 characters",
    )
    ps_show.add_argument("--json", action="store_true", help="Output as JSON")

    ps_export = public_source_actions.add_parser(
        "export-osr",
        help="Export one observed_source_reference.v0 from a validated available check",
    )
    ps_export.add_argument(
        "--check-id",
        required=True,
        help="The full check id (psc_<sha256>) or an unambiguous hex prefix of >=8 characters",
    )
    ps_export.add_argument(
        "--consumer",
        required=True,
        choices=["article-to-skills", "offline-sovereign-stack"],
        help="Closed consumer profile for the exported reference",
    )
    ps_export.add_argument("--json", action="store_true", help="Output as JSON")

    return parser


def cli_main() -> None:
    run_id = str(uuid.uuid4())[:8]
    _configure_logging(run_id)
    args = _build_parser().parse_args()
    if args.command == "status":
        handle_status_command(args)
        return
    if args.command == "correlations":
        handle_correlations_command(args)
        return
    if args.command == "alerts":
        handle_alerts_command(args)
        return
    if args.command == "graph":
        handle_graph_command(args)
        return
    if args.command == "graph-backfill":
        handle_graph_backfill_command(args)
        return
    if args.command == "targets":
        handle_targets_command(args)
        return
    if args.command == "callsigns":
        handle_callsigns_command(args)
        return
    if args.command == "temporal":
        handle_temporal_command(args)
        return
    if args.command == "tempo":
        handle_tempo_command(args)
        return
    if args.command == "profiles":
        handle_profiles_command(args)
        return
    if args.command == "backup-readiness":
        handle_backup_readiness_command(args)
        return
    if args.command == "backup-manifest":
        handle_backup_manifest_command(args)
        return
    if args.command == "backup-create":
        handle_backup_create_command(args)
        return
    if args.command == "backup-verify":
        handle_backup_verify_command(args)
        return
    if args.command == "restore-drill":
        handle_restore_drill_command(args)
        return
    if args.command == "profile-transition-preview":
        handle_profile_transition_preview_command(args)
        return
    if args.command == "profile-activate":
        handle_profile_activate_command(args)
        return
    if args.command == "active-profile":
        handle_active_profile_command(args)
        return
    if args.command == "observe":
        handle_observe_command(args)
        return
    if args.command == "observations":
        handle_observations_command(args)
        return
    if args.command == "patterns":
        handle_patterns_command(args)
        return
    if args.command == "public-sources":
        handle_public_sources_command(args)
        return
    asyncio.run(main(args))


if __name__ == "__main__":
    cli_main()
