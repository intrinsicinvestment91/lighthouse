#!/usr/bin/env bash
# LHP-V1-OPERATOR-TRUTH-01 — full CLI-only disposable RCD ceremony + loop.
#
# backup-create → backup-verify → restore-drill --output → profile-transition-preview
# → profile-activate → observe (persist) → observations query → patterns detect
# → list/show/review → patterns detect again (idempotence).
#
# Throwaway LIGHTHOUSE_DATA_DIR / DB_PATH / observation roots only.
# Never touches the host production data/state.db.
set -euo pipefail

fail() {
  echo "rehearsal failed: $*" >&2
  exit 1
}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

if [[ -z "${LIGHTHOUSE_BIN:-}" && -n "${LIGHTHOUSE_VENV:-}" ]]; then
  LIGHTHOUSE_BIN="$LIGHTHOUSE_VENV/bin/lighthouse"
fi
if [[ -z "${LIGHTHOUSE_BIN:-}" ]]; then
  fail "set LIGHTHOUSE_BIN or LIGHTHOUSE_VENV"
fi
if [[ ! -f "$LIGHTHOUSE_BIN" || ! -x "$LIGHTHOUSE_BIN" ]]; then
  fail "LIGHTHOUSE_BIN is not executable: $LIGHTHOUSE_BIN"
fi

ROOT="$(mktemp -d "${TMPDIR:-/tmp}/lighthouse-v1-rehearsal-XXXXXX")"
[[ -n "$ROOT" && "$ROOT" == */lighthouse-v1-rehearsal-* ]] || fail "unsafe rehearsal root"
cleanup() { if [[ "${KEEP_REHEARSAL:-0}" == 1 ]]; then echo "Kept: $ROOT"; else rm -rf -- "$ROOT"; fi; }
trap cleanup EXIT
trap 'cleanup; trap - EXIT; exit 130' INT
trap 'cleanup; trap - EXIT; exit 143' TERM

DATA="$ROOT/data"
BACKUP="$ROOT/backup"
RESTORE="$ROOT/restore"
REPOS="$ROOT/repos"
mkdir -p "$DATA" "$REPOS" "$DATA/archives"

export LIGHTHOUSE_DATA_DIR="$DATA"
export DB_PATH="$DATA/state.db"
export LIGHTHOUSE_OBSERVATION_ROOTS="$REPOS"

echo "Rehearsal root: $ROOT"

if [[ "${REHEARSAL_USE_SEED:-}" == "1" ]]; then
  echo "Seed mode: real"
else
  echo "Seed mode: placeholder"
fi

# -- disposable authoritative state (stdlib sqlite3; no sqlite3 CLI required) ------------
python3 - "$DATA" "$DB_PATH" "$REPO_ROOT" <<'PY' || fail "disposable SQLite / seed setup"
import os
import sqlite3
import sys
from pathlib import Path

data, db_path, repo_root = map(Path, sys.argv[1:4])
conn = sqlite3.connect(db_path)
try:
    conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
    conn.execute("INSERT INTO t (v) VALUES ('rehearsal')")
    conn.commit()
finally:
    conn.close()

use_seed = os.environ.get("REHEARSAL_USE_SEED") == "1"
for name in ("entities.json", "targets.json"):
    src = repo_root / "seed" / name
    dst = data / name
    if use_seed and src.is_file():
        dst.write_bytes(src.read_bytes())
    else:
        dst.write_text("{}\n", encoding="utf-8")
(data / "archives" / "a.html").write_text("<html>rehearsal</html>\n", encoding="utf-8")
PY

# -- two synthetic repos: init, identity, identical bytes, add, commit, verify tracked ---
init_repo() {
  local repo="$1"
  mkdir -p "$repo"
  printf 'shared rehearsal bytes\n' >"$repo/README.md"
  git -C "$repo" init --quiet
  git -C "$repo" config user.name "v1-rehearsal"
  git -C "$repo" config user.email "v1-rehearsal@local"
  git -C "$repo" add README.md
  git -C "$repo" commit --quiet -m "rehearsal init"
  git -C "$repo" ls-files --error-unmatch README.md >/dev/null \
    || fail "README.md is not tracked in $repo"
  local tracked
  tracked="$(git -C "$repo" ls-files)"
  [[ "$tracked" == *README.md* ]] || fail "expected README.md in tracked files of $repo"
}

init_repo "$REPOS/alpha"
init_repo "$REPOS/beta"
cmp -s "$REPOS/alpha/README.md" "$REPOS/beta/README.md" \
  || fail "synthetic repos do not have identical file bytes"

python3 - "$DATA/observation_sources.json" "$REPOS/alpha" "$REPOS/beta" <<'PY' || fail "write observation_sources.json"
import json
import sys
from pathlib import Path

out, alpha, beta = map(Path, sys.argv[1:4])
out.write_text(
    json.dumps(
        {
            "schema_version": 1,
            "sources": [
                {"type": "repository", "path": str(alpha.resolve()), "enabled": True},
                {"type": "repository", "path": str(beta.resolve()), "enabled": True},
            ],
        },
        indent=2,
    )
    + "\n",
    encoding="utf-8",
)
PY

json_field() {
  local file="$1"
  local expr="$2"
  python3 -c 'import json,sys; d=json.load(open(sys.argv[1], encoding="utf-8")); print('"$expr"')' "$file"
}

run_json() {
  local step="$1"
  local dest="$2"
  shift 2
  echo "==> $step"
  "$@" --json >"$dest" || fail "$step (lighthouse exited $?)"
}

# -- ceremony ---------------------------------------------------------------------------
echo "==> backup-create"
"$LIGHTHOUSE_BIN" backup-create \
  --data-dir "$DATA" \
  --destination "$BACKUP" \
  --boundary "$DATA" \
  --db-path "$DB_PATH" \
  --json >"$ROOT/backup-create.json" \
  || fail "backup-create"
