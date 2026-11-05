---
name: openspec-review
description: >
  Review an OpenSpec change before implementation: read the proposal and the
  spec delta with the user, find ambiguous or missing scenarios, and record
  the decisions in the spec. Use for /openspec-review or "review the
  behavior" on a proposed change.
disable-model-invocation: true
license: Apache-2.0
metadata:
  author: matopt
  version: "1.0"
---

# Review the behavior (OpenSpec)

The review is about **behavior**, not code. Nothing is implemented in this step.

1. `python skills/openspec/scripts/openspec.py list`, then `status <change>`; read `proposal.md`, `design.md` and
   every `specs/<cap>/spec.md` of the change, plus the baseline spec it modifies.
2. For each requirement ask: what exactly counts as the same item (a duplicate)? what is kept and what is replaced?
   what happens on invalid input, on an empty list, after reload, when storage fails? Is every scenario observable
   (a value on screen, a row in storage), not an implementation detail?
3. Present findings as a short numbered list: ambiguity → proposed decision → the scenario that would pin it down.
   Example: *Same URL, different title* → keep the existing title and status → "Existing URL under a different title:
   a Done resource titled X at U stays Done with that title when the user submits Y and U."
4. Ask the user to decide the open points. Only after they answer, edit the delta spec (and tasks.md if work
   changes), remove resolved Open Questions, and re-run `validate <change>`.
5. Do not start implementing; hand over to `/openspec-apply`.
