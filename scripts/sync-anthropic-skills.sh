#!/usr/bin/env bash
# Refresh skills/anthropic from github.com/anthropics/skills (sparse, skills/ only).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DEST="$ROOT/skills/anthropic"
TMP="$(mktemp -d "${TMPDIR:-/tmp}/matopt-skills.XXXXXX")"
trap 'rm -rf "$TMP"' EXIT

git clone --depth 1 https://github.com/anthropics/skills.git "$TMP/src"
SHA="$(git -C "$TMP/src" rev-parse HEAD)"
mkdir -p "$DEST"
rsync -a --delete --exclude UPSTREAM.md "$TMP/src/skills/" "$DEST/"
cp "$TMP/src/THIRD_PARTY_NOTICES.md" "$DEST/THIRD_PARTY_NOTICES.md"
mkdir -p "$ROOT/skills/spec"
cp "$TMP/src/spec/agent-skills-spec.md" "$ROOT/skills/spec/agent-skills-spec.md"
cp "$TMP/src/template/SKILL.md" "$ROOT/skills/spec/template.SKILL.md"

cat > "$DEST/UPSTREAM.md" <<EOF
# Anthropic skills (vendored)

Upstream: https://github.com/anthropics/skills
Commit: \`$SHA\`
Synced: $(date -u +%Y-%m-%d)

Refresh with:

    ./scripts/sync-anthropic-skills.sh
EOF

echo "Synced anthropic skills at $SHA"
