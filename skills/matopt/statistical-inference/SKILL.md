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
license: MIT
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

See [references/tests.md](references/tests.md) for a compact test chooser.
