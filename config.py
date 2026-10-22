"""Central configuration for Project Lighthouse."""

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from modules.data_paths import data_path as _canonical_data_path


def _data_path(rel: str) -> str:
    """Resolve a path relative to LIGHTHOUSE_DATA_DIR (defaults to 'data')."""
    return _canonical_data_path(rel)


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _parse_observation_roots() -> list[str]:
    """The roots under which local observation is permitted — empty unless the operator says so.

    `LIGHTHOUSE_OBSERVATION_ROOTS` is `os.pathsep`-separated (PATH-style). There is deliberately no
    default: an empty list is a valid configuration and means *nothing is observable*, which is what
    the deployed container should be — it has no git, and the operator's repositories and documents
    are not mounted into it.
    """
    raw = os.getenv("LIGHTHOUSE_OBSERVATION_ROOTS", "").strip()
    if not raw:
        return []
    return [root.strip() for root in raw.split(os.pathsep) if root.strip()]


def _parse_public_source_hosts() -> list[str]:
    """The hosts `lighthouse public-sources` may reach — empty unless the operator says so.

    `LIGHTHOUSE_PUBLIC_SOURCE_HOSTS` is a comma-separated list of exact hostnames. There is
    deliberately no default: an empty list is valid and means *nothing is reachable*, so a check
    refuses rather than trusting the network. The targets work list may only narrow this; it has no
    field in which a host could be added.
    """
    raw = os.getenv("LIGHTHOUSE_PUBLIC_SOURCE_HOSTS", "").strip()
    if not raw:
        return []
    return [host.strip() for host in raw.split(",") if host.strip()]


def _parse_relays_from_env() -> list[str]:
    raw = os.getenv("NOSTR_RELAYS", "").strip()
    if not raw:
        return [
            "wss://relay.damus.io",
            "wss://nostr.wine",
            "wss://relay.nostr.band",
            "wss://nos.lol",
        ]
    relays = [relay.strip() for relay in raw.split(",") if relay.strip()]
    return relays or [
        "wss://relay.damus.io",
        "wss://nostr.wine",
        "wss://relay.nostr.band",
        "wss://nos.lol",
    ]


@dataclass
class PatentConfig:
    # IPC/CPC classification codes to monitor
    classes: list[str] = field(
        default_factory=lambda: [
            "C22C",  # High-Entropy Alloys
            "G21B",  # Plasma Confinement / Fusion Energy
            "B64G",  # Spacecraft / Exotic Propulsion
        ]
    )
    # Assignees of interest (partial-match, case-insensitive)
    assignees_of_interest: list[str] = field(
        default_factory=lambda: [
            "JPL",
            "Jet Propulsion Laboratory",
            "Aerojet",
            "Rocketdyne",
            "MIT",
            "DARPA",
            "Lockheed",
            "General Atomics",
            "TAE Technologies",
            "Commonwealth Fusion",
            "Helion",
        ]
    )
    # Patent provider: disabled | legacy_patentsview | odp
    # Default is disabled because search.patentsview.org is decommissioned.
    patent_provider: str = os.getenv("PATENT_PROVIDER", "disabled")
    # Legacy PatentsView Search API base (known stale; requires explicit opt-in)
    patentsview_api: str = os.getenv(
        "PATENTSVIEW_BASE_URL", "https://search.patentsview.org/api/v1"
    )
    # Future USPTO Open Data Portal API base (set when confirmed and implemented)
    uspto_odp_api: str = os.getenv("USPTO_ODP_BASE_URL", "")
    # Optional API key if required by the active provider
    patentsview_api_key: str = os.getenv("PATENTSVIEW_API_KEY", "")
    # WIPO PATENTSCOPE API base (requires free registration for key)
    wipo_api: str = "https://patentscope.wipo.int/rest/v2"
    wipo_api_key: str = os.getenv("WIPO_API_KEY", "")
    wipo_page_size: int = 100
    # Output path
    output_dir: str = field(default_factory=lambda: _data_path("patents"))
    # Playwright fallback: fetch full-text HTML for flagged patents with empty abstracts
    use_playwright_fallback: bool = True
    # Timeouts
    http_timeout: float = 30.0
    playwright_timeout_ms: int = 60_000
    playwright_settle_ms: int = 2_000
    inter_class_delay: float = 1.0


