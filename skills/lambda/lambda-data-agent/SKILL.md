---
name: lambda-data-agent
description: >
  Run the full LAMBDA data-analysis agent (AMA-CMFAI/LAMBDA) end to end.
  Use when the user wants autonomous exploration, the original LAMBDA loop,
  or a combined EDA + report + slides + notebook pipeline on uploaded tables.
  Trigger on lambda, lambda agent, autonomous exploration, run the data agent,
  full analysis pipeline. Do not use for a single chart or a quick table peek
  (use lambda-eda), a LaTeX/PDF report only (lambda-report), analysis slides
  only (lambda-slides), a notebook export only (lambda-notebook), symbolic
  algebra (sympy-compute), or a single hypothesis test (statistical-inference).
license: MIT
allowed-tools: lambda_data_agent python bash
metadata:
  author: matopt
  version: "1.1"
---

# LAMBDA data agent

Call the `lambda_data_agent` tool. Do not reimplement LAMBDA's loop with `python`.

LAMBDA inspects files, runs analysis code, produces charts, and can write reports. MatOpt hosts that backend at `/api/v1`.

## When

- The user asked to “run LAMBDA” or wants autonomous exploration of an uploaded table.
- They want the whole pipeline (EDA + artifacts), not one deliverable.

For a focused job, load the matching sibling instead: `lambda-eda`, `lambda-report`, `lambda-slides`, `lambda-notebook`.

## How

1. Confirm the table is in `uploads/`.
2. Call `lambda_data_agent` with the user's request. Set `autonomous=true` when they uploaded data without a specific question, or asked for a full exploration.
3. Read `lambda_output/` afterward and link artifacts (`lambda_output/...`).
4. Summarise findings. Do not paste LAMBDA's internal tool traces.
