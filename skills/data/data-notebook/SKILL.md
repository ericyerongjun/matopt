---
name: data-notebook
description: >
  Export a reproducible Jupyter notebook of a data analysis. Use when the
  user wants an .ipynb of the EDA, a data-agent notebook, or cells they can
  re-run on the uploaded table. Trigger on jupyter notebook, ipynb,
  analysis notebook, export notebook, notebook of the analysis. Do not use
  for a PDF report (data-report), slides (data-slides), or ad-hoc
  charts in chat (data-eda).
license: Apache-2.0
allowed-tools: data_agent python bash write_file
metadata:
  author: matopt
  version: "1.0"
---

# Analysis notebook

Deliver a runnable `.ipynb` that loads the workspace table and reproduces the analysis.

## Path

1. Use native `python` and file tools to package existing cells or make a bounded notebook edit. For substantial notebook analysis workflows or an explicit data-agent request, delegate to `data_agent` with the inputs, expected notebook path, and completion evidence. Delegation shares the parent run's limits and deadline; inspect its completion status and the returned notebook before handoff.
2. Otherwise build one with `nbformat` in the `python` kernel (see [references/notebook.md](references/notebook.md)).
3. Save as `analysis.ipynb` in the workspace root (or `data_agent_output/analysis.ipynb`).
4. Cells should load `uploads/<file>`, clean, analyse, and save figures to `figures/`.

## Notebook shape

1. Title + one-paragraph question
2. Load and profile
3. Cleaning (missingness, types, filters) with comments
4. Analysis cells that print the numbers cited later
5. Charts (`savefig` to `figures/`, then display)
6. Conclusions and caveats

Do not dump the chat transcript into markdown cells. Code must run top to bottom without the conversation kernel's leftover variables.
