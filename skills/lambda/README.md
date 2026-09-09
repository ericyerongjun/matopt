# LAMBDA skill space

Data-analysis playbooks from [AMA-CMFAI/LAMBDA](https://github.com/AMA-CMFAI/LAMBDA), split the same way Anthropic skills are: one folder per job, `SKILL.md` in the catalog, optional `scripts/` / `references/` / `assets/`.

| Skill | Slash | Use for |
|---|---|---|
| `lambda-data-agent` | `/lambda-data-agent` | Full LAMBDA loop / autonomous exploration |
| `lambda-eda` | `/lambda-eda` | Inspect a table, charts, insights |
| `lambda-report` | `/lambda-report` | LaTeX / PDF analysis report |
| `lambda-slides` | `/lambda-slides` | Data-insight PowerPoint deck |
| `lambda-notebook` | `/lambda-notebook` | Jupyter notebook of the analysis |

Type `/` in the composer — they appear under **LAMBDA**, next to Anthropic and MatOpt.

The original LAMBDA backend still ships its internal `.skills/latex_skill.md` and `.skills/ppt_skill.md` for the hosted agent. These folders are the MatOpt catalog the harness and `/` menu load.
