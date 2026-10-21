#!/usr/bin/env bash
# Installs Lean 4 (via elan) and a Lake project with Mathlib for the lean_check tool.
# Core Lean alone is enough for Nat/Int/List proofs; Mathlib (several GB of
# prebuilt cache) is what makes real analysis, algebra and probability provable.
#   scripts/setup_lean.sh            # elan + Mathlib project in backend/lean
#   scripts/setup_lean.sh --core     # elan + core Lean only
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ELAN_HOME="${ELAN_HOME:-$HOME/.elan}"
if [ ! -x "$ELAN_HOME/bin/elan" ]; then
  curl -sSfL https://raw.githubusercontent.com/leanprover/elan/master/elan-init.sh -o /tmp/elan-init.sh
  sh /tmp/elan-init.sh -y --no-modify-path --default-toolchain leanprover/lean4:stable
fi
export PATH="$ELAN_HOME/bin:$PATH"
lean --version
if [ "${1:-}" = "--core" ]; then
  echo "Core Lean installed; the backend finds $ELAN_HOME/bin/lean automatically."
  exit 0
fi
PROJECT="$ROOT/backend/lean"
if [ ! -d "$PROJECT" ]; then
  (cd "$ROOT/backend" && lake new lean math)
fi
(cd "$PROJECT" && lake exe cache get)
echo
echo "Mathlib project ready. Add to backend/.env:"
echo "  LEAN_PROJECT_DIR=$PROJECT"
