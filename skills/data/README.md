# Data skill space

Data-analysis playbooks, split the same way Anthropic skills are: one folder per job, `SKILL.md` in the catalog, optional `scripts/` / `references/` / `assets/`.

| Skill | Slash | Use for |
|---|---|---|
| `data-agent` | `/data-agent` | Full data-agent loop / autonomous exploration |
| `data-eda` | `/data-eda` | Inspect a table, charts, insights |
| `data-report` | `/data-report` | LaTeX / PDF analysis report |
| `data-slides` | `/data-slides` | Data-insight PowerPoint deck |
| `data-notebook` | `/data-notebook` | Jupyter notebook of the analysis |

Type `/` in the composer — they appear under **Data**, next to Anthropic and MatOpt.

The data-agent backend (`backend/data_agent_app/`) also ships its internal `.skills/latex_skill.md` and `.skills/ppt_skill.md` for the hosted agent. These folders are the MatOpt catalog the harness and `/` menu load.
