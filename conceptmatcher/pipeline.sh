#!/usr/bin/env bash
# Runs the matching pipeline in a one-off container of the stack's
# `conceptmatcher` service.
#
#   ./pipeline.sh check
#   ./pipeline.sh match --name SBP --description "Systolic blood pressure (mmHg)"
#   ./pipeline.sh batch data/columns.csv --out data/mappings.csv --report
#   ./pipeline.sh test
#   ./pipeline.sh python benchmarks/compare_models.py
#
# Paths are container paths: ./data on the host is /app/data inside, so
# `data/columns.csv` refers to the same file from either side.
#
# Postgres is started and waited for through compose's depends_on/healthcheck,
# so this works from cold — though the database needs the vocabulary loaded
# and ../scripts/setup-conceptmatcher.sh run once to have anything in it.
set -euo pipefail

cd "$(dirname "$0")"

# Created here rather than left to Docker: compose would create a missing bind
# mount source as root, and the container runs as uid 1000.
mkdir -p data

# The service mounts pipeline/ and data/ already. Tests and benchmarks are
# mounted here on top, so they run as checked out rather than as baked into
# the image; benchmarks read-write, because the scripts write CSVs next to
# their gold sets.
exec docker compose -f ../docker-compose.yml run --rm \
    -v "$PWD/tests:/app/tests:ro" \
    -v "$PWD/benchmarks:/app/benchmarks" \
    conceptmatcher "$@"