[[ "$(json_field "$ROOT/backup-create.json" 'd["status"]')" == "created" ]] \
  || fail "backup-create status is not created"

echo "==> backup-verify"
"$LIGHTHOUSE_BIN" backup-verify --backup "$BACKUP" --json >"$ROOT/verification.json" \
  || fail "backup-verify"
[[ "$(json_field "$ROOT/verification.json" 'd["status"]')" == "verified" ]] \
  || fail "backup-verify status is not verified (do not use backup-create reports/verification.json)"

echo "==> restore-drill --output"
"$LIGHTHOUSE_BIN" restore-drill \
  --backup "$BACKUP" \
  --destination "$RESTORE" \
  --source-data-root "$DATA" \
  --output "$ROOT/restore.json" \
  || fail "restore-drill"
[[ -f "$ROOT/restore.json" ]] || fail "restore-drill --output did not write $ROOT/restore.json"
[[ "$(json_field "$ROOT/restore.json" 'd["status"]')" == "completed" ]] \
  || fail "restore-drill status is not completed"

run_json "profile-transition-preview" "$ROOT/preview.json" \
  "$LIGHTHOUSE_BIN" profile-transition-preview \
    --target-profile repository_capability_discovery \
    --backup-manifest "$BACKUP/manifest.json" \
    --verification-report "$ROOT/verification.json" \
    --restore-report "$ROOT/restore.json" \
    --output "$ROOT/preview.json"
[[ "$(json_field "$ROOT/preview.json" 'd["transition_status"]')" == "ready_for_review" ]] \
  || fail "preview is not ready_for_review"

run_json "profile-activate" "$ROOT/activate.json" \
  "$LIGHTHOUSE_BIN" profile-activate \
    --target-profile repository_capability_discovery \
    --target-version 2 \
    --confirm "ACTIVATE repository_capability_discovery v2" \
    --transition-preview "$ROOT/preview.json" \
    --backup-root "$BACKUP" \
    --backup-manifest "$BACKUP/manifest.json" \
    --verification-report "$ROOT/verification.json" \
    --restore-report "$ROOT/restore.json" \
    --data-dir "$DATA"
[[ "$(json_field "$ROOT/activate.json" 'd["status"]')" == "activated" ]] \
  || fail "profile-activate status is not activated"

# -- RCD loop ---------------------------------------------------------------------------
run_json "observe (persist)" "$ROOT/observe.json" "$LIGHTHOUSE_BIN" observe
[[ "$(json_field "$ROOT/observe.json" 'd["failed"]')" == "0" ]] || fail "observe reported failures"
stored="$(json_field "$ROOT/observe.json" 'd["stored"]')"
[[ "$stored" == "2" ]] || fail "observe stored=$stored (expected 2)"

run_json "observations list" "$ROOT/observations.json" "$LIGHTHOUSE_BIN" observations list
obs_count="$(json_field "$ROOT/observations.json" 'd["count"]')"
[[ "$obs_count" -ge 2 ]] || fail "observations list count=$obs_count (expected >= 2)"

run_json "patterns detect" "$ROOT/detect.json" "$LIGHTHOUSE_BIN" patterns detect
[[ "$(json_field "$ROOT/detect.json" 'd["stored"]')" == "1" ]] \
  || fail "patterns detect stored != 1"

run_json "patterns list" "$ROOT/list.json" "$LIGHTHOUSE_BIN" patterns list
[[ "$(json_field "$ROOT/list.json" 'd["count"]')" == "1" ]] || fail "patterns list count != 1"
CANDIDATE_ID="$(json_field "$ROOT/list.json" 'd["candidates"][0]["candidate_id"]')"
[[ "$CANDIDATE_ID" == pc_* ]] || fail "candidate_id is not pc_…"

run_json "patterns show" "$ROOT/show.json" \
  "$LIGHTHOUSE_BIN" patterns show --id "$CANDIDATE_ID"
[[ "$(json_field "$ROOT/show.json" 'd["candidate"]["is_finding"]')" == "False" ]] \
  || fail "candidate claimed to be a finding"

run_json "patterns review" "$ROOT/review.json" \
  "$LIGHTHOUSE_BIN" patterns review \
    --id "$CANDIDATE_ID" \
    --operator-id v1-rehearsal \
    --decision needs_more_evidence \
    --reason "Repository lineage has not been assessed."
[[ "$(json_field "$ROOT/review.json" 'd["disposition"]["decision"]')" == "needs_more_evidence" ]] \
  || fail "review decision mismatch"

run_json "patterns detect (idempotent)" "$ROOT/detect2.json" "$LIGHTHOUSE_BIN" patterns detect
[[ "$(json_field "$ROOT/detect2.json" 'd["stored"]')" == "0" ]] \
  || fail "second detect stored != 0"
already="$(json_field "$ROOT/detect2.json" 'd["already_present"]')"
[[ "$already" -ge 1 ]] || fail "second detect already_present=$already (expected >= 1)"

echo
echo "Rehearsal OK."
echo "  root:              $ROOT"
echo "  activation:        $(json_field "$ROOT/activate.json" 'd["status"]')"
echo "  observe stored:    $stored"
echo "  observations:      $obs_count"
echo "  candidate_id:      $CANDIDATE_ID"
echo "  review:            needs_more_evidence"
echo "  second detect:     stored=0 already_present=$already"
if [[ "${KEEP_REHEARSAL:-0}" == 1 ]]; then
  echo "Rehearsal root kept: $ROOT"
else
  echo "Rehearsal root removed (on exit)"
fi
