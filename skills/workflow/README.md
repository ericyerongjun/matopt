# Workflow skills

Spec-driven development, review, automation and authoring commands, adapted from the
*Generative AI for Code Generation* lab (COMP4431 Lab 05) for MatOpt. Every folder is an
[Agent Skill](https://agentskills.io/specification); type `/name` in the composer.

Command skills set `disable-model-invocation: true`: they appear in the `/` menu and in
`search_skills`, but the lexical router never activates them on its own.

| Command | What it does |
|---|---|
| `/openspec-propose` · `/openspec-review` · `/openspec-apply` · `/openspec-archive` | Spec-driven development with [OpenSpec](https://openspec.dev): propose a change, settle the behavior, implement it task by task, archive it into the baseline specs. `openspec` holds the format and a dependency-free helper (`scripts/openspec.py`). |
| `/plan-mode` | Read-only planning of a bounded task; build only after review. |
| `/prepare-reading-batch` | JSON batch of real titles and URLs plus a source report (`scripts/reading_batch.py` validates it). Auto-activates. |
| `/canvas` | Interactive panel that explains code or data (native UI, mini-app, or a standalone HTML file). |
| `/review` · `/review-bugbot` · `/review-security` | Choose a reviewer; find bugs and regressions; security audit. |
| `/loop` | Repeat a prompt on an interval, or watch a file quietly until a long job finishes or fails. |
| `/automate` | Calendar schedules (cron + time zone) that remember what they already reported. |
| `/goal` | Long-term goal tracked in `GOAL.md` across turns. |
| `/side` · `/btw` | A side question that does not derail the current task. |
| `/shell` | Run one shell command verbatim. |
| `/autopilot` | Handle PR review comments and failing CI with `gh`. |
| `/split-to-prs` | Split a large change into a stack of reviewable PRs. |
| `/create-hook` · `/create-rule` · `/create-skill` · `/create-subagent` | Author hooks, AGENTS.md rules, skills and sub-agent roles. |
| `/prompt-blame` | Trace a line of code back to the commit, PR and prompt that produced it. |
| `/migrate-to-skills` | Convert Claude Code / Cursor / Codex commands and rules into skills (`scripts/convert.py`). |
| `/matopt-sdk` | Drive MatOpt from code (HTTP + SSE) or use it as an MCP server from other apps. |

`/loop` and `/automate` use the `create_automation`, `list_automations` and `delete_automation`
tools; Settings → Automations lists, pauses, runs and deletes them. Remote documentation such as
Microsoft Learn comes from Settings → Integrations → Connect apps.
