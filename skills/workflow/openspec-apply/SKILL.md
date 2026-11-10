---
name: openspec-apply
description: >
  Apply an approved OpenSpec change: implement tasks.md in order, tick each
  task as it is done, check every acceptance scenario with tests and in the
  browser, and report progress. Use for /openspec-apply or "implement the
  change" after the proposal was reviewed.
disable-model-invocation: true
license: Apache-2.0
metadata:
  author: matopt
  version: "1.0"
---

# Apply the change (OpenSpec)

1. `python skills/openspec/scripts/openspec.py status <change>`. If the proposal still has Open Questions, stop and
   ask; do not guess behavior the user has not decided.
2. Read the delta spec, design.md and tasks.md. Mirror the task list with `update_plan`.
3. Implement tasks **in order**. After each one, change its `- [ ]` to `- [x]` in tasks.md. Keep to the scope of the
   change; do not add behavior the spec does not ask for (no extra edit/delete controls, no network calls when the
   spec says local).
4. Check the scenarios:
   - write automated tests that name the scenarios (e.g. `node --test reading-board.test.mjs`, `pytest`);
   - for UI, exercise each scenario in a real browser on a local URL (`http://127.0.0.1:<port>`), including failure
     paths such as corrupt storage or a blocked write; undo any temporary test overrides before you finish.
5. Report like:
   `Change: <name> Schema: spec-driven Progress: 9/9 tasks complete`, then the completed tasks, the test command and
   its result, and which scenarios were verified in the browser. List anything that did not match the spec.
6. Do not archive here; `/openspec-archive` does that once the user accepts the result.
