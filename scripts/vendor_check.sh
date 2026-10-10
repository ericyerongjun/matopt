#!/usr/bin/env bash
# Staleness gate for every vendored upstream in this repository.
#
#   bash scripts/vendor_check.sh
#
# Runs `scripts/vendor_*.py --check` for each vendor script that supports it and
# exits non-zero if any vendored file drifted from the upstream commit pin it
# was generated from. This is the check CI (and anyone reviewing a vendored
# diff) should run; see vendor/UPSTREAM.md for the pins and the refresh commands.
#
# Convention: a vendor script that can be verified advertises a `--check` mode.
# Scripts without one are reported as skipped rather than silently ignored, so
# adding a new pin means adding `--check` to its script, not editing this file.
# Override the interpreter with PYTHON=/path/to/python3.

set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT" || exit 2

PYTHON="${PYTHON:-python3}"
checked=0
failed=0
skipped=0

shopt -s nullglob
for script in scripts/vendor_*.py; do
    name="$(basename "$script")"
    if ! grep -q -- '--check' "$script"; then
        printf 'SKIP  %s (no --check mode)\n' "$name"
        skipped=$((skipped + 1))
        continue
    fi
    printf 'CHECK %s\n' "$name"
    if "$PYTHON" "$script" --check; then
        printf 'PASS  %s\n' "$name"
        checked=$((checked + 1))
    else
        printf 'FAIL  %s\n' "$name"
        failed=$((failed + 1))
    fi
done
shopt -u nullglob

echo
printf 'vendor_check: %d passed, %d failed, %d skipped\n' "$checked" "$failed" "$skipped"

if [ "$failed" -ne 0 ]; then
    echo "vendored files drifted from their pins; re-run the vendor script(s) above" >&2
    exit 1
fi
if [ "$checked" -eq 0 ]; then
    echo "no vendor script was verified (nothing advertises --check)" >&2
    exit 2
fi
exit 0
