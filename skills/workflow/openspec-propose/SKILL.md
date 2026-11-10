---
name: openspec-propose
description: >
  Propose an OpenSpec change: turn the user's description of behavior into a
  change folder (proposal.md, design.md, tasks.md and a spec delta with
  acceptance scenarios) without implementing anything. Use for
  /openspec-propose or "propose the change first; don't implement yet".
disable-model-invocation: true
license: Apache-2.0
metadata:
  author: matopt
  version: "1.0"
---

# Propose a change (OpenSpec)

Describe the behavior; the agent drafts the artifacts. **Do not write application code in this step.**

1. `load_skill openspec` if `skills/openspec/` is not in the workspace (it has the format and the helper).
2. Pick a kebab-case change name (use the one the user gave, e.g. `build-reading-board`, `add-batch-import`) and the
   capability it touches. Read `openspec/specs/<capability>/spec.md` first if it exists: a change to existing
   behavior is a `MODIFIED` delta, never a second copy under `ADDED`.
3. Scaffold: `python skills/openspec/scripts/openspec.py init` (once) then
   `python skills/openspec/scripts/openspec.py new <change> --capability <cap>`.
4. Fill every file and remove every `<!-- placeholder -->`:
   - `proposal.md`: Why, What Changes, Capabilities, Impact, **Open Questions**.
   - `specs/<cap>/spec.md`: one requirement per user-visible rule (SHALL/MUST) with concrete scenarios — real values,
     edge cases the user did not mention (whitespace, duplicates, invalid input, empty state, persistence after reload,
     storage that is corrupt or refuses a write).
   - `design.md`: storage keys, data shapes, module boundaries, rejected alternatives.
   - `tasks.md`: small numbered checkbox tasks (`- [ ] 1.1 …`) grouped by area, ending with verification tasks.
5. `python skills/openspec/scripts/openspec.py validate <change>` until it passes.
6. Reply with the change path, a one-paragraph summary, the scenario list, and **every behavior still to decide**
   as explicit questions (e.g. "Same URL with a different title: keep the saved title and status?"). Stop there;
   the user reviews before `/openspec-apply`.

Use the existing validation rules and conventions of the project; keep the change local to what was asked.
