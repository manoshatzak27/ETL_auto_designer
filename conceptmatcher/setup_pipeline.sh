#!/usr/bin/env bash
# Builds the database-side objects the matching pipeline needs on top of the
# already-loaded, filtered vocabulary:
#   extensions -> search matview + log/feedback tables -> indexes -> IDF table
#   -> spelling dictionary.
#
# Safe to re-run. Derived structures (matview, token_idf) are rebuilt; captured
# history (mapping logs, manual mappings, embedding cache) is preserved.
#
# Run ./load_vocab.sh first — this script assumes vocab.concept is populated.
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

# Fail fast with a clear message rather than building an empty matview.
concepts=$("${COMPOSE[@]}" exec -T "$SERVICE" psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
            -tAc "SELECT count(*) FROM vocab.concept" 2>/dev/null || echo 0)
if [ "${concepts//[!0-9]/}" = "0" ]; then
  echo "ERROR: vocab.concept is empty or missing. Run ./load_vocab.sh first." >&2
  exit 1
fi
echo "==> Postgres is up. vocab.concept has $concepts rows."

run_sql() {
  local file="$1"
  echo "==> [$(date +%T)] Running $file ..."
  local start end
  start=$(date +%s)
  "${COMPOSE[@]}" exec -T "$SERVICE" psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
      -v ON_ERROR_STOP=1 -f - < "sql/$file"
  end=$(date +%s)
  echo "==> Done $file in $((end - start))s"
}

run_sql 10_pipeline_extensions.sql
run_sql 11_pipeline_schema.sql
run_sql 12_pipeline_indexes.sql
run_sql 13_pipeline_idf.sql
run_sql 14_pipeline_spelling.sql

echo
echo "==> Pipeline schema ready."
echo "    Next: ./pipeline.sh match --name SBP --description 'Systolic blood pressure (mmHg)'"
