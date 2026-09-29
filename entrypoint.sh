#!/usr/bin/env sh
# Container entrypoint: migrate, then serve.
#
# Running migrations here (rather than in a separate release step) is what makes
# `docker compose up` and a Railway deploy behave identically.
set -eu

: "${PORT:=8000}"
: "${WEB_CONCURRENCY:=2}"

if [ "${RUN_MIGRATIONS:-true}" = "true" ]; then
    # A database that is not ready *yet* is the normal case when the app and
    # its database start together, or when a managed database is waking. One
    # attempt turns that ordinary race into a dead container: the process
    # exits before the server starts, so /api/health — the one endpoint built
    # to report `database.connected: false` — never answers, and the platform
    # serves an opaque 502 with no way to tell a missing database from a
    # missing key from a crash.
    attempts="${MIGRATION_ATTEMPTS:-8}"
    delay="${MIGRATION_RETRY_SECONDS:-2}"
    attempt=1

    while :; do
        echo "Applying database migrations (attempt ${attempt}/${attempts})..."
        if alembic upgrade head; then
            break
        fi

        if [ "${attempt}" -ge "${attempts}" ]; then
            # Fail loudly rather than serving on a schema that may be wrong,
            # but say what to look at. Never print DATABASE_URL: it carries
            # the password.
            echo "" >&2
            echo "FATAL: database migrations failed ${attempts} times; not starting." >&2
            echo "       The server is deliberately not started, because the schema" >&2
            echo "       it would serve is unknown." >&2
            echo "" >&2
            echo "       Check, in this order:" >&2
            echo "         1. the database service is running and reachable" >&2
            echo "         2. DATABASE_URL names it (host, port, database, credentials)" >&2
            echo "         3. the error above, which is the real reason" >&2
            echo "" >&2
            echo "       On Windows use 127.0.0.1, not localhost." >&2
            exit 1
        fi

        echo "Migration attempt ${attempt} failed; retrying in ${delay}s..." >&2
        attempt=$((attempt + 1))
        sleep "${delay}"
    done
fi

echo "Starting ULUGBEK AI on port ${PORT}..."
exec uvicorn ulugbek_ai.main:app \
    --host 0.0.0.0 \
    --port "${PORT}" \
    --workers "${WEB_CONCURRENCY}" \
    --no-access-log
