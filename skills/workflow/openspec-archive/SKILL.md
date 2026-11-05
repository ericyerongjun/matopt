---
name: openspec-archive
description: >
  Archive a finished OpenSpec change: confirm all tasks are done, merge its
  spec deltas into openspec/specs (the baseline), move the change to
  changes/archive/YYYY-MM-DD-<name>/, validate, and prepare the commit. Use
  for /openspec-archive or "archive the change and keep the baseline".
disable-model-invocation: true
license: Apache-2.0
metadata:
  author: matopt
  version: "1.0"
---

# Archive and keep the baseline (OpenSpec)

Archive the change · sync the specs · commit the code.

1. `python skills/openspec/scripts/openspec.py status <change>`: every task must be `[x]`. If not, stop and say which
   are open (use `--force` only when the user explicitly accepts unfinished tasks).
2. `python skills/openspec/scripts/openspec.py archive <change>` (or `openspec archive <change>` with the official
   CLI). It validates, merges ADDED/MODIFIED/REMOVED/RENAMED into `openspec/specs/<cap>/spec.md` and moves the folder.
3. `python skills/openspec/scripts/openspec.py validate --specs` must pass. Fix the `## Purpose` line if it still
   says TBD.
4. Report: `Archive Complete` · change · archived path · specs synced (requirement count) · validation result ·
   "application code was not changed".
5. If the project is a git repository, offer to commit the code and the `openspec/` changes together; commit only
   when the user says so.
