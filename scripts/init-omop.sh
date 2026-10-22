#!/bin/sh
# ──────────────────────────────────────────────────────────────────────────────
# omop-init service entrypoint.
# Waits for Postgres to be reachable, then delegates schema creation + DDL
# application to the Python ddl_applier module so vocab and clinical tables go
# into separate schemas. Idempotent via per-schema marker rows.
# ──────────────────────────────────────────────────────────────────────────────
set -eu

PGHOST="${PGHOST:-postgres}"
PGUSER="${PGUSER:-omop}"
PGDATABASE="${PGDATABASE:-omop}"

echo "[omop-init] waiting for postgres on ${PGHOST}…"
for i in $(seq 1 60); do
    if pg_isready -h "$PGHOST" -U "$PGUSER" -d "$PGDATABASE" >/dev/null 2>&1; then
        break
    fi
    sleep 1
done

cd /app
exec python -m app.services.ddl_applier bootstrap
