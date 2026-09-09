#!/usr/bin/env bash
# Runs the matching pipeline in its container.
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
# The image builds itself on first use. Postgres is started and waited for
# through compose's depends_on/healthcheck, so this works from cold — though
# the database still needs ./load_vocab.sh and ./setup_pipeline.sh to have
# been run once to have anything in it.
set -euo pipefail

cd "$(dirname "$0")"

# Created here rather than left to Docker: compose would create a missing bind
# mount source as root, and the container runs as uid 1000.
mkdir -p data

exec docker compose run --rm pipeline "$@"
