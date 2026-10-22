#!/usr/bin/env bash
# Entry point for the pipeline image.
#
# Anything that is not one of the verbs below is passed straight to the CLI,
# so every documented `python -m pipeline.cli <cmd>` invocation works with the
# prefix dropped:
#
#   ./pipeline.sh check
#   ./pipeline.sh match --name SBP --description "Systolic blood pressure"
#   ./pipeline.sh batch data/columns.csv --out data/mappings.csv
#
# The verbs cover the things that are not the CLI:
#
#   ./pipeline.sh serve                            # the HTTP service
#   ./pipeline.sh test                             # the unittest suite
#   ./pipeline.sh python benchmarks/compare_models.py
#   ./pipeline.sh bash                             # a shell in the image
#
set -euo pipefail

case "${1:-}" in
  serve)
    # The one long-running verb: pipeline.service puts the matcher behind HTTP
    # so another application (the ETL Auto-Designer's Concepts step) can map a
    # whole column list in one call. Listen address comes from SERVICE_HOST /
    # SERVICE_PORT.
    shift
    exec python -m pipeline.service "$@"
    ;;
  test|tests)
    shift
    exec python -m unittest discover -s tests "$@"
    ;;
  python|python3)
    shift
    exec python "$@"
    ;;
  bash|sh)
    exec "$@"
    ;;
  *)
    exec python -m pipeline.cli "$@"
    ;;
esac
