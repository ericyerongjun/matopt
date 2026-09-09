---
name: lambda-report
description: >
  Write a polished LaTeX/PDF data-analysis report. Use when the user wants a
  formal analysis PDF, publication-style report, long-form EDA write-up, or
  a Chinese/English LAMBDA report from tabular results. Trigger on latex
  report, analysis pdf, data report, EDA report, formal report, pdflatex,
  xelatex. Do not use for generic PDF forms or watermarks (use pdf), a
  chat-length answer, analysis slides (lambda-slides), or a notebook
  (lambda-notebook).
license: MIT
allowed-tools: lambda_data_agent python bash write_file read_file
metadata:
  author: matopt
  version: "1.0"
---

# LAMBDA analysis report

Produce a stand-alone PDF. Narrative first, then figures next to the paragraphs that interpret them.

## Path

1. Finish the analysis (or call `lambda_data_agent`) so numbers and `figures/` exist.
2. Copy the matching template from this skill:
   - English: [references/english-template.tex](references/english-template.tex) — compile with `pdflatex`
   - Chinese or mixed: [references/chinese-template.tex](references/chinese-template.tex) — compile with `xelatex`
3. Write `report.tex` in the workspace. Use finding-style section titles (“Location explains most of the price spread”), not “Executive Summary”.
4. Logo (optional header): `skills/lambda-report/assets/logo.png` after this skill is loaded.
5. Compile twice if you have refs or a TOC:

```bash
pdflatex -interaction=nonstopmode -halt-on-error report.tex
# or: xelatex -interaction=nonstopmode -halt-on-error report.tex
```

6. Link `report.pdf` (and keep `report.tex`).

Or ask `lambda_data_agent` to write the report, then copy artifacts from `lambda_output/`.

## Rules

- Paragraphs over bullet lists. Cite concrete values.
- Tables: `tabularx` + `\textwidth`. Never a raw wide `tabular` that overflows.
- Figures: `\includegraphics[width=\textwidth]{figures/<name>.png}` (workspace-relative). Interpret each figure beside it.
- Default author `LAMBDA` unless the user names one. Match the user's language.
- Do not paste compiler logs into the chat. Fix overfull boxes and recompile.

See [references/layout.md](references/layout.md) for table and figure defaults.
