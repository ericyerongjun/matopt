---
name: data-agent
description: >
  Run MatOpt's full data-analysis agent end to end.
  Use when the user wants autonomous exploration, the full data-agent loop,
  or a combined EDA + report + slides + notebook pipeline on uploaded tables.
  Trigger on autonomous exploration, run the data agent,
  full analysis pipeline. Do not use for a single chart or a quick table peek
  (use data-eda), a LaTeX/PDF report only (data-report), analysis slides
  only (data-slides), a notebook export only (data-notebook), symbolic
  algebra (sympy-compute), or a single hypothesis test (statistical-inference).
license: Apache-2.0
allowed-tools: data_agent python bash
metadata:
  author: matopt
  version: "1.1"
---

# Data agent

Use `data_agent` for an explicit data-agent request or a substantial specialized analysis pipeline. Use native `python` and file tools by default for short bounded calculations, single statistics, table inspections, and file edits. A table attachment alone does not require delegation.

The data agent inspects files, runs analysis code, produces charts, and can write reports. MatOpt hosts that backend at `/api/v1`.

## When

- The user asked to “run the data agent” or wants autonomous exploration of an uploaded table.
- They want the whole pipeline (EDA + artifacts), not one deliverable.

For a focused job, load the matching sibling instead: `data-eda`, `data-report`, `data-slides`, `data-notebook`.

## How

1. Confirm the table is in `uploads/`.
2. Specify the inputs, question, expected deliverable and output paths, and completion evidence in the delegated request. Set `autonomous=true` only when the user requests full autonomous exploration.
3. Delegation shares the parent run's model/tool limits and deadline; it does not reset them. Check the returned incomplete/error status and inspect the actual workspace artifacts before treating the deliverable as complete.
4. Summarise findings and link the returned artifacts at their actual paths. Do not paste the data agent's internal tool traces.