@dataclass
class ArchiveConfig:
    targets: list[dict] = field(
        default_factory=lambda: [
            {
                "agency": "DoD",
                "url": "https://www.defense.gov/News/News-Releases/",
                "feed_url": (
                    "https://www.defense.gov/DesktopModules/ArticleCS/RSS.ashx"
                    "?ContentType=9&Site=945&max=50"
                ),
                "keywords": ["personnel", "strategic", "special access", "SAP", "initiative"],
                "article_link_selector": "div.news-releases a[href]",
            },
            {
                "agency": "NASA",
                "url": "https://www.nasa.gov/news/releases/",
                "feed_url": "https://www.nasa.gov/news-release/feed/",
                "keywords": ["personnel", "strategic", "initiative", "program office"],
                "article_link_selector": "article.news a[href]",
            },
            {
                "agency": "DoE",
                "url": "https://www.energy.gov/newsroom",
                "keywords": ["personnel", "strategic", "special access", "initiative", "audit"],
                "article_link_selector": "div.newsroom-article a[href]",
            },
        ]
    )
    output_dir: str = field(default_factory=lambda: _data_path("archives"))
    # IPFS node — defaults to local daemon; swap for a pinning service endpoint
    ipfs_api_url: str = os.getenv("IPFS_API_URL", "/ip4/127.0.0.1/tcp/5001")
    # Pinning backend selection: "local" | "pinata" | "web3storage"
    ipfs_pinning_service: str = os.getenv("IPFS_PINNING_SERVICE", "local").strip().lower()
    # Optional provider credentials for remote pinning backends
    pinata_jwt: str = os.getenv("PINATA_JWT", "")
    web3storage_token: str = os.getenv("WEB3STORAGE_TOKEN", "")
    # Polite crawl delay (seconds) — respect server load
    crawl_delay: float = 3.0
    max_pages_per_target: int = 50
    # Timeouts
    http_timeout: float = 30.0
    playwright_goto_timeout_ms: int = 60_000
    playwright_settle_ms: int = 2_000


@dataclass
class NostrConfig:
    # Generate once with cryptography, then set NOSTR_PRIVATE_KEY_HEX.
    private_key_hex: str = os.getenv("NOSTR_PRIVATE_KEY_HEX", "")
    relays: list[str] = field(default_factory=_parse_relays_from_env)
    output_dir: str = field(default_factory=lambda: _data_path("nostr"))
    # NIP-01 event kind for metadata notes
    event_kind: int = 1
    # Timeouts and pacing
    ws_open_timeout: float = 10.0
    ws_recv_timeout: float = 5.0
    inter_event_sleep: float = 0.5
    relay_max_retries: int = 3
    node_region: str = os.getenv("LIGHTHOUSE_REGION", "")
    publish_correlations: bool = _env_bool("NOSTR_PUBLISH_CORRELATIONS", False)


@dataclass
class AppConfig:
    # Persistent SQLite cache for cross-run deduplication
    state_db_path: str = field(default_factory=lambda: _data_path("state.db"))


@dataclass
class VelocityConfig:
    spike_zscore_threshold: float = 2.0
    freeze_weeks: int = 4
    min_history_weeks: int = 8
    output_dir: str = field(default_factory=lambda: _data_path("velocity"))


