#!/usr/bin/env bash
set -euo pipefail

SERVICE_ROLE="${SERVICE_ROLE:-worker}"
PORT="${PORT:-8080}"

if [[ "${INSTALL_DEPS:-1}" == "1" ]]; then
  pip install -e .
fi

case "$SERVICE_ROLE" in
  web)
    exec uvicorn portal.app:app --host 0.0.0.0 --port "$PORT"
    ;;
  worker)
    exec lighthouse --module all
    ;;
  *)
    echo "Unsupported SERVICE_ROLE: $SERVICE_ROLE (expected: web|worker)" >&2
    exit 1
    ;;
esac
