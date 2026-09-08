---
name: lambda-data-agent
description: >
  Run LAMBDA, MatOpt's data-analysis agent (the original AMA-CMFAI/LAMBDA backend).
  Use for dataset inspection, exploratory data analysis, charts, modeling,
  autonomous exploration of CSV/Excel/Parquet, LaTeX/PDF analysis reports,
  Jupyter notebooks, and PowerPoint decks from tabular data. Trigger on
  dataset, dataframe, EDA, exploratory analysis, analyze this csv, data report,
  autonomous exploration, lambda, pandas profiling, business insights from a
  table. Do not use for symbolic algebra, creating an .xlsx as the primary
  deliverable (use xlsx), a single hypothesis test (use statistical-inference),
  or authoring slides that are not a data-analysis deck (use pptx).
license: MIT
allowed-tools: lambda_data_agent python bash
metadata:
  author: matopt
  version: "1.0"
---

# LAMBDA data agent

Call the `lambda_data_agent` tool. Do not reimplement LAMBDA's loop with `python`.

LAMBDA is the original data agent from [AMA-CMFAI/LAMBDA](https://github.com/AMA-CMFAI/LAMBDA): inspect files, run analysis code, produce charts, and write reports. MatOpt hosts that backend at `/api/v1` and this tool runs its chat stream.

## When to call it

- The user uploaded a table (CSV, TSV, Excel, Parquet) and wants analysis, insights, or a report.
- They ask for autonomous exploration, an EDA PDF, a LaTeX report, a notebook export, or analysis slides.
- The question is about *this dataset* rather than a one-off statistical test you can answer in a short `python` cell.

## How

1. Make sure the table is in `uploads/`.
2. Call `lambda_data_agent` with the user's request. Set `autonomous=true` when they uploaded data without a specific question, or asked for a full report.
3. Read `lambda_output/` afterward if you need to cite a generated PDF, figure, or notebook.
4. Summarise LAMBDA's findings for the user and link artifacts (`lambda_output/...`).

Do not paste LAMBDA's internal tool traces. The user wants the analysis and the files.
