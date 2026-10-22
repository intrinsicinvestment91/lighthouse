# Lighthouse Static Explainer Site

Open `docs/site/index.html` in a browser — no build step, no server required.

This site is a human-readable explainer layer that mirrors the canonical Markdown docs. It is
**secondary** to Markdown — every substantive statement traces to a canonical doc. The navigation hub
for all documentation is [`docs/ARCHITECTURE_INDEX.md`](../ARCHITECTURE_INDEX.md); canonical sources
include `docs/CAPABILITIES.md`, `docs/ROADMAP.md`, `docs/ROADMAP_STATUS.md`, `docs/PACKETS.md`, `docs/FUTURE_PLAN.md`,
`docs/SECURITY.md`, `docs/adr/`, and `docs/OPERATIONS.md`.

## Pages

| File | Audience | Purpose |
|------|----------|---------|
| `index.html` | Everyone | Landing page, status, navigation |
| `overview.html` | Non-technical | What Lighthouse is and why it exists |
| `capabilities.html` | Everyone | Plain-English capability guide (mirrors `CAPABILITIES.md`) |
| `day-to-day.html` | Operators | Common commands and workflows |
| `operator.html` | Operators | Operator guide — runs, backups, rollback (mirrors `OPERATIONS.md`) |
| `targets.html` | Operators | Target profiles and signal routing |
| `start9.html` | Operators | Start9 deployment and data safety |
| `local-gateway.html` | Developers | Private loopback read-only intelligence gateway |
| `signals.html` | Anyone | Each signal source explained |
| `graph.html` | Anyone | Graph intelligence and callsigns |
| `profiles.html` | Anyone | Intelligence Profiles (declarative; RCD v2 initial activation only) |
| `architecture.html` | Developers | System architecture and data flow |
| `gateway-boundary.html` | Anyone | Gateway/Lighthouse ownership split (mirrors boundary doc) |
| `source-packs.html` | Anyone | Repository Source Packs (architecture-only) |
| `security.html` | Anyone | Security posture (`LHP-SECURITY-01`) |
| `adr.html` | Anyone | Architecture Decision Records index |
| `developer.html` | Developers | Setup, testing, invariants, adding packets |
| `roadmap.html` | Anyone | Architecture phases A–K; completed work |
| `troubleshooting.html` | Operators | Common problems and safe fixes |

The sidebar navigation is identical on every page (only the active marker moves). It is generated
consistently — do not hand-edit one page's nav in isolation.

## Maintenance

When canonical docs change in a way that materially affects operator-facing architecture, workflow,
status, or roadmap, update the corresponding page here. The site summarizes and links rather than
duplicating every line, so updates should be lightweight. Do not introduce architectural claims here
that are not backed by a canonical Markdown doc.
