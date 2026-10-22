"""
Module 3 — Decentralized Data Custodian (Nostr NIP-01).

Publishes signed metadata notes to Nostr relays.  Each note contains:
  - IPFS CIDs of archived government pages or patent snapshots
  - Links to public congressional / oversight transcripts
  - Geospatial bounding boxes of publicly reported Search-and-Rescue zones
    (sourced from USCG/NOAA public SAROPS broadcasts or FOIA releases)

The bot publishes facts only — no commentary, no analysis.

Requires:
    pip install websockets cryptography coincurve
    NOSTR_PRIVATE_KEY_HEX env var  (64-char hex secp256k1 scalar)
"""

import asyncio
import hashlib
import json
import logging
import time
from dataclasses import dataclass

import websockets
from coincurve import PrivateKey
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives.asymmetric.ec import (
    SECP256K1,
    EllipticCurvePublicKey,
    derive_private_key,
)

from config import nostr_cfg
from modules.retry import ws_retry

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# NIP-01 key management
# ---------------------------------------------------------------------------


def _load_private_key(hex_scalar: str):
    scalar = int(hex_scalar, 16)
    return derive_private_key(scalar, SECP256K1(), default_backend())


def _pubkey_hex(priv_key) -> str:
    """Return the 32-byte x-coordinate of the public key as hex (NIP-01 pubkey)."""
    pub: EllipticCurvePublicKey = priv_key.public_key()
    x = pub.public_numbers().x
    return x.to_bytes(32, "big").hex()


def _schnorr_sign(priv_key, message_bytes: bytes) -> str:
    """BIP-340 Schnorr signature over secp256k1 via `coincurve`."""
    raw_scalar = priv_key.private_numbers().private_value.to_bytes(32, "big")
    sig_bytes = PrivateKey(raw_scalar).sign_schnorr(message_bytes)
    return sig_bytes.hex()


def _upsert_tag(tags: list[list[str]], key: str, value: str) -> None:
    """Ensure a single [key, value] tag exists without duplicates."""
    replaced = False
    deduped: list[list[str]] = []
    for tag in tags:
        if len(tag) >= 2 and tag[0] == key:
            if not replaced:
                deduped.append([key, value])
                replaced = True
            continue
        deduped.append(tag)
    if not replaced:
        deduped.append([key, value])
    tags[:] = deduped


def enrich_mesh_tags(event: "NostrEvent", pubkey: str) -> None:
    """Attach node/region mesh tags to all outbound events."""
    _upsert_tag(event.tags, "node", pubkey)
    region = nostr_cfg.node_region.strip()
    if region:
        _upsert_tag(event.tags, "region", region)


# ---------------------------------------------------------------------------
# NIP-01 event construction
# ---------------------------------------------------------------------------


@dataclass
class NostrEvent:
    pubkey: str
    kind: int
    content: str
    tags: list[list[str]]
    created_at: int = 0
    id: str = ""
    sig: str = ""

    def compute_id(self) -> str:
        """SHA-256 of the canonical NIP-01 serialization."""
        serialized = json.dumps(
            [0, self.pubkey, self.created_at, self.kind, self.tags, self.content],
            separators=(",", ":"),
            ensure_ascii=False,
        )
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()

    def sign(self, priv_key) -> "NostrEvent":
        if not self.created_at:
            self.created_at = int(time.time())
        self.id = self.compute_id()
        self.sig = _schnorr_sign(priv_key, bytes.fromhex(self.id))
        return self

    def to_wire(self) -> dict:
        return {
            "id": self.id,
            "pubkey": self.pubkey,
            "created_at": self.created_at,
            "kind": self.kind,
            "tags": self.tags,
            "content": self.content,
            "sig": self.sig,
        }


# ---------------------------------------------------------------------------
# Metadata note builders
# ---------------------------------------------------------------------------


def build_archive_note(
    pubkey: str,
    agency: str,
    url: str,
    ipfs_cid: str,
    snapshot_ts: str,
    matched_keywords: list[str],
) -> NostrEvent:
    """Metadata note for a government press-release archive."""
    content = (
        f"[ARCHIVE] {agency} | {snapshot_ts}\n"
        f"Source: {url}\n"
        f"IPFS: ipfs://{ipfs_cid}\n"
        f"Keywords: {', '.join(matched_keywords)}"
    )
    tags = [
        ["t", "archive"],
        ["t", agency.lower()],
        ["r", url],
        ["ipfs", ipfs_cid],
        *[["t", kw.lower().replace(" ", "_")] for kw in matched_keywords],
    ]
    return NostrEvent(pubkey=pubkey, kind=nostr_cfg.event_kind, content=content, tags=tags)


