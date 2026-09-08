---
name: sympy-compute
description: >
  Symbolic mathematics with SymPy in the persistent python kernel. Use whenever
  the task is algebraic or exact — simplify, expand, factor, limits, series,
  derivatives, integrals, ODEs/PDEs, substitutions, solving equations or
  inequalities, matrices, generating functions, closed forms. Trigger on words
  like sympy, symbolic, exact, simplify, factor, expand, apart, together,
  derivative, integral, antiderivative, limit, Taylor, Laurent, ODE, PDE,
  closed form, algebraic identity. Prefer this over mental algebra or numeric
  guessing. Do not use for pure statistics, ML training, or spreadsheet editing.
license: MIT
metadata:
  author: matopt
  version: "1.0"
---

# Symbolic computation

Work in the `python` tool. `sympy` is already imported as `sp` in the kernel after the first cell that imports it; import explicitly in a new kernel.

## Procedure

1. Define symbols with explicit assumptions (`real=True`, `positive=True`, `integer=True`) when they matter.
2. Build the expression; do not simplify until the expression is complete.
3. Compute with the exact method (`diff`, `integrate`, `limit`, `dsolve`, `solve` / `solveset`, `series`, `simplify` / `powsimp` / `trigsimp`).
4. Cross-check: a second symbolic path, or `sp.N(...)` / numeric quadrature on a few points.
5. Confirm a claimed identity with `check_equivalence` or `sp.simplify(A - B) == 0`.
6. Final answer in LaTeX (`sp.latex`) and `\boxed{}` when there is a definite result.

## Rules

- Never skip the kernel for a multi-step integral, ODE, or matrix inverse.
- If `integrate` returns an unevaluated `Integral`, try `manual=True`, a substitution, or a numeric check; say so if no closed form exists.
- For `solve`, state domain restrictions; prefer `solveset` when the solution set is infinite.
- Pretty-print with `sp.pretty` only for debugging; the user-facing answer is LaTeX.

## Scripts

After this skill is loaded, run:

```bash
python skills/sympy-compute/scripts/identity_check.py --a "sin(x)**2 + cos(x)**2" --b "1"
```