_ARXIV_RESEARCHERS_FALLBACK: list[dict[str, str]] = [
    {
        "name": "Synthetic Researcher A",
        "institution": "Synthetic Fusion Plasma Institute",
        "arxiv_query": 'au:"Synthetic_A"',
    },
    {
        "name": "Synthetic Researcher B",
        "institution": "Synthetic Fusion Plasma Institute",
        "arxiv_query": 'au:"Synthetic_B"',
    },
    {
        "name": "Synthetic Researcher C",
        "institution": "Synthetic Fusion Plasma Institute",
        "arxiv_query": 'au:"Synthetic_C"',
    },
    {
        "name": "Synthetic Researcher D",
        "institution": "Synthetic Fusion Plasma Institute",
        "arxiv_query": 'au:"Synthetic_D"',
    },
    {
        "name": "Synthetic Researcher E",
        "institution": "Synthetic Fusion Plasma Institute",
        "arxiv_query": 'au:"Synthetic_E"',
    },
    {
        "name": "Synthetic Researcher F",
        "institution": "Synthetic Inertial Confinement Laboratory",
        "arxiv_query": 'au:"Synthetic_F"',
    },
    {
        "name": "Synthetic Researcher G",
        "institution": "Synthetic Inertial Confinement Laboratory",
        "arxiv_query": 'au:"Synthetic_G"',
    },
    {
        "name": "Synthetic Researcher H",
        "institution": "Synthetic Inertial Confinement Laboratory",
        "arxiv_query": 'au:"Synthetic_H"',
    },
    {
        "name": "Synthetic Researcher I",
        "institution": "Synthetic Inertial Confinement Laboratory",
        "arxiv_query": 'au:"Synthetic_I"',
    },
    {
        "name": "Synthetic Researcher J",
        "institution": "Synthetic Inertial Confinement Laboratory",
        "arxiv_query": 'au:"Synthetic_J"',
    },
]


def _load_seed_researchers(path: str | Path = "seed/researchers.json") -> list[dict[str, str]]:
    """Load arXiv researcher roster from seed JSON, or fallback if the file is absent."""
    p = Path(path)
    if not p.is_file():
        return list(_ARXIV_RESEARCHERS_FALLBACK)

    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in researcher seed {p}: {exc}") from exc

    if not isinstance(raw, dict):
        raise ValueError(
            f"Researcher seed {p} must be a JSON object with a 'researchers' array, "
            f"got {type(raw).__name__}"
        )

    researchers = raw.get("researchers")
    if not isinstance(researchers, list):
        raise ValueError(
            f"Researcher seed {p} missing non-empty array 'researchers' "
            f"(got {type(researchers).__name__})"
        )

    out: list[dict[str, str]] = []
    for i, row in enumerate(researchers):
        if not isinstance(row, dict):
            raise ValueError(
                f"Researcher seed {p} researchers[{i}] must be an object, got {type(row).__name__}"
            )
        for key in ("name", "institution", "arxiv_query"):
            val = row.get(key)
            if not isinstance(val, str) or not val.strip():
                raise ValueError(
                    f"Researcher seed {p} researchers[{i}] invalid or empty string field {key!r}"
                )
        out.append(
            {
                "name": str(row["name"]).strip(),
                "institution": str(row["institution"]).strip(),
                "arxiv_query": str(row["arxiv_query"]).strip(),
            }
        )
    return out


@dataclass
class ArxivConfig:
    researchers: list[dict[str, str]] = field(default_factory=_load_seed_researchers)
    silence_threshold_days: int = 60
    arxiv_base: str = "https://export.arxiv.org/api/query"
    http_timeout: float = 20.0
    output_dir: str = field(default_factory=lambda: _data_path("arxiv"))


@dataclass
class ProxyConfig:
    proxies: list[str] = field(
        default_factory=lambda: [
            p.strip() for p in os.getenv("PROXY_LIST", "").split(",") if p.strip()
        ]
    )
    strategy: str = "round_robin"  # "round_robin" | "random"


@dataclass
class NarrativeConfig:
    anthropic_api_key: str = field(default_factory=lambda: os.getenv("ANTHROPIC_API_KEY", ""))
    model: str = "claude-haiku-4-5-20251001"
    shift_threshold: float = 2.5
    context_window_days: int = 30
    output_dir: str = field(default_factory=lambda: _data_path("narrative"))


