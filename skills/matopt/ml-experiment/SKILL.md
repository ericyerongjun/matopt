---
name: ml-experiment
description: >
  Supervised and unsupervised learning experiments with scikit-learn. Use for
  train/test splits, pipelines, leakage control, cross-validation, calibration,
  feature importance, clustering, dimensionality reduction, and reporting
  metrics. Trigger on sklearn, classifier, regressor, cross-validation, ROC,
  AUC, F1, pipeline, StandardScaler, leakage, random forest, logistic
  regression, k-means, PCA. Do not use for GLM inference p-values (use
  statistical-inference) or for writing an Excel workbook as the product (xlsx).
license: Apache-2.0
metadata:
  author: matopt
  version: "1.0"
---

# ML experiment

## Procedure

1. Define the target, the split unit, and what would count as leakage (future features, target-derived columns, group bleed).
2. `train_test_split` (or group/time split) **before** any fit. Put scaling and encoding inside a `Pipeline`.
3. Baseline first (DummyClassifier / mean predictor), then one simple model, then a stronger one only if needed.
4. Cross-validate on train; report mean ± std. Fit once on train, evaluate on test.
5. Classification: confusion matrix, ROC/PR when imbalanced — not accuracy alone. Regression: RMSE, MAE, residual plot.
6. Save plots to `figures/`. Summarise metrics in a table.

## Rules

- No `.fit` on full data before the test split.
- Do not tune on the test set.
- If n is tiny, prefer a linear model and say the experiment is underpowered.
- sklearn is for prediction; use `statsmodels` when the user wants coefficients and SEs.