def build_patent_note(
    pubkey: str,
    patent_id: str,
    title: str,
    assignee: str,
    filing_date: str,
    ipc_class: str,
    source: str,
    ipfs_cid: str | None = None,
) -> NostrEvent:
    """Metadata note for a newly detected patent filing."""
    ipfs_line = f"IPFS: ipfs://{ipfs_cid}\n" if ipfs_cid else ""
    content = (
        f"[PATENT] {source} {patent_id}\n"
        f"Title: {title}\n"
        f"Assignee: {assignee}\n"
        f"IPC: {ipc_class}  Filed/Published: {filing_date}\n"
        f"{ipfs_line}"
    )
    tags = [
        ["t", "patent"],
        ["t", ipc_class.lower()],
        ["t", source.lower()],
        ["assignee", assignee],
    ]
    if ipfs_cid:
        tags.append(["ipfs", ipfs_cid])
    return NostrEvent(pubkey=pubkey, kind=nostr_cfg.event_kind, content=content, tags=tags)


def build_transcript_note(
    pubkey: str,
    committee: str,
    hearing_date: str,
    transcript_url: str,
    summary: str,
    ipfs_cid: str | None = None,
) -> NostrEvent:
    """Metadata note for a publicly released congressional transcript."""
    ipfs_line = f"IPFS: ipfs://{ipfs_cid}\n" if ipfs_cid else ""
    content = (
        f"[TRANSCRIPT] {committee} | {hearing_date}\n"
        f"{summary}\n"
        f"Source: {transcript_url}\n"
        f"{ipfs_line}"
    )
    tags = [
        ["t", "transcript"],
        ["t", "congressional"],
        ["r", transcript_url],
    ]
    if ipfs_cid:
        tags.append(["ipfs", ipfs_cid])
    return NostrEvent(pubkey=pubkey, kind=nostr_cfg.event_kind, content=content, tags=tags)


def build_sar_zone_note(
    pubkey: str,
    incident_date: str,
    agency: str,
    bounding_box: dict[str, float],  # {north, south, east, west} decimal degrees
    source_url: str,
    description: str,
) -> NostrEvent:
    """
    Metadata note for a publicly reported Search-and-Rescue zone.
    Geospatial data sourced exclusively from official public broadcasts
    (USCG SAROPS, NOAA, FAA NOTAMs, public FOIA releases).
    """
    bbox_str = (
        f"N{bounding_box['north']:.4f} S{bounding_box['south']:.4f} "
        f"E{bounding_box['east']:.4f} W{bounding_box['west']:.4f}"
    )
    content = (
        f"[SAR-ZONE] {agency} | {incident_date}\n"
        f"BBox: {bbox_str}\n"
        f"{description}\n"
        f"Source: {source_url}"
    )
    tags = [
        ["t", "sar"],
        ["t", agency.lower().replace(" ", "_")],
        ["r", source_url],
        ["g", bbox_str],
    ]
    return NostrEvent(pubkey=pubkey, kind=nostr_cfg.event_kind, content=content, tags=tags)


def build_velocity_note(
    pubkey: str,
    ipc_class: str,
    anomaly_type: str,
    week: str,
    zscore: float,
    context: str,
) -> NostrEvent:
    """Metadata note for a patent filing rate anomaly (spike or freeze)."""
    content = (
        f"[VELOCITY-{anomaly_type.upper()}] IPC class {ipc_class}\n"
        f"Week: {week}  Z-score: {zscore:+.2f}\n"
        f"{context}"
    )
    tags = [
        ["t", "patent_velocity"],
        ["t", anomaly_type],
        ["t", ipc_class.lower()],
    ]
    return NostrEvent(pubkey=pubkey, kind=nostr_cfg.event_kind, content=content, tags=tags)


def build_researcher_note(
    pubkey: str,
    name: str,
    institution: str,
    last_pub_date: str,
    silent_days: int,
) -> NostrEvent:
    """Metadata note for a researcher who has stopped publishing on arXiv."""
    content = (
        f"[RESEARCHER-DARK] {name}\n"
        f"Institution: {institution}\n"
        f"Last arXiv publication: {last_pub_date}\n"
        f"Silence duration: {silent_days} days"
    )
    tags = [
        ["t", "researcher_dark"],
        ["t", "arxiv"],
    ]
    return NostrEvent(pubkey=pubkey, kind=nostr_cfg.event_kind, content=content, tags=tags)


