---
name: goal
description: >
  Work toward a long-term goal across turns: record the goal, success
  criteria and milestones in GOAL.md, make progress on the next milestone
  each turn, and keep the file current. Use for /goal <objective>, or /goal
  alone to continue or review the current goal.
disable-model-invocation: true
license: Apache-2.0
metadata:
  author: matopt
  version: "1.0"
---

# /goal: work toward a long-term goal

`GOAL.md` in the workspace is the durable state. Read it first on every `/goal`.

**`/goal <objective>` (new goal)**
1. Write `GOAL.md`: Objective; Success criteria (measurable, how each is checked); Constraints; Milestones as a
   checkbox list (each finishable in one turn); Log (dated one-line entries); Next step.
2. Confirm the criteria with the user in two or three lines if they are ambiguous; otherwise start milestone 1.

**`/goal` (continue)**
1. Read `GOAL.md`; if every criterion is met, verify each one again, report, and mark the goal done.
2. Otherwise do the next unchecked milestone completely (verify it), tick it, add a Log line, update Next step.
3. End with: progress `k/n` milestones, what changed this turn, the next step.

**`/goal status`** — summarize without working. **`/goal clear`** — archive GOAL.md to `goals/<date>-<slug>.md`.

To keep going unattended, offer `/automate` with the prompt `/goal` on a schedule; create it only if the user agrees.
