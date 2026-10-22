# ADR-0009 — No LLM in the deterministic core pipeline

- **Status:** Accepted
- **Date:** 2026-07-10
- **Related packets:** 1–28 (standing guardrail)
- **Supersedes:** —
- **Superseded-by:** —

## Context

Lighthouse's value depends on reproducible, auditable intelligence. Correlation scoring, witness
attestation, graph construction, and the explanation engine must produce the same output for the same
input, and must be defensible. Introducing an LLM into that path would make results non-deterministic,
non-reproducible, and harder to attest.

## Decision

- **No LLM is used in the deterministic core pipeline.** Correlation scoring
  (`modules/correlation_scoring.py`), the correlator, witness signing (`modules/witness.py`), graph
  construction, and the graph explainer (`modules/graph_explainer.py`) are all deterministic.
- LLM usage is confined to the **optional, edge** narrative analyzer (`modules/narrative_analyzer.py`),
  which requires an explicit API key and never gates the core path.

> **Correction (2026-10-22):** "witness signing" here is historical; `modules/witness.py` computes a
> deterministic SHA-256 witness hash and signs only when given a key, which the production path does
> not pass. The decision (no LLM in the core) is unchanged.

## Consequences

- Correlation/graph/explanation output is reproducible and attestable.
- Any future "smart" scoring must be deterministic or live at the optional edge, never in the core.

## Repository evidence

- `modules/correlation_scoring.py`, `modules/correlator.py`, `modules/witness.py`,
  `modules/graph_explainer.py` (stdlib-only, deterministic).
- Internal agent guide guardrails ("Do NOT introduce LLM usage into core pipeline").
- `modules/narrative_analyzer.py` (optional; requires `ANTHROPIC_API_KEY`).
