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

Use `lambda_data_agent` for an explicit LAMBDA request or a substantial specialized analysis pipeline. Use native `python` and file tools by default for short bounded calculations, single statistics, table inspections, and file edits. A table attachment alone does not require delegation.

LAMBDA inspects files, runs analysis code, produces charts, and can write reports. MatOpt hosts that backend at `/api/v1`.

## When

- The user asked to “run LAMBDA” or wants autonomous exploration of an uploaded table.
- They want the whole pipeline (EDA + artifacts), not one deliverable.

For a focused job, load the matching sibling instead: `lambda-eda`, `lambda-report`, `lambda-slides`, `lambda-notebook`.

## How

1. Confirm the table is in `uploads/`.
2. Specify the inputs, question, expected deliverable and output paths, and completion evidence in the delegated request. Set `autonomous=true` only when the user requests full autonomous exploration.
3. Delegation shares the parent run's model/tool limits and deadline; it does not reset them. Check the returned incomplete/error status and inspect the actual workspace artifacts before treating the deliverable as complete.
4. Summarise findings and link the returned artifacts at their actual paths. Do not paste LAMBDA's internal tool traces.
