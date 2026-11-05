---
name: create-skill
description: >
  Create a reusable Agent Skill (SKILL.md with frontmatter, instructions and
  optional scripts) for a recurring task, with a predictable output format.
  Use for /create-skill or "turn this into a skill".
disable-model-invocation: true
license: Apache-2.0
metadata:
  author: matopt
  version: "1.0"
---

# Create a skill: reusable instructions + a predictable output format

1. Pin down: the task, when it should trigger (and when not), the inputs, the exact outputs (file names, format),
   and the limits ("writes the JSON and a report only; does not import or edit application code").
2. Write `skills/<name>/SKILL.md` in the workspace:
   ```markdown
   ---
   name: prepare-reading-batch          # kebab-case, equals the folder name, ≤ 64 chars
   description: Prepare a JSON file … Use when … Writes … only; does not …   # ≤ 1024 chars; what + when + limits
   disable-model-invocation: true       # optional: only runs as /name, never auto-activated
   ---
   # Title
   Short rules first (use real sources; don't invent; don't change application code), then numbered steps,
   then the output format.
   ```
   Put deterministic work (validation, conversion) in `scripts/` and call it from the steps; put long reference
   material in `references/`.
3. Test it: run the scripts on a realistic input, then walk through the steps once on a real example.
4. Install: personal skills go in `~/.matopt/skills/<name>/` (or a folder listed in `SKILLS_DIRS`), shared ones in the
   repository's `skills/` tree; they load at the next backend start and appear in the `/` menu. Copy only with the
   user's agreement. For evaluation loops and description tuning, `load_skill skill-creator`.
