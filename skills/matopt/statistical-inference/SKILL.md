---
name: statistical-inference
description: >
  Frequentist and basic Bayesian inference with scipy, statsmodels, and pandas.
  Use for hypothesis tests, confidence intervals, GLMs, ANOVA, bootstrap,
  permutation tests, A/B tests, power, sample size, p-values, effect sizes,
  residual diagnostics, and interpreting tabular experimental data. Trigger on
  t-test, chi-square, Wilcoxon, Mann-Whitney, regression, logistic, GLM, OLS,
  ANOVA, bootstrap, confidence interval, p-value, effect size, power analysis,
  A/B test. Do not use for symbolic algebra, neural nets, or creating .xlsx
  files as the primary deliverable (use xlsx for spreadsheet authoring).
license: Apache-2.0
metadata:
  author: matopt
  version: "1.0"
---

# Statistical inference

Use the persistent `python` kernel. Load tables with pandas (`uploads/…`).

## Procedure

1. Inspect the data: shape, dtypes, missingness, obvious leakage or pairing.
2. State the estimand, the hypothesis, and the unit of independence *before* testing.
3. Check assumptions (normality / equal variance / link function) with plots and tests; pick a robust or exact alternative when they fail.
4. Fit with `statsmodels` (preferred for inference) rather than sklearn.
5. Report estimate, SE or bootstrap interval, statistic, p-value, effect size, and sample size. Do not star-gaze a p-value without an effect size.
6. Residual / calibration check. If the model is wrong, say so and fit a better one.

## Rules

- Multiple tests: control FDR or Bonferroni and say which.
- Observational data: do not claim causality.
- Paired designs: use paired tests; do not ignore clustering.
- Save diagnostic plots under `figures/` and reference them in Markdown.

## Required report

Lead with this report before extra commentary:

1. **Question.** The estimand, in words, and the unit of independence.
2. **Hypothesis.** The null and alternative, or the model being estimated.
3. **Assumptions.** Which checks were run, and whether they hold.
4. **Procedure.** The test or model, and why it was chosen.
5. **Estimate.** The point estimate with its standard error.
6. **Interval.** The confidence or credible interval and its level.
7. **Effect size.** A magnitude the reader can interpret, together with the p-value.
8. **Sample size.** n for the analysis, not only the raw table.

If the request is to explore, profile, or plot a whole table and no estimand is specified, do not invent a test. Hand that task to the `data-eda`, `data-report`, or `data-agent` playbook.

See [references/tests.md](references/tests.md) for a compact test chooser.
