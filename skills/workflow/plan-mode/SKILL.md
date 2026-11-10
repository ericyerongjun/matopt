---
name: plan-mode
description: >
  Plan a bounded task before building it: read the relevant code and data,
  write a short reviewable plan, and wait for the user's go-ahead before any
  edit. Use for /plan-mode or "plan first, then build".
disable-model-invocation: true
license: Apache-2.0
metadata:
  author: matopt
  version: "1.0"
---

# Plan mode: plan a bounded task, review, then build

While planning you **only read**: `read_file`, `list_files`, `grep`, `find_files`, `web_search`, read-only MCP tools.
No `write_file`, `edit_file`, state-changing shell commands or installs until the user approves.

1. Restate the task in one sentence and its boundary (what is explicitly out of scope).
2. Explore just enough: entry points, data shapes, existing tests, conventions.
3. Write `PLAN.md` in the workspace (the one file you may write) and mirror the steps with `update_plan`:
   - Goal and acceptance checks (observable behavior, the test command);
   - Steps: small, ordered, each naming the files it touches;
   - Decisions and the alternatives rejected; risks; open questions for the user.
4. Stop and ask: "Build this plan?" Do not continue in the same turn.
5. When the user approves (or edits the plan), build step by step, keep `update_plan` current, and finish with the
   acceptance checks. For requirements that must outlive this change, suggest `/openspec-propose` instead.
