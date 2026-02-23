"""
Math engine: orchestrates SymPy operations, LaTeX conversion, Wolfram Alpha,
and the Python sandbox into a unified service that the chat service can call.

This is the "tool layer" that the LLM invokes via function-calling.

Uses numpy/scipy for fast numerical work, SymPy for symbolic algebra,
mpmath for arbitrary-precision arithmetic, and matplotlib for plots.
"""

from __future__ import annotations

import base64
import io
import logging
from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional

import numpy as np
import scipy
import scipy.optimize
import scipy.integrate as sp_integrate
import scipy.interpolate
import scipy.linalg
import scipy.stats
import scipy.fft
import mpmath
import sympy as sp
from sympy.printing.latex import latex as sp_latex

from app.config import settings
from app.core.latex_converter import latex_converter, ConversionResult
from app.core.python_sandbox import python_sandbox
from app.utils.latex_utils import find_boxed_answer

logger = logging.getLogger(__name__)


# ── Result types ────────────────────────────────────────────────────────
class ToolName(str, Enum):
    PARSE_LATEX = "parse_latex"
    SIMPLIFY = "simplify"
    SOLVE = "solve"
    DIFFERENTIATE = "differentiate"
    INTEGRATE = "integrate"
    SERIES_EXPAND = "series_expand"
    EVALUATE = "evaluate"
    MATRIX_OPS = "matrix_ops"
    NUMERICAL_SOLVE = "numerical_solve"
    NUMERICAL_INTEGRATE = "numerical_integrate"
    STATISTICS = "statistics_compute"
    PLOT_FUNCTION = "plot_function"
    WOLFRAM = "wolfram_query"
    EXEC_PYTHON = "exec_python"
    COMPARE_ANSWERS = "compare_answers"


@dataclass
class ToolResult:
    name: str
    success: bool
    result: str               # human-readable / LaTeX
    raw: Any = None           # SymPy expr or dict
    error: Optional[str] = None


