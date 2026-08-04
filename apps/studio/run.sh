#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"
# Load .env so the guard below sees what the Python app will see.
if [ -f .env ]; then
  set -a
  # shellcheck disable=SC1091
  . ./.env
  set +a
fi
HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-8080}"
if [ "$HOST" != "127.0.0.1" ] && [ "$HOST" != "localhost" ]; then
  if [ -z "${DASHBOARD_SECRET_KEY:-}" ] || [ "${DASHBOARD_SECRET_KEY:-}" = "local-dev-secret-change-me" ]; then
    echo "Refusing to bind $HOST with the default DASHBOARD_SECRET_KEY." >&2
    echo "Set a real DASHBOARD_SECRET_KEY in .env before exposing the app." >&2
    exit 1
  fi
fi
(cd frontend && pnpm build)
exec uv run uvicorn app.main:app --host "$HOST" --port "$PORT" --workers 1
