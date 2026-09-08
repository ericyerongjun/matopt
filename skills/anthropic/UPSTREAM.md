# Anthropic skills (vendored)

Upstream: https://github.com/anthropics/skills  
Commit: `41bbe19d1a1a7eaab5e7bb9050a417e5c6cffc8f`  
Synced: 2026-09-08

These folders are copied from Anthropic's public Agent Skills repository so MatOpt can load them through the same progressive-disclosure harness (catalog → `SKILL.md` → bundled files).

Refresh with:

```bash
./scripts/sync-anthropic-skills.sh
```

## Licence

Many example skills are Apache 2.0 (`LICENSE.txt` in the skill folder). The document skills `docx`, `pdf`, `pptx`, and `xlsx` are **source-available, not Apache 2.0** — see each folder's `LICENSE.txt` and Anthropic's terms. Do not treat those four as open source. `THIRD_PARTY_NOTICES.md` covers bundled third-party components.

Skill bodies are not modified. MatOpt only discovers, scores, and loads them.