# ── Tool definitions (for LLM function-calling schema) ──────────────────
TOOL_DEFINITIONS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": ToolName.PARSE_LATEX,
            "description": "Parse a LaTeX math expression into a canonical SymPy form. Returns canonical LaTeX and free symbols.",
            "parameters": {
                "type": "object",
                "properties": {
                    "latex": {"type": "string", "description": "The LaTeX expression to parse"},
                },
                "required": ["latex"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": ToolName.SIMPLIFY,
            "description": "Simplify a LaTeX math expression using SymPy.",
            "parameters": {
                "type": "object",
                "properties": {
                    "latex": {"type": "string", "description": "LaTeX expression to simplify"},
                },
                "required": ["latex"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": ToolName.SOLVE,
            "description": "Solve an equation or system. Provide the equation in LaTeX (use = for equality).",
            "parameters": {
                "type": "object",
                "properties": {
                    "latex": {"type": "string", "description": "LaTeX equation to solve"},
                    "variable": {"type": "string", "description": "Variable to solve for (default: auto-detect)"},
                },
                "required": ["latex"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": ToolName.DIFFERENTIATE,
            "description": "Compute the derivative of a LaTeX expression.",
            "parameters": {
                "type": "object",
                "properties": {
                    "latex": {"type": "string", "description": "LaTeX expression to differentiate"},
                    "variable": {"type": "string", "description": "Differentiation variable (default: x)"},
                    "order": {"type": "integer", "description": "Order of derivative (default: 1)"},
                },
                "required": ["latex"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": ToolName.INTEGRATE,
            "description": "Compute the integral of a LaTeX expression (symbolic). For fast numerical quadrature use numerical_integrate.",
            "parameters": {
                "type": "object",
                "properties": {
                    "latex": {"type": "string", "description": "LaTeX expression to integrate"},
                    "variable": {"type": "string", "description": "Integration variable (default: x)"},
                    "lower": {"type": "string", "description": "Lower bound (omit for indefinite)"},
                    "upper": {"type": "string", "description": "Upper bound (omit for indefinite)"},
                },
                "required": ["latex"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": ToolName.SERIES_EXPAND,
            "description": "Compute the Taylor / Laurent series expansion of a LaTeX expression around a point.",
            "parameters": {
                "type": "object",
                "properties": {
                    "latex": {"type": "string", "description": "LaTeX expression to expand"},
                    "variable": {"type": "string", "description": "Expansion variable (default: x)"},
                    "point": {"type": "string", "description": "Expansion point (default: 0)"},
                    "order": {"type": "integer", "description": "Number of terms (default: 6)"},
                },
                "required": ["latex"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": ToolName.EVALUATE,
            "description": (
                "Numerically evaluate a LaTeX expression using numpy+mpmath for speed and precision. "
                "Optionally substitute variable values."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "latex": {"type": "string", "description": "LaTeX expression to evaluate"},
                    "substitutions": {
                        "type": "object",
                        "description": 'Mapping of variable names to numeric values, e.g. {"x": 3.14}',
                    },
                    "precision": {
                        "type": "integer",
                        "description": "Decimal digits of precision (default: 15; max: 100). Uses mpmath for >15.",
                    },
                },
                "required": ["latex"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": ToolName.MATRIX_OPS,
            "description": (
                "Perform matrix/linear-algebra operations using numpy. "
                "Supported ops: determinant, inverse, eigenvalues, eigenvectors, svd, rank, norm, solve_linear, transpose, trace, rref."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "matrix": {
                        "type": "array",
                        "description": "The matrix as a list of lists (row-major), e.g. [[1,2],[3,4]]",
                        "items": {"type": "array", "items": {"type": "number"}},
                    },
                    "operation": {
                        "type": "string",
                        "description": "One of: determinant, inverse, eigenvalues, eigenvectors, svd, rank, norm, solve_linear, transpose, trace, rref",
                    },
                    "rhs": {
                        "type": "array",
                        "description": "Right-hand side vector for solve_linear, e.g. [1, 2]",
                        "items": {"type": "number"},
                    },
                },
                "required": ["matrix", "operation"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": ToolName.NUMERICAL_SOLVE,
            "description": (
                "Find numerical root(s) of an equation using scipy.optimize. "
                "Accepts a LaTeX expression (assumed = 0) and initial guess(es)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "latex": {"type": "string", "description": "LaTeX expression (set = 0)"},
                    "variable": {"type": "string", "description": "Variable name (default: x)"},
                    "x0": {"type": "number", "description": "Initial guess (default: 1.0)"},
                    "method": {
                        "type": "string",
                        "description": "Solver method: brentq (bracket), newton, fsolve (default: fsolve)",
                    },
                    "bracket": {
                        "type": "array",
                        "description": "Bracket [a, b] for brentq method where f(a)*f(b)<0",
                        "items": {"type": "number"},
                    },
                },
                "required": ["latex"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": ToolName.NUMERICAL_INTEGRATE,
            "description": (
                "Fast numerical definite integration via scipy.integrate.quad. "
                "Much faster than symbolic integration for definite integrals."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "latex": {"type": "string", "description": "LaTeX expression (integrand)"},
                    "variable": {"type": "string", "description": "Integration variable (default: x)"},
                    "lower": {"type": "number", "description": "Lower bound"},
                    "upper": {"type": "number", "description": "Upper bound"},
                },
                "required": ["latex", "lower", "upper"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": ToolName.STATISTICS,
            "description": (
                "Compute descriptive statistics on a dataset using numpy/scipy.stats. "
                "Returns mean, median, std, variance, skewness, kurtosis, percentiles, and more."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "data": {
                        "type": "array",
                        "description": "1-D array of numeric values",
                        "items": {"type": "number"},
                    },
                    "operations": {
                        "type": "array",
                        "description": (
                            "Stats to compute (default: all). "
                            "Options: mean, median, std, var, min, max, sum, skew, kurtosis, "
                            "mode, percentile_25, percentile_75, iqr, zscore, describe"
                        ),
                        "items": {"type": "string"},
                    },
                },
                "required": ["data"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": ToolName.PLOT_FUNCTION,
            "description": (
                "Plot one or more mathematical functions and return a base64-encoded PNG. "
                "Uses matplotlib with numpy vectorised evaluation."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "expressions": {
                        "type": "array",
                        "description": "List of LaTeX expressions to plot",
                        "items": {"type": "string"},
                    },
                    "variable": {"type": "string", "description": "Independent variable (default: x)"},
                    "x_range": {
                        "type": "array",
                        "description": "Plot range [xmin, xmax] (default: [-10, 10])",
                        "items": {"type": "number"},
                    },
                    "num_points": {"type": "integer", "description": "Number of sample points (default: 1000)"},
                    "title": {"type": "string", "description": "Plot title"},
                },
                "required": ["expressions"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": ToolName.WOLFRAM,
            "description": "Query Wolfram Alpha for a math or science question. Returns a short answer.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Natural language or LaTeX query for Wolfram Alpha"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": ToolName.EXEC_PYTHON,
            "description": (
                "Execute a Python code snippet. Pre-imported: numpy (np), scipy, sympy, mpmath, "
                "pandas (pd), networkx (nx), matplotlib.pyplot (plt), statistics, fractions, "
                "itertools, functools. Returns stdout output."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {"type": "string", "description": "Python code to execute"},
                },
                "required": ["code"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": ToolName.COMPARE_ANSWERS,
            "description": "Check whether two LaTeX math answers are equivalent (symbolic + numeric checks).",
            "parameters": {
                "type": "object",
                "properties": {
                    "answer_a": {"type": "string", "description": "First LaTeX answer"},
                    "answer_b": {"type": "string", "description": "Second LaTeX answer"},
                },
                "required": ["answer_a", "answer_b"],
            },
        },
    },
]


# ── Helpers ─────────────────────────────────────────────────────────────

def _sympy_expr_to_numpy_func(expr: sp.Basic, variable: sp.Symbol):
    """Convert a SymPy expression to a numpy-callable function (lambdify)."""
    return sp.lambdify(variable, expr, modules=["numpy", "scipy"])


def _format_numpy_array(arr: np.ndarray, name: str = "") -> str:
    """Format a numpy array as a readable string with optional name prefix."""
    prefix = f"{name} = " if name else ""
    if arr.ndim == 1:
        return f"{prefix}[{', '.join(f'{v:.8g}' for v in arr)}]"
    elif arr.ndim == 2:
        rows = ["  [" + ", ".join(f"{v:.8g}" for v in row) + "]" for row in arr]
        return f"{prefix}[\n" + "\n".join(rows) + "\n]"
    return f"{prefix}{arr}"


def _matrix_to_latex(arr: np.ndarray) -> str:
    """Convert a numpy 2-D array to a LaTeX pmatrix."""
    if arr.ndim == 1:
        inner = " \\\\ ".join(f"{v:.8g}" for v in arr)
        return f"\\begin{{pmatrix}} {inner} \\end{{pmatrix}}"
    rows = [" & ".join(f"{v:.8g}" for v in row) for row in arr]
    inner = " \\\\ ".join(rows)
    return f"\\begin{{pmatrix}} {inner} \\end{{pmatrix}}"


# ── Engine ──────────────────────────────────────────────────────────────
class MathEngine:
    """
    Dispatcher that receives a tool name + arguments and returns a ToolResult.

    Designed to be called from the chat service's tool-call loop.
    Uses numpy/scipy for numerical work and sympy for symbolic algebra.
    """

    # ── public dispatch ─────────────────────────────────────────────────

    def call(self, name: str, arguments: dict) -> ToolResult:
        handler = {
            ToolName.PARSE_LATEX: self._parse_latex,
            ToolName.SIMPLIFY: self._simplify,
            ToolName.SOLVE: self._solve,
            ToolName.DIFFERENTIATE: self._differentiate,
            ToolName.INTEGRATE: self._integrate,
            ToolName.SERIES_EXPAND: self._series_expand,
            ToolName.EVALUATE: self._evaluate,
            ToolName.MATRIX_OPS: self._matrix_ops,
            ToolName.NUMERICAL_SOLVE: self._numerical_solve,
            ToolName.NUMERICAL_INTEGRATE: self._numerical_integrate,
            ToolName.STATISTICS: self._statistics,
            ToolName.PLOT_FUNCTION: self._plot_function,
            ToolName.WOLFRAM: self._wolfram,
            ToolName.EXEC_PYTHON: self._exec_python,
            ToolName.COMPARE_ANSWERS: self._compare_answers,
        }.get(name)  # type: ignore[arg-type]

        if handler is None:
            return ToolResult(name=name, success=False, result="", error=f"Unknown tool: {name}")
        try:
            return handler(**arguments)
        except Exception as exc:
            logger.exception("Tool %s failed", name)
            return ToolResult(name=name, success=False, result="", error=str(exc))

    # ── symbolic tools ──────────────────────────────────────────────────

    def _parse_latex(self, latex: str) -> ToolResult:
        res = latex_converter.parse(latex)
        if res.success:
            return ToolResult(
                name=ToolName.PARSE_LATEX,
                success=True,
                result=f"Canonical: ${res.canonical_latex}$\nFree symbols: {res.free_symbols}",
                raw=res,
            )
        return ToolResult(name=ToolName.PARSE_LATEX, success=False, result="", error=res.error)

    def _simplify(self, latex: str) -> ToolResult:
        res = latex_converter.parse(latex)
        if not res.success:
            return ToolResult(name=ToolName.SIMPLIFY, success=False, result="", error=res.error)
        simplified = sp.simplify(res.expr)
        return ToolResult(
            name=ToolName.SIMPLIFY,
            success=True,
            result=f"${sp_latex(simplified)}$",
            raw=simplified,
        )

    def _solve(self, latex: str, variable: str | None = None) -> ToolResult:
        res = latex_converter.parse(latex)
        if not res.success:
            return ToolResult(name=ToolName.SOLVE, success=False, result="", error=res.error)

        expr = res.expr
        # If it's an Eq, solve it directly; otherwise assume expr = 0
        if not isinstance(expr, sp.Eq):
            if "=" in latex:
                parts = latex.split("=", 1)
                lhs = latex_converter.parse(parts[0])
                rhs = latex_converter.parse(parts[1])
                if lhs.success and rhs.success:
                    expr = sp.Eq(lhs.expr, rhs.expr)
                else:
                    expr = sp.Eq(expr, 0)
            else:
                expr = sp.Eq(expr, 0)

        var = sp.Symbol(variable) if variable else (list(expr.free_symbols)[0] if expr.free_symbols else sp.Symbol("x"))
        solutions = sp.solve(expr, var)
        sol_latex = ", ".join(sp_latex(s) for s in solutions)
        return ToolResult(
            name=ToolName.SOLVE,
            success=True,
            result=f"${var} = {sol_latex}$" if solutions else "No solutions found.",
            raw=solutions,
        )

    def _differentiate(self, latex: str, variable: str = "x", order: int = 1) -> ToolResult:
        res = latex_converter.parse(latex)
        if not res.success:
            return ToolResult(name=ToolName.DIFFERENTIATE, success=False, result="", error=res.error)
        var = sp.Symbol(variable)
        deriv = sp.diff(res.expr, var, order)
        return ToolResult(
            name=ToolName.DIFFERENTIATE,
            success=True,
            result=f"${sp_latex(deriv)}$",
            raw=deriv,
        )

    def _integrate(self, latex: str, variable: str = "x", lower: str | None = None, upper: str | None = None) -> ToolResult:
        res = latex_converter.parse(latex)
        if not res.success:
            return ToolResult(name=ToolName.INTEGRATE, success=False, result="", error=res.error)
        var = sp.Symbol(variable)
        if lower is not None and upper is not None:
            lo = latex_converter.parse(lower)
            hi = latex_converter.parse(upper)
            lo_expr = lo.expr if lo.success else sp.sympify(lower)
            hi_expr = hi.expr if hi.success else sp.sympify(upper)
            integral = sp.integrate(res.expr, (var, lo_expr, hi_expr))
        else:
            integral = sp.integrate(res.expr, var)
        return ToolResult(
            name=ToolName.INTEGRATE,
            success=True,
            result=f"${sp_latex(integral)}$",
            raw=integral,
        )

    def _series_expand(self, latex: str, variable: str = "x", point: str = "0", order: int = 6) -> ToolResult:
        """Taylor / Laurent series expansion around a point."""
        res = latex_converter.parse(latex)
        if not res.success:
            return ToolResult(name=ToolName.SERIES_EXPAND, success=False, result="", error=res.error)
        var = sp.Symbol(variable)
        pt_res = latex_converter.parse(point)
        pt = pt_res.expr if pt_res.success else sp.sympify(point)
        series = sp.series(res.expr, var, pt, n=order).removeO()
        return ToolResult(
            name=ToolName.SERIES_EXPAND,
            success=True,
            result=f"${sp_latex(series)} + O({sp_latex(var)}^{{{order}}})$",
            raw=series,
        )

    # ── numerical tools (numpy / scipy / mpmath accelerated) ────────────

    def _evaluate(self, latex: str, substitutions: dict | None = None, precision: int = 15) -> ToolResult:
        """
        Numerically evaluate an expression.
        - For precision <= 15: uses numpy (fast, hardware float64).
        - For precision > 15:  uses mpmath (arbitrary precision).
        """
        res = latex_converter.parse(latex)
        if not res.success:
            return ToolResult(name=ToolName.EVALUATE, success=False, result="", error=res.error)
        expr = res.expr

        if substitutions:
            subs = {sp.Symbol(k): v for k, v in substitutions.items()}
            expr = expr.subs(subs)

        precision = min(max(precision, 1), 100)

        if precision <= 15 and not expr.free_symbols:
            # Fast path: try numpy via lambdify
            try:
                f = sp.lambdify([], expr, modules=["numpy"])
                val = f()
                if np.isfinite(val):
                    return ToolResult(
                        name=ToolName.EVALUATE,
                        success=True,
                        result=f"${val:.15g}$",
                        raw=float(val),
                    )
            except Exception:
                pass  # fall through to sympy/mpmath

        if precision > 15:
            # Use mpmath for arbitrary precision
            with mpmath.workdps(precision + 5):
                val = expr.evalf(precision, chop=True)
                return ToolResult(
                    name=ToolName.EVALUATE,
                    success=True,
                    result=f"${sp_latex(val)}$  ({precision} digits)",
                    raw=val,
                )

        # Default: sympy N (uses mpmath internally)
        val = sp.N(expr)
        return ToolResult(
            name=ToolName.EVALUATE,
            success=True,
            result=f"${sp_latex(val)}$",
            raw=val,
        )

    def _matrix_ops(self, matrix: list[list[float]], operation: str, rhs: list[float] | None = None) -> ToolResult:
        """Matrix / linear-algebra operations powered by numpy."""
        A = np.array(matrix, dtype=np.float64)
        op = operation.lower().strip()

        try:
            if op == "determinant":
                det = np.linalg.det(A)
                return ToolResult(name=ToolName.MATRIX_OPS, success=True, result=f"$\\det(A) = {det:.8g}$", raw=float(det))

            elif op == "inverse":
                inv = np.linalg.inv(A)
                return ToolResult(name=ToolName.MATRIX_OPS, success=True, result=f"$A^{{-1}} = {_matrix_to_latex(inv)}$", raw=inv)

            elif op == "eigenvalues":
                eigvals = np.linalg.eigvals(A)
                vals_str = ", ".join(f"{v:.8g}" for v in eigvals)
                return ToolResult(name=ToolName.MATRIX_OPS, success=True, result=f"Eigenvalues: $\\lambda = {vals_str}$", raw=eigvals)

            elif op == "eigenvectors":
                eigvals, eigvecs = np.linalg.eig(A)
                parts = []
                for i, (val, vec) in enumerate(zip(eigvals, eigvecs.T)):
                    parts.append(f"$\\lambda_{i+1} = {val:.8g}$, $v_{i+1} = {_matrix_to_latex(vec)}$")
                return ToolResult(name=ToolName.MATRIX_OPS, success=True, result="\n".join(parts), raw={"eigenvalues": eigvals, "eigenvectors": eigvecs})

            elif op == "svd":
                U, S, Vt = np.linalg.svd(A)
                return ToolResult(
                    name=ToolName.MATRIX_OPS,
                    success=True,
                    result=f"Singular values: [{', '.join(f'{s:.8g}' for s in S)}]\n$U = {_matrix_to_latex(U)}$\n$V^T = {_matrix_to_latex(Vt)}$",
                    raw={"U": U, "S": S, "Vt": Vt},
                )

            elif op == "rank":
                r = np.linalg.matrix_rank(A)
                return ToolResult(name=ToolName.MATRIX_OPS, success=True, result=f"$\\text{{rank}}(A) = {r}$", raw=int(r))

            elif op == "norm":
                n = np.linalg.norm(A)
                return ToolResult(name=ToolName.MATRIX_OPS, success=True, result=f"$\\|A\\| = {n:.8g}$", raw=float(n))

            elif op == "solve_linear":
                if rhs is None:
                    return ToolResult(name=ToolName.MATRIX_OPS, success=False, result="", error="rhs vector required for solve_linear")
                b = np.array(rhs, dtype=np.float64)
                x = np.linalg.solve(A, b)
                return ToolResult(name=ToolName.MATRIX_OPS, success=True, result=f"$x = {_matrix_to_latex(x)}$", raw=x)

            elif op == "transpose":
                T = A.T
                return ToolResult(name=ToolName.MATRIX_OPS, success=True, result=f"$A^T = {_matrix_to_latex(T)}$", raw=T)

            elif op == "trace":
                tr = np.trace(A)
                return ToolResult(name=ToolName.MATRIX_OPS, success=True, result=f"$\\text{{tr}}(A) = {tr:.8g}$", raw=float(tr))

            elif op == "rref":
                # Use SymPy for exact RREF (numpy doesn't have it)
                M = sp.Matrix(matrix)
                rref_mat, pivots = M.rref()
                return ToolResult(
                    name=ToolName.MATRIX_OPS,
                    success=True,
                    result=f"RREF:\n${sp_latex(rref_mat)}$\nPivot columns: {list(pivots)}",
                    raw={"rref": rref_mat, "pivots": list(pivots)},
                )

            else:
                return ToolResult(name=ToolName.MATRIX_OPS, success=False, result="", error=f"Unknown operation: {operation}")

        except np.linalg.LinAlgError as exc:
            return ToolResult(name=ToolName.MATRIX_OPS, success=False, result="", error=f"Linear algebra error: {exc}")

    def _numerical_solve(
        self,
        latex: str,
        variable: str = "x",
        x0: float = 1.0,
        method: str = "fsolve",
        bracket: list[float] | None = None,
    ) -> ToolResult:
        """Find numerical roots via scipy.optimize."""
        res = latex_converter.parse(latex)
        if not res.success:
            return ToolResult(name=ToolName.NUMERICAL_SOLVE, success=False, result="", error=res.error)

        var = sp.Symbol(variable)
        f_np = _sympy_expr_to_numpy_func(res.expr, var)

        if method == "brentq" and bracket and len(bracket) == 2:
            root = scipy.optimize.brentq(f_np, bracket[0], bracket[1])
            return ToolResult(
                name=ToolName.NUMERICAL_SOLVE,
                success=True,
                result=f"${variable} \\approx {root:.15g}$  (brentq on [{bracket[0]}, {bracket[1]}])",
                raw=float(root),
            )
        elif method == "newton":
            # Also lambdify the derivative for Newton's method
            df = sp.diff(res.expr, var)
            df_np = _sympy_expr_to_numpy_func(df, var)
            root = scipy.optimize.newton(f_np, x0, fprime=df_np)
            return ToolResult(
                name=ToolName.NUMERICAL_SOLVE,
                success=True,
                result=f"${variable} \\approx {root:.15g}$  (Newton, x0={x0})",
                raw=float(root),
            )
        else:
            roots = scipy.optimize.fsolve(f_np, x0, full_output=False)
            root_val = float(roots[0]) if hasattr(roots, '__len__') else float(roots)
            return ToolResult(
                name=ToolName.NUMERICAL_SOLVE,
                success=True,
                result=f"${variable} \\approx {root_val:.15g}$  (fsolve, x0={x0})",
                raw=root_val,
            )

    def _numerical_integrate(
        self,
        latex: str,
        lower: float,
        upper: float,
        variable: str = "x",
    ) -> ToolResult:
        """Fast numerical quadrature via scipy.integrate.quad."""
        res = latex_converter.parse(latex)
        if not res.success:
            return ToolResult(name=ToolName.NUMERICAL_INTEGRATE, success=False, result="", error=res.error)

        var = sp.Symbol(variable)
        f_np = _sympy_expr_to_numpy_func(res.expr, var)
        value, abs_error = sp_integrate.quad(f_np, lower, upper)
        return ToolResult(
            name=ToolName.NUMERICAL_INTEGRATE,
            success=True,
            result=f"$\\int_{{{lower}}}^{{{upper}}} \\ldots \\, d{variable} \\approx {value:.15g}$  (error ≤ {abs_error:.2e})",
            raw={"value": value, "error": abs_error},
        )

    def _statistics(self, data: list[float], operations: list[str] | None = None) -> ToolResult:
        """Descriptive statistics via numpy + scipy.stats."""
        arr = np.array(data, dtype=np.float64)
        ops = [o.lower() for o in operations] if operations else ["describe"]

        results: dict[str, Any] = {}

        def _add(key: str, value: Any):
            results[key] = value

        if "describe" in ops or "mean" in ops:
            _add("mean", float(np.mean(arr)))
        if "describe" in ops or "median" in ops:
            _add("median", float(np.median(arr)))
        if "describe" in ops or "std" in ops:
            _add("std", float(np.std(arr, ddof=1)))
        if "describe" in ops or "var" in ops:
            _add("variance", float(np.var(arr, ddof=1)))
        if "describe" in ops or "min" in ops:
            _add("min", float(np.min(arr)))
        if "describe" in ops or "max" in ops:
            _add("max", float(np.max(arr)))
        if "describe" in ops or "sum" in ops:
            _add("sum", float(np.sum(arr)))
        if "describe" in ops or "skew" in ops:
            _add("skewness", float(scipy.stats.skew(arr)))
        if "describe" in ops or "kurtosis" in ops:
            _add("kurtosis", float(scipy.stats.kurtosis(arr)))
        if "mode" in ops:
            mode_res = scipy.stats.mode(arr, keepdims=False)
            _add("mode", float(mode_res.mode))
        if "describe" in ops or "percentile_25" in ops:
            _add("Q1 (25%)", float(np.percentile(arr, 25)))
        if "describe" in ops or "percentile_75" in ops:
            _add("Q3 (75%)", float(np.percentile(arr, 75)))
        if "describe" in ops or "iqr" in ops:
            _add("IQR", float(scipy.stats.iqr(arr)))
        if "zscore" in ops:
            z = scipy.stats.zscore(arr)
            _add("z-scores", [round(float(v), 6) for v in z])
        if "describe" in ops:
            _add("n", len(arr))

        lines = [f"- **{k}**: {v}" for k, v in results.items()]
        return ToolResult(
            name=ToolName.STATISTICS,
            success=True,
            result="\n".join(lines),
            raw=results,
        )

    def _plot_function(
        self,
        expressions: list[str],
        variable: str = "x",
        x_range: list[float] | None = None,
        num_points: int = 1000,
        title: str | None = None,
    ) -> ToolResult:
        """
        Render one or more math functions to a base64 PNG using matplotlib + numpy.
        """
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        xmin, xmax = (x_range or [-10.0, 10.0])[:2]
        x = np.linspace(xmin, xmax, num_points)
        var = sp.Symbol(variable)

        fig, ax = plt.subplots(figsize=(8, 5), dpi=100)
        for latex_expr in expressions:
            res = latex_converter.parse(latex_expr)
            if not res.success:
                continue
            f_np = _sympy_expr_to_numpy_func(res.expr, var)
            try:
                y = f_np(x)
                y = np.where(np.isfinite(y), y, np.nan)  # mask infinities
            except Exception:
                continue
            ax.plot(x, y, label=f"${res.canonical_latex}$")

        ax.set_xlabel(f"${variable}$")
        ax.set_ylabel("$y$")
        ax.legend()
        ax.grid(True, alpha=0.3)
        if title:
            ax.set_title(title)

        buf = io.BytesIO()
        fig.savefig(buf, format="png", bbox_inches="tight")
        plt.close(fig)
        buf.seek(0)
        b64 = base64.b64encode(buf.read()).decode()
        return ToolResult(
            name=ToolName.PLOT_FUNCTION,
            success=True,
            result=f"![plot](data:image/png;base64,{b64})",
            raw={"base64_png": b64},
        )

    # ── external tools ──────────────────────────────────────────────────

    def _wolfram(self, query: str) -> ToolResult:
        """Query Wolfram Alpha. Requires WOLFRAM_APP_ID in settings."""
        if not settings.wolfram_app_id:
            return ToolResult(name=ToolName.WOLFRAM, success=False, result="", error="Wolfram Alpha APP ID not configured")
        try:
            import wolframalpha  # type: ignore[import-untyped]
            client = wolframalpha.Client(settings.wolfram_app_id)
            res = client.query(query)
            answer = next(res.results).text
            return ToolResult(name=ToolName.WOLFRAM, success=True, result=answer, raw=answer)
        except Exception as exc:
            return ToolResult(name=ToolName.WOLFRAM, success=False, result="", error=str(exc))

    def _exec_python(self, code: str) -> ToolResult:
        output, report = python_sandbox.run(code)
        success = report == "Done"
        return ToolResult(
            name=ToolName.EXEC_PYTHON,
            success=success,
            result=output if success else f"Error: {report}",
            error=None if success else report,
        )

    def _compare_answers(self, answer_a: str, answer_b: str) -> ToolResult:
        """
        Compare two LaTeX answers using:
        1. Symbolic simplification (SymPy)
        2. Numerical evaluation at random points (numpy)
        3. String fallback
        """
        a = latex_converter.parse(answer_a)
        b = latex_converter.parse(answer_b)

        if not a.success or not b.success:
            from app.utils.latex_utils import strip_latex_string
            equal = strip_latex_string(answer_a) == strip_latex_string(answer_b)
            return ToolResult(
                name=ToolName.COMPARE_ANSWERS,
                success=True,
                result=f"String comparison: {'equivalent' if equal else 'not equivalent'}",
                raw=equal,
            )

        # 1. Try symbolic simplification
        try:
            if bool(sp.simplify(a.expr - b.expr) == 0):
                return ToolResult(name=ToolName.COMPARE_ANSWERS, success=True, result="✓ Equivalent (symbolic)", raw=True)
        except Exception:
            pass

        # 2. Numerical cross-check at random points (numpy-accelerated)
        try:
            free = a.expr.free_symbols | b.expr.free_symbols
            if free:
                rng = np.random.default_rng(42)
                test_points = rng.uniform(-10, 10, size=(20, len(free)))
                syms = sorted(free, key=str)
                fa = sp.lambdify(syms, a.expr, modules=["numpy"])
                fb = sp.lambdify(syms, b.expr, modules=["numpy"])
                vals_a = np.array([fa(*pt) for pt in test_points])
                vals_b = np.array([fb(*pt) for pt in test_points])
                if np.allclose(vals_a, vals_b, rtol=1e-10, atol=1e-12, equal_nan=True):
                    return ToolResult(name=ToolName.COMPARE_ANSWERS, success=True, result="✓ Equivalent (numerical, 20 random points)", raw=True)
                else:
                    return ToolResult(name=ToolName.COMPARE_ANSWERS, success=True, result="✗ Not equivalent (numerical)", raw=False)
            else:
                # No free symbols – just evaluate both
                va = complex(sp.N(a.expr))
                vb = complex(sp.N(b.expr))
                equal = np.isclose(va, vb, rtol=1e-12)
                return ToolResult(
                    name=ToolName.COMPARE_ANSWERS,
                    success=True,
                    result=f"{'✓ Equivalent' if equal else '✗ Not equivalent'} (numeric)",
                    raw=bool(equal),
                )
        except Exception:
            pass

        # 3. Fallback: structural equality
        equal = a.expr == b.expr
        return ToolResult(
            name=ToolName.COMPARE_ANSWERS,
            success=True,
            result=f"{'✓ Equivalent' if equal else '✗ Not equivalent'} (structural)",
            raw=bool(equal),
        )


# Module-level singleton
math_engine = MathEngine()