@dataclass
class FlightConfig:
    facilities: list[dict] = field(
        default_factory=lambda: [
            {
                "name": "NIF (LLNL)",
                "lat": 37.6913,
                "lon": -121.7055,
                "radius_km": 50,
                "aliases": [
                    "Lawrence Livermore National Laboratory",
                    "LLNL",
                    "NIF",
                    "National Ignition Facility",
                ],
            },
            {
                "name": "DARPA HQ",
                "lat": 38.8796,
                "lon": -77.1063,
                "radius_km": 30,
                "aliases": [
                    "DARPA",
                    "Defense Advanced Research Projects Agency",
                    "DARPA HQ",
                ],
            },
            {
                "name": "Helion Everett",
                "lat": 47.9793,
                "lon": -122.2021,
                "radius_km": 40,
                "aliases": ["Helion", "Helion Energy", "Helion Everett"],
            },
            {
                "name": "Commonwealth Fusion (SPARC)",
                "lat": 42.3601,
                "lon": -71.2394,
                "radius_km": 30,
                "aliases": [
                    "Commonwealth Fusion Systems",
                    "CFS",
                    "SPARC",
                    "Commonwealth Fusion",
                ],
            },
            # NC targets (A2)
            {"name": "Greensboro/PTI", "lat": 36.0978, "lon": -79.9373, "radius_km": 50},
            {
                "name": "Research Triangle Park",
                "lat": 35.8990,
                "lon": -78.8709,
                "radius_km": 40,
                "aliases": [
                    "Research Triangle Park",
                    "RTP",
                    "North Carolina defense corridor",
                ],
            },
            {
                "name": "Camp Lejeune",
                "lat": 34.6710,
                "lon": -77.3414,
                "radius_km": 50,
                "aliases": [
                    "Camp Lejeune",
                    "Marine Corps Base Camp Lejeune",
                    "USMC Camp Lejeune",
                    "government defense labs",
                    "government defense",
                ],
            },
        ]
    )
    unusual_callsign_patterns: list[str] = field(
        default_factory=lambda: ["EXEC", "PAT", "SPAR", "JTARS", "AFSOC"]
    )
    opensky_base: str = "https://opensky-network.org/api"
    opensky_user: str = field(default_factory=lambda: os.getenv("OPENSKY_USER", ""))
    opensky_pass: str = field(default_factory=lambda: os.getenv("OPENSKY_PASS", ""))
    scan_interval_hours: int = 6
    http_timeout: float = 30.0
    output_dir: str = field(default_factory=lambda: _data_path("flights"))


@dataclass
class DmsConfig:
    web_host: str = "0.0.0.0"
    web_port: int = 8080
    check_interval_hours: int = 1
    max_interval_hours: int = 720  # 30 days
    db_path: str = field(default_factory=lambda: _data_path("state.db"))


@dataclass
class AdsbConfig:
    api_key: str = field(default_factory=lambda: os.getenv("ADSB_EXCHANGE_API_KEY", ""))
    adsb_lol_base: str = "https://api.adsb.lol/v2"
    # US military ICAO hex block starts with AE (AE0000–AFFFFF)
    military_hex_prefixes: list[str] = field(default_factory=lambda: ["ae"])
    http_timeout: float = 30.0
    output_dir: str = field(default_factory=lambda: _data_path("adsb"))


@dataclass
class SecConfig:
    # Form types to monitor — 8-K covers material corporate events
    form_types: list[str] = field(default_factory=lambda: ["8-K"])
    # 8-K item numbers that indicate material events worth alerting on
    material_items: list[str] = field(
        default_factory=lambda: ["1.01", "1.02", "2.01", "5.02", "8.01"]
    )
    # SEC fair-access policy asks automated clients to declare a User-Agent with an organization
    # name and contact email, e.g. "Example Org admin@example.org". Set SEC_USER_AGENT; there is
    # deliberately no default, and the SEC sentinel refuses to run while it is unset.
    user_agent: str = field(default_factory=lambda: os.getenv("SEC_USER_AGENT", "").strip())
    http_timeout: float = 30.0
    output_dir: str = field(default_factory=lambda: _data_path("sec"))
    efts_base: str = "https://efts.sec.gov/LATEST/search-index"


