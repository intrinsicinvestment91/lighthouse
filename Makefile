.PHONY: install lock sync sync-portal lint format test test-cov playwright-install docker-up docker-down clean mcp-dev

install:
	pip install -e ".[dev]"

lock:
	python -m pip install pip-tools
	python -m piptools compile pyproject.toml --output-file requirements.lock
	python -m piptools compile pyproject.toml --extra dev --output-file requirements-dev.lock
	python -m piptools compile pyproject.toml --extra portal --output-file requirements-portal.lock

sync:
	python -m pip install pip-tools
	python -m piptools sync requirements-dev.lock
	python -m pip install -e .

sync-portal:
	python -m pip install pip-tools
	python -m piptools sync requirements-portal.lock
	python -m pip install -e .

mcp-dev:
	pip install -e ".[portal]"
	mcp dev portal/mcp_server.py

lint:
	ruff check .
	ruff format --check .

format:
	ruff format .
	ruff check --fix .

test:
	python -m pytest tests/unit -v --tb=short

test-cov:
	python -m pytest tests/unit --cov=modules --cov-report=term-missing --cov-report=html

playwright-install:
	playwright install chromium

docker-up:
	docker compose up -d

docker-down:
	docker compose down

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .ruff_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name "*.egg-info" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name dist -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name htmlcov -exec rm -rf {} + 2>/dev/null || true
