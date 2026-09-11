#!/usr/bin/env sh
# Container entrypoint: migrate, then serve.
#
# Running migrations here (rather than in a separate release step) is what makes
# `docker compose up` and a Railway deploy behave identically.
set -eu

: "${PORT:=8000}"
: "${WEB_CONCURRENCY:=2}"

if [ "${RUN_MIGRATIONS:-true}" = "true" ]; then
    echo "Applying database migrations..."
    alembic upgrade head
fi

echo "Starting ULUGBEK AI on port ${PORT}..."
exec uvicorn ulugbek_ai.main:app \
    --host 0.0.0.0 \
    --port "${PORT}" \
    --workers "${WEB_CONCURRENCY}" \
    --no-access-log
