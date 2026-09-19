#!/bin/sh
# Bring the schema up to date, then run whatever was asked for.
#
# The API container owns migrations; the worker waits for them rather than
# racing to apply the same revisions. Alembic is idempotent, but two processes
# stamping the same revision at once is a needless way to find that out.
set -e

if [ "${RUN_MIGRATIONS:-true}" = "true" ]; then
  echo "[entrypoint] applying migrations"
  # Postgres may still be accepting connections a moment after the healthcheck
  # passes, so a short retry beats a container that dies on first boot.
  attempt=1
  until alembic upgrade head; do
    if [ "$attempt" -ge 10 ]; then
      echo "[entrypoint] migrations failed after $attempt attempts" >&2
      exit 1
    fi
    echo "[entrypoint] database not ready (attempt $attempt), retrying in 2s"
    attempt=$((attempt + 1))
    sleep 2
  done
  echo "[entrypoint] schema is up to date"
fi

exec "$@"
