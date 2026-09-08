#!/usr/bin/env bash
# Re-vendor the AMA-CMFAI/LAMBDA FastAPI backend into backend/lambda_app.
# Mechanical changes only: package rename (app → lambda_app) and MatOpt data-dir glue.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
exec python3 "$ROOT/scripts/vendor_lambda_backend.py" "$@"
