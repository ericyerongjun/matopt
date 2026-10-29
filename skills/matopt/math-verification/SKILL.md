---
name: math-verification
description: >
  Independent verification of a mathematical answer. Use when checking a
  closed form, comparing two expressions, validating a boxed result, or when
  verify-mode is on. Trigger on verify, check equivalence, prove identity,
  sanity check the answer, math-verify, boxed answer. Combine python, sympy,
  numeric probes, and the check_equivalence tool. Do not use for training
  models or for editing office documents.
license: Apache-2.0
metadata:
  author: matopt
  version: "1.0"
---

# Verify a mathematical result

Recompute; do not trust the derivation that produced the candidate.

## Procedure

1. Restate the claimed result (the boxed value or identity).
2. Re-derive on an independent path (different SymPy call, numeric quadrature, special cases, dimensional check).
3. `check_equivalence` for symbolic / LaTeX answers.
4. Numeric probe: evaluate both sides at several random admissible points.
5. Edge cases: 0, 1, domain endpoints, large-n.
6. Formal proof (when `lean_check` is available and the claim is a theorem, not a number): state it in Lean 4 and
   prove it. Only a `VERIFIED` result counts; never weaken the statement to make it compile, and report the exact
   theorem that was checked.
7. Report verdict: confirmed / contradicted / inconclusive, with the evidence.

## Rules

- If the two paths disagree, find the bug before answering.
- Floating-point: use a tolerance relative to magnitude (`rel=1e-8` typical); do not claim exact equality of floats.
- For contest-style answers, compare in the same format the problem asked for (set vs singleton, positive root vs all roots).
