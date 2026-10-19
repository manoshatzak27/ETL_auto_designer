#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────────
# Builds the concept matcher's database objects in the stack's own Postgres, on
# top of the Athena vocabulary already loaded into the `vocab` schema:
#   extensions -> pipeline.concept_search matview + log/feedback tables ->
#   indexes -> IDF table -> spelling dictionary   (conceptmatcher/sql/10-14)
#
# Run it once after the vocabulary is first loaded (Finalize step, card 1), and
# again after every vocabulary reload: the matview and IDF table are derived
# from vocab.concept and do not follow it on their own. ~5 min on a full Athena
# export. Safe to re-run — derived structures are rebuilt, captured history
# (mapping logs, manual mappings, embedding cache) is kept.
#
#   ./scripts/setup-conceptmatcher.sh
#   docker compose restart conceptmatcher
# ──────────────────────────────────────────────────────────────────────────────
set -euo pipefail

cd "$(dirname "$0")/.."

if [ -f .env ]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

POSTGRES_USER="${POSTGRES_USER:-omop}"
POSTGRES_DB="${POSTGRES_DB:-omop}"

# The matcher's SQL and queries name the `vocab` schema directly.
if [ "${OMOP_VOCAB_SCHEMA:-vocab}" != "vocab" ]; then
  echo "ERROR: the concept matcher needs OMOP_VOCAB_SCHEMA=vocab (is '${OMOP_VOCAB_SCHEMA}')." >&2
  exit 1
fi

psql_exec() {
  # The builds over the full vocabulary want more memory than Postgres's
  # defaults. Parallel workers are switched off for this session: they share
  # memory through /dev/shm, which is 64MB in the postgres container, and a
  # build that outgrows it fails midway with "could not resize shared memory".
  docker compose exec -T \
      -e PGOPTIONS="-c maintenance_work_mem=1GB -c work_mem=256MB -c max_parallel_maintenance_workers=0 -c max_parallel_workers_per_gather=0" \
      postgres psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1 "$@"
}

concepts=$(psql_exec -tAc "SELECT count(*) FROM vocab.concept" 2>/dev/null || echo 0)
if [ "${concepts//[!0-9]/}" = "0" ] || [ -z "${concepts//[!0-9]/}" ]; then
  echo "ERROR: vocab.concept is empty or missing. Load the vocabulary first (Finalize step, card 1)." >&2
  exit 1
fi
echo "==> vocab.concept has $concepts rows."

for file in 10_pipeline_extensions 11_pipeline_schema 12_pipeline_indexes \
            13_pipeline_idf 14_pipeline_spelling; do
  echo "==> [$(date +%T)] $file.sql"
  start=$(date +%s)
  # Piped over stdin: the SQL lives in this checkout, not in the container.
  psql_exec -q -f - < "conceptmatcher/sql/$file.sql"
  echo "    done in $(( $(date +%s) - start ))s"
done

echo
echo "==> Concept matcher schema ready. Pick it up with:"
echo "    docker compose restart conceptmatcher"
