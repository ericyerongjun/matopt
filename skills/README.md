# MatOpt skill space

Skills follow the [Agent Skills](https://agentskills.io/specification) format: a folder with `SKILL.md` (YAML frontmatter + instructions) and optional `scripts/`, `references/`, and `assets/`.

```
skills/
  matopt/       # native math / stats / ML / CS playbooks
  lambda/       # LAMBDA data-analysis playbooks (eda, report, slides, notebook, agent)
  anthropic/    # full catalog from github.com/anthropics/skills
  spec/         # format notes and the upstream template
```

## How the harness uses them

1. **Discovery** — only `name` + `description` sit in the system prompt (and in `search_skills`). The composer `/` menu lists skills and tools with a one-line intro.
2. **Routing** — a lexical router scores the user message, attachments, and workspace files; high-confidence matches are auto-activated (at most two per turn). Typing `/skill-name` or `/tool-name` (or sending `skills` / `tools` on the chat or steer request) force-activates that playbook. The same `/` tokens work mid-turn.
3. **Activation** — `load_skill` (or auto-activate) injects the `SKILL.md` body and copies the folder into the conversation workspace at `skills/<name>/`.
4. **Execution** — bundled scripts run via `python` / `bash` from that workspace copy.

Add a math/stats playbook under `skills/matopt/`, a LAMBDA deliverable under `skills/lambda/`, or a user skill in `~/.matopt/skills/`. The next backend start picks it up. See `skill-creator` for the authoring workflow.
