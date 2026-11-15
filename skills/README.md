# MatOpt skill space

Skills follow the [Agent Skills](https://agentskills.io/specification) format: a folder with `SKILL.md` (YAML frontmatter + instructions) and optional `scripts/`, `references/`, and `assets/`.

```
skills/
  matopt/       # native math / stats / ML / CS playbooks
  data/         # data-analysis playbooks (eda, report, slides, notebook, agent)
  workflow/     # spec-driven development, review, /loop, /automate, authoring commands
  anthropic/    # full catalog from github.com/anthropics/skills
  spec/         # format notes and the upstream template
```

## Docker setup

The standard Compose setup mounts this repository's `skills/` directory
read-only at `/app/skills` and configures all four skill groups. Edit or add
skills on the host, then run `docker compose restart backend` so the catalog is
loaded again. Restarting the backend also restarts its Python kernels.

The host's `~/.matopt/skills/` is not mounted by default. To distribute custom workflows with MatOpt,
keep their skills in the repository so they are included with the source.
Scripts run inside the backend container and need their dependencies there.

## How the harness uses them

1. **Discovery** — only `name` + `description` sit in the system prompt (and in `search_skills`). The composer `/` menu lists skills and tools with a one-line intro.
2. **Routing** — a lexical router scores the user message, attachments, and workspace files; high-confidence matches are auto-activated (at most two per turn). Typing `/skill-name` or `/tool-name` (or sending `skills` / `tools` on the chat or steer request) force-activates that playbook. The same `/` tokens work mid-turn.
3. **Activation** — `load_skill` (or auto-activate) injects the `SKILL.md` body and copies the folder into the conversation workspace at `skills/<name>/`.
4. **Execution** — bundled scripts run via `python` / `bash` from that workspace copy.

A skill with `disable-model-invocation: true` in its frontmatter is a command: it is listed and loadable but never auto-activated.

Add a math/stats playbook under `skills/matopt/` or a data-analysis deliverable
under `skills/data/`. Native development also discovers user skills in
`~/.matopt/skills/`. The next backend start picks them up. See `skill-creator`
for the authoring workflow.
