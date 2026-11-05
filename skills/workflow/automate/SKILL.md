---
name: automate
description: >
  Schedule recurring agent work on a calendar (cron with a time zone), e.g.
  a weekly reading list from official changelogs every Monday at 9 AM
  Asia/Shanghai, remembering what was already reported. Use for /automate.
disable-model-invocation: true
license: Apache-2.0
metadata:
  author: matopt
  version: "1.0"
---

# /automate: schedule recurring work

Backed by `create_automation` (cron + IANA time zone); runs normally go to a dedicated conversation.

1. Draft before creating. Show the user:
   - **When**: a cron expression and time zone, in words too ("Mondays at 09:00, Asia/Shanghai" = `0 9 * * 1`).
   - **What** each run does: sources to check (official ones first — product changelogs, Microsoft Learn via its MCP
     server if connected, standards pages), the selection criteria, and the output (e.g. "a short candidate reading
     list: title — URL — one line why"; for a reading board, a batch file via `prepare-reading-batch`).
   - **Memory**: each run reads and updates `automations/<id>/state.md` (created automatically) so unchanged items
     are not repeated; list what it should store (URLs reported, last changelog version seen).
   - **Where**: a new dedicated conversation (`target: "new"`) unless the user wants it here.
2. Ask for confirmation unless the user already said to create it. Then call `create_automation` with `name`,
   `prompt` (self-contained: a run does not see this conversation), `cron`, `timezone`, `target`; add `run_now: true`
   when the user wants a first run immediately.
3. Report the id, the next run time, where results will appear, and how to pause or delete it (Settings →
   Automations, or ask to delete it).
