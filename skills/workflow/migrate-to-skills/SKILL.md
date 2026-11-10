---
name: migrate-to-skills
description: >
  Convert rules and slash commands from other agents (Claude Code
  .claude/commands, Cursor .cursor/commands and .cursor/rules/*.mdc,
  .cursorrules, Windsurf rules, Copilot instructions) into MatOpt Agent
  Skills and AGENTS.md. Use for /migrate-to-skills.
disable-model-invocation: true
license: Apache-2.0
metadata:
  author: matopt
  version: "1.0"
---

# Migrate rules and commands to skills

MCP servers, existing skills and CLAUDE.md files are imported by **Settings → Integrations → Import**; this skill
handles what that importer does not: commands and scoped rules.

1. Dry run from the project root:
   `python skills/migrate-to-skills/scripts/convert.py --root . --out converted-skills`
   It maps:
   - command files (`.claude/commands/**/*.md`, `.cursor/commands/*.md`, `.codex/prompts/*.md`) → one skill each,
     `disable-model-invocation: true`, `$ARGUMENTS`/`$1…` explained in the body;
   - Cursor rules with a `description` (agent-requested) → a skill whose description is that text;
   - always-apply rules (`alwaysApply: true`, `.cursorrules`, `.windsurfrules`, `.github/copilot-instructions.md`)
     → sections of `converted-skills/AGENTS.md` (persistent guidance, not skills);
   - glob-scoped rules → a skill whose description names the globs.
2. Read `converted-skills/REPORT.md`; review every generated SKILL.md: tighten descriptions (what + when + limits),
   drop tool-specific instructions that do not apply to MatOpt, keep names kebab-case.
3. Validate: each folder name equals its `name:`; descriptions ≤ 1024 characters.
4. With the user's agreement, copy skills to `~/.matopt/skills/` and merge the AGENTS.md text (see `/create-rule`).
   Never delete the originals.