@dataclass
class CorrelatorConfig:
    entities_path: str = field(default_factory=lambda: _data_path("entities.json"))
    # Max hours between a flight observation and an SEC 8-K filing to flag as correlated
    window_flight_sec_hours: int = 48
    # Max days between a patent anomaly and a researcher silence alert
    window_patent_silence_days: int = 30
    # Rolling window for triple-signal detection
    window_triple_signal_days: int = 14
    output_dir: str = field(default_factory=lambda: _data_path("correlations"))


@dataclass
class ObservationConfig:
    """Local observation intake (LHP-INTEL-01E) — the trust boundary lives here.

    `allowed_roots` is the **authoritative policy boundary** for the observation layer: a repository
    or document is observable only if it resolves inside one of these roots (symlink escape
    included). The environment variable is only its initial source — modules consume this config,
    never the environment.

    It is **empty by default, and empty fails closed**: with no roots configured, `lighthouse
    observe` refuses to run rather than implicitly trusting the filesystem. Nothing on the
    operator's disk is observable until they say so — there is no default path, and no
    home-directory scan.

    `data/observation_sources.json` (the declared work list) may only *narrow* what is observed
    within these roots. It can never widen them; it has no field in which a root could be written.
    """

    sources_path: str = field(default_factory=lambda: _data_path("observation_sources.json"))
    allowed_roots: list[str] = field(default_factory=lambda: _parse_observation_roots())


@dataclass
class PublicSourceConfig:
    """Public-source check intake (LHP-INTEL-04B) — the network trust boundary lives here.

    `allowed_hosts` is the authoritative allowlist for `lighthouse public-sources`. It is **empty
    by default, and empty fails closed**: with no hosts configured, a check refuses rather than
    reaching the network. The environment variable is only its initial source — the adapter consumes
    this config, never the environment.

    `data/public_source_targets.json` (the declared work list) may only *narrow* what is checked. It
    can never widen the allowlist; it has no field in which a host could be written.
    """

    targets_path: str = field(default_factory=lambda: _data_path("public_source_targets.json"))
    allowed_hosts: list[str] = field(default_factory=_parse_public_source_hosts)


patent_cfg = PatentConfig()
archive_cfg = ArchiveConfig()
nostr_cfg = NostrConfig()
app_cfg = AppConfig()
velocity_cfg = VelocityConfig()
arxiv_cfg = ArxivConfig()
proxy_cfg = ProxyConfig()
narrative_cfg = NarrativeConfig()
flight_cfg = FlightConfig()
dms_cfg = DmsConfig()
adsb_cfg = AdsbConfig()
sec_cfg = SecConfig()
correlator_cfg = CorrelatorConfig()
observation_cfg = ObservationConfig()
public_source_cfg = PublicSourceConfig()


def get_nostr_cfg() -> NostrConfig:
    return nostr_cfg


def get_patent_cfg() -> PatentConfig:
    return patent_cfg


def get_sec_cfg() -> SecConfig:
    return sec_cfg


def get_velocity_cfg() -> VelocityConfig:
    return velocity_cfg


def get_arxiv_cfg() -> ArxivConfig:
    return arxiv_cfg


def get_narrative_cfg() -> NarrativeConfig:
    return narrative_cfg


def get_correlator_cfg() -> CorrelatorConfig:
    return correlator_cfg


def get_observation_cfg() -> ObservationConfig:
    return observation_cfg


def get_public_source_cfg() -> PublicSourceConfig:
    return public_source_cfg
