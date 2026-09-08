# Test chooser

| Question | Typical test | Python |
|---|---|---|
| Mean vs value, roughly normal | 1-sample t | `scipy.stats.ttest_1samp` |
| Two independent means | 2-sample t / Welch | `ttest_ind(..., equal_var=False)` |
| Paired means | paired t | `ttest_rel` |
| Two distributions, skewed | Mann–Whitney | `mannwhitneyu` |
| Paired, skewed | Wilcoxon signed-rank | `wilcoxon` |
| Counts vs expected | χ² GOF | `chisquare` |
| Independence in a table | χ² / Fisher | `chi2_contingency` / `fisher_exact` |
| Correlation, linear | Pearson | `pearsonr` |
| Correlation, monotone | Spearman | `spearmanr` |
| Binary outcome ~ covariates | Logistic GLM | `statsmodels.formula.api.logit` |
| Continuous ~ covariates | OLS | `statsmodels.formula.api.ols` |
| Uncertain SE / weird sampling | Bootstrap | resample statistic, percentile CI |

Prefer `statsmodels` summary tables for GLMs. Use `robust='HC3'` when heteroskedasticity is plausible.
