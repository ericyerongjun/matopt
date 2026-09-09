---
name: lambda-eda
description: >
  Exploratory data analysis on tabular files. Use for dataset inspection,
  profiling, missingness, distributions, group comparisons, correlation,
  and charts from CSV/TSV/Excel/Parquet. Trigger on EDA, exploratory
  analysis, analyze this csv, profile the table, dataframe, dataset,
  pandas profiling, what's in this file, plot the columns, explore the
  table. Do not use for a single pre-specified hypothesis test
  (statistical-inference), training a model as the product (ml-experiment),
  authoring an .xlsx workbook (xlsx), or writing a formal PDF/slide/notebook
  deliverable (lambda-report, lambda-slides, lambda-notebook).
license: MIT
allowed-tools: lambda_data_agent python bash read_file list_files
metadata:
  author: matopt
  version: "1.0"
---

# LAMBDA exploratory analysis

Inspect the table, compute the facts, and plot what matters. Prefer `lambda_data_agent` when the user wants LAMBDA's own exploration loop; use the `python` kernel for a tight, directed EDA.

## Procedure

1. List `uploads/` and load the table (`pandas.read_csv` / `read_excel` / `read_parquet`).
2. Report shape, dtypes, missingness, duplicates, and obvious data-quality issues before any insight.
3. Separate identifiers, categoricals, numerics, and dates. Do not treat IDs as features.
4. Answer the user's question with group stats, comparisons, or a simple baseline — not a chart dump.
5. Save figures under `figures/` with titles and labelled axes. Reference them as `![caption](figures/<name>.png)`.
6. State limitations (sample size, missing fields, selection bias). Do not claim causality from observational tables.

## Charts

One claim per figure. Prefer:

- histograms / ECDFs for a single numeric
- bar or box plots for a group comparison
- a scatter or line only when both axes are meaningful

If the table is large, aggregate first. Print the numbers you cite.

## LAMBDA

For autonomous multi-step exploration, call `lambda_data_agent` with the user's EDA request (`autonomous=true` only when they did not specify a question). Then summarise `lambda_output/` rather than pasting traces.
