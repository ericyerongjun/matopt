---
name: create-rule
description: >
  Write persistent agent guidance (a rule) as AGENTS.md instructions: coding
  conventions, project facts, do/don't lists that apply to every turn. Use
  for /create-rule or "always do X from now on".
disable-model-invocation: true
license: Apache-2.0
metadata:
  author: matopt
  version: "1.0"
---

# Create a rule (persistent agent guidance)

MatOpt reads `AGENTS.md` (or `AGENTS.override.md`, which wins) from three places:

| Scope | File | Who sees it |
|---|---|---|
| Every conversation, this user | `~/.matopt/AGENTS.md` | operator scope, part of the system prompt |
| Every conversation, this server | `<data_dir>/AGENTS.md` | operator scope |
| This conversation only | `AGENTS.md` in the workspace root | per-turn workspace context |

1. Turn the request into rules that are specific and checkable: "Use `pytest -q` from `backend/`", not "test well".
   One bullet per rule, grouped under short headings; add the reason when it is not obvious.
2. Prefer a skill over a rule for a multi-step procedure that is only sometimes needed (`/create-skill`); prefer a
   hook for something that must be enforced, not merely advised (`/create-hook`).
3. Workspace scope: edit `AGENTS.md` in the workspace directly (append; keep existing rules). Operator scope: show
   the exact text and the path, and write it only if the user asks and the path is writable; it applies from the
   next conversation.
4. Keep the whole file short (the total is capped at 32 KiB); remove rules that contradict the new one and say so.
