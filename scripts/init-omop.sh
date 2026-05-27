#!/bin/sh
# ──────────────────────────────────────────────────────────────────────────────
# omop-init service entrypoint.
# Applies the OMOP CDM v5.4 DDL + primary keys into ${OMOP_SCHEMA} on first run.
# A marker table (cdm_marker) keeps the operation idempotent.
# ──────────────────────────────────────────────────────────────────────────────
set -eu

SCHEMA="${OMOP_SCHEMA:-cdm}"
DDL_DIR="/ddl"

echo "[omop-init] target schema: ${SCHEMA}"
echo "[omop-init] waiting for postgres on ${PGHOST}…"
for i in $(seq 1 30); do
    if psql -h "$PGHOST" -U "$PGUSER" -d "$PGDATABASE" -c "select 1" >/dev/null 2>&1; then
        break
    fi
    sleep 1
done

echo "[omop-init] creating schema if not exists"
psql -v ON_ERROR_STOP=1 -h "$PGHOST" -U "$PGUSER" -d "$PGDATABASE" \
    -c "CREATE SCHEMA IF NOT EXISTS \"${SCHEMA}\";"

echo "[omop-init] checking for marker table"
ALREADY=$(psql -tA -h "$PGHOST" -U "$PGUSER" -d "$PGDATABASE" \
    -c "SELECT EXISTS (SELECT 1 FROM information_schema.tables
                       WHERE table_schema = '${SCHEMA}' AND table_name = 'cdm_marker')")
if [ "$ALREADY" = "t" ]; then
    echo "[omop-init] cdm_marker already present in ${SCHEMA} — skipping DDL"
    exit 0
fi

apply() {
    file="$1"
    if [ -f "${DDL_DIR}/${file}" ]; then
        echo "[omop-init] applying ${file}"
        # OHDSI DDL uses @cdmDatabaseSchema as a placeholder — substitute then pipe to psql
        sed "s/@cdmDatabaseSchema/${SCHEMA}/g" "${DDL_DIR}/${file}" | \
            psql -v ON_ERROR_STOP=1 -h "$PGHOST" -U "$PGUSER" -d "$PGDATABASE"
    else
        echo "[omop-init] WARNING: ${file} not found in ${DDL_DIR} — skipping"
    fi
}

apply "OMOPCDM_postgresql_5.4_ddl.sql"
apply "OMOPCDM_postgresql_5.4_primary_keys.sql"
# constraints + indices are deferred until after vocabulary + ETL load
# apply "OMOPCDM_postgresql_5.4_constraints.sql"
# apply "OMOPCDM_postgresql_5.4_indices.sql"

echo "[omop-init] writing marker"
psql -v ON_ERROR_STOP=1 -h "$PGHOST" -U "$PGUSER" -d "$PGDATABASE" -c "
CREATE TABLE IF NOT EXISTS \"${SCHEMA}\".cdm_marker (
    applied_at timestamp NOT NULL DEFAULT now(),
    note text
);
INSERT INTO \"${SCHEMA}\".cdm_marker (note) VALUES ('OMOP CDM v5.4 DDL + PKs applied');
"

echo "[omop-init] done."
