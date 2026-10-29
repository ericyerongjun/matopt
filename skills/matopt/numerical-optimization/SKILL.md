---
name: numerical-optimization
description: >
  Numerical optimisation with scipy.optimize (and linear algebra when the
  problem is a least-squares or QP). Use for unconstrained and constrained
  minimisation, nonlinear least squares, root finding, linear/quadratic
  programming, curve fitting, Lagrange multipliers numerically, and checking
  KKT/stationarity. Trigger on minimize, maximise, optimize, gradient descent,
  Newton, BFGS, L-BFGS, SLSQP, least squares, curve_fit, linear programming,
  constraints, feasible set. Do not use for symbolic closed forms (use
  sympy-compute) or for training sklearn/neural models (use ml-experiment).
license: Apache-2.0
metadata:
  author: matopt
  version: "1.0"
---

# Numerical optimisation

## Procedure

1. Write the objective as a Python function of a 1-D `ndarray`. Scale variables if magnitudes differ by decades.
2. Supply analytic Jacobian/Hessian when cheap; otherwise let the solver difference.
3. Choose a solver:
   - smooth unconstrained → `BFGS` / `L-BFGS-B` / `trust-constr`
   - bounds → `L-BFGS-B` or `trust-constr`
   - equality/inequality constraints → `SLSQP` or `trust-constr`
   - nonlinear least squares → `least_squares` / `curve_fit`
   - linear program → `linprog` (HiGHS)
4. Run from **more than one** start if the problem may be multimodal. Report the best feasible value and whether other starts disagreed.
5. Verify: gradient norm (or KKT residual), constraint violation, and a nearby finite-difference check.

## Rules

- Print `success`, `message`, `nit`, `fun`, and `x`.
- If `success` is false, change solver, scaling, or bounds before claiming a minimum.
- Maximise `f` by minimising `-f`; say so.
- Integer / combinatorial problems: say when a continuous relaxation is used.

## Required report

Lead with this report before extra commentary:

1. **Solver.** The algorithm, and why it matches the bounds or constraints.
2. **Starting points.** Every start that was tried.
3. **Outcome.** `success`, `message`, iteration count, objective value, and the point `x`.
4. **Minimum check.** Gradient norm or KKT residual, constraint violation, and whether the other starts agreed. If this check fails, do not call the point a minimum.
