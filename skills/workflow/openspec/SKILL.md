---
name: openspec
description: >
  Spec-driven development (SDD) with OpenSpec: requirements live with the
  project as behavior plus acceptance scenarios in openspec/specs/, and every
  change goes through propose, review, apply, archive. Use when the user
  mentions OpenSpec, spec-driven development, a spec delta, acceptance
  scenarios, or asks to keep requirements in sync with code. Bundles the
  openspec.py helper (init, new, list, status, validate, archive) used by the
  openspec-propose, openspec-review, openspec-apply and openspec-archive commands.
license: Apache-2.0
metadata:
  author: matopt
  version: "1.0"
---

# OpenSpec: spec-driven development

A **spec** is behavior plus acceptance scenarios. Requirements live in the project, next to the code, and
survive every change. The cycle is **Propose → Review → Apply → Archive**:

| Step | Command | What happens |
|---|---|---|
| Propose | `/openspec-propose` | Describe the behavior; the agent drafts `proposal.md`, `design.md`, `tasks.md` and a spec delta. No code. |
| Review | `/openspec-review` | Read the scenarios with the user; settle open decisions (e.g. "what counts as a duplicate?"). |
| Apply | `/openspec-apply` | Implement `tasks.md` in order, tick each task, check every scenario, verify in the browser/tests. |
| Archive | `/openspec-archive` | Move the change to `changes/archive/YYYY-MM-DD-<name>/`, merge its deltas into `openspec/specs/`, validate. |

## Layout

```
openspec/
  specs/<capability>/spec.md          # the baseline: current, agreed behavior
  changes/<change-name>/
    .openspec.yaml                    # schema: spec-driven, created date
    proposal.md                       # why + what changes + impact
    design.md                         # decisions, trade-offs, open questions
    tasks.md                          # numbered checkbox tasks (1.1, 1.2, 2.1 …)
    specs/<capability>/spec.md        # the DELTA for this change
  changes/archive/YYYY-MM-DD-<name>/  # finished changes
```

## Spec format

Baseline `openspec/specs/<capability>/spec.md`:

```markdown
# reading-board Specification

## Purpose
Save useful links and track reading status locally.

## Requirements
### Requirement: Add a resource
The system SHALL add a resource with a trimmed title and an http(s) URL, newest first.

#### Scenario: Duplicate URL with surrounding whitespace
- **WHEN** the user submits " https://openspec.dev/docs " and that URL is already saved
- **THEN** no resource is added
- **AND** the saved resource and its status stay unchanged
```

A change's delta spec uses section headers instead of `## Requirements`:
`## ADDED Requirements`, `## MODIFIED Requirements` (repeat the whole requirement under its existing name),
`## REMOVED Requirements` (the `### Requirement:` header plus a **Reason**), and `## RENAMED Requirements`
(`- FROM: ### Requirement: Old` / `- TO: ### Requirement: New`).

Rules: every requirement uses SHALL or MUST and has at least one `#### Scenario:` (exactly four `#`), each with
WHEN/THEN bullets. Scenarios are concrete: real values, observable outcomes, edge cases (whitespace, duplicates,
corrupt storage, a failed write).

## Helper

Prefer the official CLI when it is installed (`npx @fission-ai/openspec@latest <cmd>` or `openspec <cmd>`). Otherwise
use the bundled helper, run from the project root:

```bash
python skills/openspec/scripts/openspec.py init
python skills/openspec/scripts/openspec.py new build-reading-board --capability reading-board
python skills/openspec/scripts/openspec.py list
python skills/openspec/scripts/openspec.py status build-reading-board
python skills/openspec/scripts/openspec.py validate build-reading-board   # or: validate --specs
python skills/openspec/scripts/openspec.py archive build-reading-board    # --force only if the user agrees
```

`--root DIR` points at a project other than the current directory. `archive` refuses while tasks are unchecked or
validation fails, merges ADDED/MODIFIED/REMOVED/RENAMED into the baseline and never touches application code.
