#!/usr/bin/env bash
# Brings up the vocab Postgres container and runs the staged load:
#   raw COPY into staging -> filter into vocab schema -> indexes -> cleanup.
# Safe to re-run: each SQL step drops/recreates its own tables first.
set -euo pipefail

cd "$(dirname "$0")"

if [ -f .env ]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

POSTGRES_USER="${POSTGRES_USER:-vocab}"
POSTGRES_DB="${POSTGRES_DB:-athena_vocab}"
SERVICE="postgres"
COMPOSE=(docker compose)

echo "==> Starting Postgres container..."
"${COMPOSE[@]}" up -d

echo "==> Waiting for Postgres to accept connections..."
until "${COMPOSE[@]}" exec -T "$SERVICE" pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB" >/dev/null 2>&1; do
  sleep 2
done
echo "==> Postgres is up."

run_sql() {
  local file="$1"
  echo "==> [$(date +%T)] Running $file ..."
  local start end
  start=$(date +%s)
  # Piped over stdin rather than read from a /sql bind mount: a rewritten file
  # on the host can otherwise still be served stale inside the container.
  "${COMPOSE[@]}" exec -T "$SERVICE" psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
      -v ON_ERROR_STOP=1 -f - < "sql/$file"
  end=$(date +%s)
  echo "==> Done $file in $((end - start))s"
}

run_sql 01_staging_schema.sql
run_sql 02_load_staging.sql
run_sql 03_final_schema.sql
run_sql 04_load_filtered.sql
run_sql 05_indexes.sql
run_sql 06_cleanup.sql

echo "==> Load complete. Connect with:"
echo "    psql -h localhost -p ${POSTGRES_PORT:-5433} -U ${POSTGRES_USER} -d ${POSTGRES_DB}"
