---
name: loop
description: >
  Repeat a prompt or skill on an interval, or monitor a long task (an
  experiment, a build, a batch job) and report only when it finishes or
  fails. Use for /loop, e.g. "check experiment-status.json every 5 minutes
  and tell me only when it finishes or fails".
disable-model-invocation: true
license: Apache-2.0
metadata:
  author: matopt
  version: "1.0"
---

# /loop: repeat work or monitor a long task

Backed by the `create_automation` tool (`list_automations`, `delete_automation` to inspect and stop).

1. Parse the request: what to check or do, the interval (default 10 minutes if none is given; minimum 1), the stop
   condition, and whether the user wants silence while nothing changes.
2. **Quiet monitor** (preferred when a file reflects the state — `experiment-status.json`, a log, a results file):
   - read the file once now and show its current state;
   - create the automation with `watch_path` and, for "tell me only when it finishes or fails", a `watch_pattern`
     that matches the terminal states, e.g. `"status"\s*:\s*"(finished|completed|failed|error)"`. No model turn runs
     until the pattern matches; then one turn runs, reports, and the loop completes on its own.
   - without a terminal pattern, `watch_path` alone runs a turn only when the file changes.
   - the file must be in this conversation's workspace; if it lives elsewhere (a remote GPU, CI), use a schedule
     without a watch and make the prompt fetch the status.
3. **Repeating prompt** (no file to watch): `every_minutes` with a prompt that states exactly what to report and
   "reply in one line 'no change' when nothing changed". Add `max_runs` or `expires_in_hours` so it cannot run forever.
4. Write the run prompt so it stands alone (a later run does not see this message): what to check, where, what
   counts as done or failed, what to report, and "then call delete_automation with this automation's id" for the
   terminal case.
5. Confirm in one line: the automation id, what is checked and how often, when it stops, and that it can be paused
   or deleted in Settings → Automations (or `/loop stop`, which means: `list_automations` then `delete_automation`).
