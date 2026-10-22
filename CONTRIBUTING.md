# Contributing to Lighthouse

> Product: **Lighthouse** · Git repository / package: `pattern-match-sentinel`. New here? Start with
> [`README.md`](README.md) and [`docs/ARCHITECTURE_INDEX.md`](docs/ARCHITECTURE_INDEX.md); architecture
> decisions live in [`docs/adr/`](docs/adr/README.md).

## Development Setup

```bash
git clone https://github.com/intrinsicinvestment91/lighthouse pattern-match-sentinel
cd pattern-match-sentinel

# Install with dev extras
pip install -e ".[dev]"

# Install Chromium for Playwright
make playwright-install

# Install pre-commit hooks
pre-commit install

# Copy and fill in env vars
cp .env.example .env
```

## Running Tests

Unit tests require no network access or API keys:

```bash
make test          # run all unit tests
make test-cov      # with coverage report
```

Integration tests hit live APIs and require real keys. Run them explicitly:

```bash
python -m pytest tests/integration -m integration -v
```

## Linting and Formatting

```bash
make lint          # check only
make format        # auto-fix
```

The CI pipeline runs `ruff check` and `ruff format --check` on every push. Pre-commit hooks run the same checks before each commit.

## Submitting a Pull Request

1. Fork the repository and create a branch from `main`.
2. Make your changes. Add or update tests as needed.
3. Ensure `make lint` and `make test` both pass.
4. Open a pull request. The CI pipeline will run automatically.

## Adding a New Agency Target

Edit the `targets` list in `ArchiveConfig` (`config.py`):

```python
{
    "agency": "FAA",
    "url": "https://www.faa.gov/news/press_releases/",
    "keywords": ["UAM", "UAS", "airspace", "propulsion"],
    "article_link_selector": "div.press-release a[href]",
}
```

The `article_link_selector` is optional but strongly recommended — without it the archiver falls back to a generic `a[href]` heuristic with lower precision.

## Updating the Relay List

Edit `NostrConfig.relays` in `config.py`. Each entry must be a `wss://` WebSocket URL. Test connectivity with:

```bash
python -c "import asyncio, websockets; asyncio.run(websockets.connect('wss://relay.damus.io', open_timeout=5).__aenter__())"
```

## Code Style

- Python 3.11+
- Line length: 100 characters
- Formatting: `ruff format`
- Linting: `ruff check` (E, F, I, UP, B, SIM rules)
- No new comments that describe *what* code does — only the *why* when non-obvious
