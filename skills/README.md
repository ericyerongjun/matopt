# MatOpt skill space

Skills follow the [Agent Skills](https://agentskills.io/specification) format: a folder with `SKILL.md` (YAML frontmatter + instructions) and optional `scripts/`, `references/`, and `assets/`.

```
skills/
  matopt/       # native math / stats / ML / CS playbooks
  anthropic/    # full catalog from github.com/anthropics/skills
  spec/         # format notes and the upstream template
```

## How the harness uses them

1. **Discovery** — only `name` + `description` sit in the system prompt (and in `search_skills`).
2. **Routing** — a lexical router scores the user message, attachments, and workspace files; high-confidence matches are auto-activated (at most two per turn).
3. **Activation** — `load_skill` (or auto-activate) injects the `SKILL.md` body and copies the folder into the conversation workspace at `skills/<name>/`.
4. **Execution** — bundled scripts run via `python` / `bash` from that workspace copy.

Add a skill by dropping a folder with `SKILL.md` into `skills/matopt/` (or `~/.matopt/skills/`). The next backend start picks it up. See `skill-creator` for the authoring workflow.