def build_narrative_shift_note(
    pubkey: str,
    agency: str,
    url: str,
    urgency_delta: float,
    specificity_delta: float,
    summary: str,
) -> NostrEvent:
    """Metadata note for a detected shift in agency press-release tone."""
    content = (
        f"[NARRATIVE-SHIFT] {agency}\n"
        f"Urgency delta: {urgency_delta:+.1f}  Specificity delta: {specificity_delta:+.1f}\n"
        f"{summary}\n"
        f"Source: {url}"
    )
    tags = [
        ["t", "narrative_shift"],
        ["t", agency.lower().replace(" ", "_")],
        ["r", url],
    ]
    return NostrEvent(pubkey=pubkey, kind=nostr_cfg.event_kind, content=content, tags=tags)


def build_adsb_note(
    pubkey: str,
    facility: str,
    hex_code: str,
    callsign: str,
    lat: float | None,
    lon: float | None,
    military: bool,
    interesting: bool,
) -> NostrEvent:
    """Metadata note for an unusual aircraft observed via adsb.lol."""
    flags = [f for f, v in [("military", military), ("interesting", interesting)] if v]
    flag_str = ", ".join(flags) if flags else "callsign-pattern"
    pos_str = f"{lat:.4f},{lon:.4f}" if lat is not None and lon is not None else "unknown"
    content = (
        f"[ADSB] Unusual aircraft near {facility}\n"
        f"Hex: {hex_code}  Callsign: {callsign}\n"
        f"Flags: {flag_str}\n"
        f"Position: {pos_str}\n"
        f"Source: https://adsb.lol"
    )
    tags = [
        ["t", "adsb"],
        ["t", facility.lower().replace(" ", "_").replace("(", "").replace(")", "")],
        ["t", flag_str.replace(", ", "_")],
    ]
    if lat is not None and lon is not None:
        tags.append(["g", pos_str])
    return NostrEvent(pubkey=pubkey, kind=nostr_cfg.event_kind, content=content, tags=tags)


def build_sec_note(
    pubkey: str,
    company: str,
    form_type: str,
    filed_at: str,
    items: str,
    url: str,
    cik: str,
) -> NostrEvent:
    """Metadata note for a material SEC EDGAR filing from a watched company."""
    content = (
        f"[SEC-FILING] {company}\n"
        f"Form: {form_type}  Filed: {filed_at}\n"
        f"Items: {items}\n"
        f"CIK: {cik}\n"
        f"Source: {url}"
    )
    tags = [
        ["t", "sec_filing"],
        ["t", form_type.lower().replace("-", "")],
        ["t", company.lower().replace(" ", "_")],
        ["r", url],
    ]
    return NostrEvent(pubkey=pubkey, kind=nostr_cfg.event_kind, content=content, tags=tags)


# Keys must match the signal names emitted by modules/correlator.py (_build_event).
_SIGNAL_LABELS: dict[str, str] = {
    "sec": "SEC 8-K filing",
    "flight": "unusual aircraft (flight tracker)",
    "adsb": "ADS-B observation",
    "narrative": "agency press release tone shift",
}


def build_correlation_note(
    pubkey: str,
    event_type: str,
    entities: list[str],
    score: float,
    signals: list[str],
    summary: str,
) -> NostrEvent:
    """Human-readable note for a cross-source correlation event."""
    score_tier = "high" if score >= 8 else "medium" if score >= 6 else "low"
    entity = entities[0] if entities else "unknown"
    deduped_signals = sorted(set(signals))
    signal_lines = " · ".join(_SIGNAL_LABELS.get(s, s) for s in deduped_signals)
    content = (
        f"Signal detected — {entity}\n"
        f"Score: {round(score, 1)}/10 ({score_tier} confidence)\n\n"
        f"Corroborating sources: {signal_lines}\n\n"
        f"{summary}\n\n"
        "#lighthouse #osint #correlation"
    )
    tags: list[list[str]] = [
        ["t", "correlation"],
        ["t", "lighthouse"],
        ["score", score_tier],
        ["entity", entity],
    ]
    for signal in deduped_signals:
        tags.append(["signal", signal])
    return NostrEvent(pubkey=pubkey, kind=nostr_cfg.event_kind, content=content, tags=tags)


def build_dms_broadcast_note(
    pubkey: str,
    display_name: str,
    encrypted_message: str,
    registration_id: str,
) -> NostrEvent:
    """Dead Man's Switch broadcast — researcher missed check-in deadline."""
    content = (
        f"[DMS-BROADCAST] {display_name}\nRegistration: {registration_id}\n{encrypted_message}"
    )
    tags = [
        ["t", "dms"],
        ["t", "dead_mans_switch"],
        ["d", registration_id],
    ]
    return NostrEvent(pubkey=pubkey, kind=nostr_cfg.event_kind, content=content, tags=tags)


