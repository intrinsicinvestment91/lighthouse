#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

mkdir -p logs backups

echo "[prod-run] Validating entity seed..."
python scripts/validate_entities.py data/entities.json

echo "[prod-run] Running unit test precheck with clean Nostr env..."
env -u NOSTR_PRIVATE_KEY_HEX -u NOSTR_PUBLISH_CORRELATIONS pytest tests/unit -v --tb=short

if [[ -f "data/state.db" ]]; then
  backup_path="backups/state-before-run-$(date +%Y%m%d-%H%M%S).db"
  cp data/state.db "$backup_path"
  echo "[prod-run] Backed up state DB to $backup_path"
else
  echo "[prod-run] data/state.db not found; skipping DB backup"
fi

if [[ -f ".env" ]]; then
  echo "[prod-run] Loading .env"
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
else
  echo "[prod-run] .env not found; using current shell environment"
fi

timestamp="$(date +%Y%m%d-%H%M%S)"
log_path="logs/live-${timestamp}.log"

echo "[prod-run] Starting Lighthouse live run (since-days=${SINCE_DAYS:-7})"
echo "[prod-run] Logging to $log_path"

lighthouse --module all --since-days "${SINCE_DAYS:-7}" > "$log_path" 2>&1

echo "[prod-run] Completed successfully"
echo "[prod-run] Inspect with: tail -200 \"$log_path\""

# Usage:
#   SINCE_DAYS=7 scripts/run_lighthouse_prod.sh
