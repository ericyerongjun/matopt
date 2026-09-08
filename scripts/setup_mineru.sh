#!/usr/bin/env bash
# Installs MinerU (PDF/image → Markdown+LaTeX OCR) into an isolated virtualenv
# (backend/.venv-mineru) because its dependencies conflict with the main backend.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PY="${PYTHON:-}"
if [ -z "$PY" ]; then
  for cand in /opt/homebrew/opt/python@3.12/bin/python3.12 python3.12 python3.11 python3.13; do
    if command -v "$cand" >/dev/null 2>&1; then PY="$cand"; break; fi
  done
fi
[ -n "$PY" ] || { echo "Need Python 3.10-3.13 (brew install python@3.12)" >&2; exit 1; }
VENV="$ROOT/backend/.venv-mineru"
[ -d "$VENV" ] || "$PY" -m venv "$VENV"
"$VENV/bin/python" -m pip install --upgrade pip
"$VENV/bin/python" -m pip install "mineru[core]"
echo
echo "MinerU installed at $VENV/bin/mineru (auto-detected by the backend)."
echo "Models are downloaded on first use (set MINERU_MODEL_SOURCE=modelscope in backend/.env if HuggingFace is slow)."
echo "Pre-download now with:  $VENV/bin/mineru-models-download -s huggingface -m pipeline"
