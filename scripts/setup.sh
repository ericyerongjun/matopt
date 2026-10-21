#!/usr/bin/env bash
# One-shot developer setup for MatOpt (macOS/Linux). Uses Homebrew Python + pip (no uv).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PY="${PYTHON:-}"
if [ -z "$PY" ]; then
  for cand in /opt/homebrew/opt/python@3.12/bin/python3.12 python3.12 python3.13 python3.11; do
    if command -v "$cand" >/dev/null 2>&1; then PY="$cand"; break; fi
  done
fi
if [ -z "$PY" ]; then
  echo "Python 3.11+ not found. Install with: brew install python@3.12" >&2; exit 1
fi
echo "Using $PY ($($PY --version))"

cd "$ROOT/backend"
[ -d .venv ] || "$PY" -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt -c constraints.txt
[ -f .env ] || cp .env.example .env
mkdir -p data

cd "$ROOT/frontend"
if command -v npm >/dev/null 2>&1; then npm ci; else echo "npm not found — install Node 22+ to build the frontend" >&2; fi

cat <<MSG

Done. Next steps:
  1. Set DEEPSEEK_API_KEY in backend/.env (the live API model is deepseek-flash).
  2. Backend:  cd backend && .venv/bin/python main.py        (http://localhost:8000)
  3. Frontend: cd frontend && npm run dev                    (http://localhost:5173)
     The isolated interactive UI runtime also starts on port 5174.
  Optional OCR: scripts/setup_mineru.sh
MSG