def build_node_heartbeat_note(
    pubkey: str,
    region: str,
    modules_active: list[str],
) -> NostrEvent:
    """Kind-30078 parametrized replaceable event announcing this node is alive."""
    ts = int(time.time())
    module_str = ", ".join(modules_active) if modules_active else "none"
    region_str = f" · Region: {region}" if region else ""
    content = f"Lighthouse node online{region_str}\nMonitoring: {module_str}\n\n#lighthouse #osint"
    tags: list[list[str]] = [
        ["t", "lighthouse-node"],
        ["d", pubkey],  # replaceable-event key: one canonical heartbeat per pubkey
        ["timestamp", str(ts)],
    ]
    event = NostrEvent(pubkey=pubkey, kind=30078, content=content, tags=tags)
    enrich_mesh_tags(event, pubkey)
    return event


async def broadcast_node_heartbeat(
    pubkey: str,
    region: str,
    modules_active: list[str],
) -> dict[str, bool]:
    """Build, sign, and broadcast a kind-30078 heartbeat. Returns {} if key is absent."""
    if not nostr_cfg.private_key_hex:
        logger.warning("[Nostr] NOSTR_PRIVATE_KEY_HEX not set — skipping heartbeat")
        return {}

    priv_key = _load_private_key(nostr_cfg.private_key_hex)
    event = build_node_heartbeat_note(pubkey=pubkey, region=region, modules_active=modules_active)
    event.sign(priv_key)
    results = await broadcast(event)
    ok_count = sum(1 for v in results.values() if v)
    logger.info("[Nostr] Heartbeat %s → %d/%d relays ok", event.id[:12], ok_count, len(results))
    return results


# ---------------------------------------------------------------------------
# Relay publisher
# ---------------------------------------------------------------------------


@ws_retry
async def publish_to_relay(event: NostrEvent, relay_url: str) -> bool:
    """Send a signed EVENT message to one relay; return True on OK."""
    message = json.dumps(["EVENT", event.to_wire()])
    async with websockets.connect(relay_url, open_timeout=nostr_cfg.ws_open_timeout) as ws:
        await ws.send(message)
        for _ in range(5):
            raw = await asyncio.wait_for(ws.recv(), timeout=nostr_cfg.ws_recv_timeout)
            resp = json.loads(raw)
            if resp[0] == "OK":
                logger.info("[Nostr] %s accepted %s", relay_url, event.id[:12])
                return True
            if resp[0] == "NOTICE":
                logger.warning("[Nostr] NOTICE from %s: %s", relay_url, resp[1])
    return False


async def broadcast(event: NostrEvent) -> dict[str, bool]:
    """Broadcast a signed event to all configured relays concurrently."""

    async def _publish(relay: str) -> tuple[str, bool]:
        try:
            result = await publish_to_relay(event, relay)
            return relay, result
        except Exception as exc:
            logger.warning("[Nostr] Relay %s failed after retries: %s", relay, exc)
            return relay, False

    results = await asyncio.gather(*[_publish(r) for r in nostr_cfg.relays])
    return dict(results)


# ---------------------------------------------------------------------------
# Bot entry point
# ---------------------------------------------------------------------------


async def run_nostr_bot(events: list[NostrEvent]) -> None:
    if not nostr_cfg.private_key_hex:
        raise ValueError("NOSTR_PRIVATE_KEY_HEX is not set")

    priv_key = _load_private_key(nostr_cfg.private_key_hex)
    pubkey = _pubkey_hex(priv_key)
    logger.info("[Nostr] Bot pubkey: %s", pubkey)

    for ev in events:
        ev.pubkey = pubkey
        enrich_mesh_tags(ev, pubkey)
        ev.sign(priv_key)
        results = await broadcast(ev)
        ok_count = sum(1 for v in results.values() if v)
        logger.info(
            "[Nostr] Event %s published to %d/%d relays",
            ev.id[:12],
            ok_count,
            len(results),
        )
        await asyncio.sleep(nostr_cfg.inter_event_sleep)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    priv = _load_private_key(nostr_cfg.private_key_hex)
    pub = _pubkey_hex(priv)

    sample_events = [
        build_archive_note(
            pubkey=pub,
            agency="DoD",
            url="https://www.defense.gov/News/News-Releases/News-Release-View/Article/example/",
            ipfs_cid="bafybeigdyrzt5sfp7udm7hu76uh7y26nf3efuylqabf3oeab8nhjrwxznm",
            snapshot_ts="20260101T000000Z",
            matched_keywords=["strategic initiative", "personnel"],
        ),
        build_sar_zone_note(
            pubkey=pub,
            incident_date="2026-01-15",
            agency="USCG District 11",
            bounding_box={"north": 34.5, "south": 33.8, "east": -117.2, "west": -118.1},
            source_url="https://www.navcen.uscg.gov/",
            description="Public SAROPS broadcast — mariners advised",
        ),
    ]

    asyncio.run(run_nostr_bot(sample_events))
