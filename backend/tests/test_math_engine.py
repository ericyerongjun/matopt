"""
Smoke tests for the math engine, LaTeX converter, and Python sandbox.
Run with:  pytest tests/ -v
"""

import pytest
from app.core.latex_converter import LatexConverter, ConversionResult
from app.core.math_engine import MathEngine, ToolName
from app.core.python_sandbox import PythonSandbox
from app.utils.latex_utils import (
    validate_latex,
    clean_latex,
    find_boxed_answer,
    extract_latex_blocks,
)


# ── LaTeX utils ─────────────────────────────────────────────────────────

class TestLatexUtils:
    def test_validate_balanced_braces(self):
        assert validate_latex(r"\frac{1}{2}") is True
        assert validate_latex(r"\frac{1}{2") is False

    def test_clean_latex(self):
        result = clean_latex(r"\dfrac{1}{2}")
        assert "dfrac" not in result
        assert "frac" in result

    def test_find_boxed_answer(self):
        text = r"The answer is \boxed{42}."
        assert find_boxed_answer(text) == "42"

    def test_find_boxed_nested(self):
        text = r"\boxed{\frac{1}{2}}"
        assert find_boxed_answer(text) == r"\frac{1}{2}"

    def test_extract_latex_blocks(self):
        md = "Inline $x^2$ and display $$y = mx + b$$ done."
        blocks = extract_latex_blocks(md)
        assert "x^2" in blocks
        assert "y = mx + b" in blocks


# ── LatexConverter ──────────────────────────────────────────────────────

class TestLatexConverter:
    def setup_method(self):
        self.converter = LatexConverter(timeout=5)

    def test_parse_simple(self):
        result = self.converter.parse(r"x^2 + 1")
        assert result.success is True
        assert result.expr is not None
        assert result.canonical_latex is not None

    def test_parse_empty(self):
        result = self.converter.parse("")
        assert result.success is False

    def test_to_canonical_latex(self):
        canonical = self.converter.to_canonical_latex(r"\frac{1}{2}")
        assert canonical is not None

    def test_to_numpy_func(self):
        func, result = self.converter.to_numpy_func(r"x^2", "x")
        assert func is not None
        assert result.success is True
        import numpy as np
        assert np.isclose(func(3.0), 9.0)

    def test_evaluate_hp(self):
        val = self.converter.evaluate_hp(r"\pi", precision=30)
        assert val is not None
        assert val.startswith("3.14159265358979")


# ── MathEngine ──────────────────────────────────────────────────────────

class TestMathEngine:
    def setup_method(self):
        self.engine = MathEngine()

    def test_simplify(self):
        r = self.engine.call(ToolName.SIMPLIFY, {"latex": r"x^2 - x^2"})
        assert r.success is True
        assert "0" in r.result

    def test_solve(self):
        r = self.engine.call(ToolName.SOLVE, {"latex": r"x^2 - 4 = 0"})
        assert r.success is True

    def test_differentiate(self):
        r = self.engine.call(ToolName.DIFFERENTIATE, {"latex": r"x^3", "variable": "x"})
        assert r.success is True
        assert "3" in r.result  # d/dx(x^3) = 3x^2

    def test_integrate(self):
        r = self.engine.call(ToolName.INTEGRATE, {"latex": r"x", "variable": "x"})
        assert r.success is True

    def test_series_expand(self):
        r = self.engine.call(ToolName.SERIES_EXPAND, {"latex": r"\sin(x)", "variable": "x", "order": 5})
        assert r.success is True
        assert "x" in r.result

    def test_evaluate_numpy_fast_path(self):
        r = self.engine.call(ToolName.EVALUATE, {"latex": r"\sqrt{2}", "precision": 15})
        assert r.success is True
        assert "1.414" in r.result

    def test_evaluate_high_precision(self):
        r = self.engine.call(ToolName.EVALUATE, {"latex": r"\pi", "precision": 50})
        assert r.success is True
        assert "3.14159265358979" in r.result

    def test_unknown_tool(self):
        r = self.engine.call("nonexistent_tool", {})
        assert r.success is False

    def test_compare_equal(self):
        r = self.engine.call(
            ToolName.COMPARE_ANSWERS,
            {"answer_a": r"\frac{2}{4}", "answer_b": r"\frac{1}{2}"},
        )
        assert r.success is True


class TestMatrixOps:
    def setup_method(self):
        self.engine = MathEngine()

    def test_determinant(self):
        r = self.engine.call(ToolName.MATRIX_OPS, {
            "matrix": [[1, 2], [3, 4]],
            "operation": "determinant",
        })
        assert r.success is True
        assert "-2" in r.result

    def test_inverse(self):
        r = self.engine.call(ToolName.MATRIX_OPS, {
            "matrix": [[1, 0], [0, 1]],
            "operation": "inverse",
        })
        assert r.success is True

    def test_eigenvalues(self):
        r = self.engine.call(ToolName.MATRIX_OPS, {
            "matrix": [[2, 0], [0, 3]],
            "operation": "eigenvalues",
        })
        assert r.success is True

    def test_svd(self):
        r = self.engine.call(ToolName.MATRIX_OPS, {
            "matrix": [[1, 2], [3, 4]],
            "operation": "svd",
        })
        assert r.success is True

    def test_rank(self):
        r = self.engine.call(ToolName.MATRIX_OPS, {
            "matrix": [[1, 2], [2, 4]],
            "operation": "rank",
        })
        assert r.success is True
        assert "1" in r.result

    def test_solve_linear(self):
        # Ax = b  =>  [[2,1],[1,3]] x = [5, 10]  =>  x = [1, 3]
        r = self.engine.call(ToolName.MATRIX_OPS, {
            "matrix": [[2, 1], [1, 3]],
            "operation": "solve_linear",
            "rhs": [5, 10],
        })
        assert r.success is True

    def test_rref(self):
        r = self.engine.call(ToolName.MATRIX_OPS, {
            "matrix": [[1, 2, 3], [4, 5, 6]],
            "operation": "rref",
        })
        assert r.success is True
        assert "Pivot" in r.result

    def test_unknown_op(self):
        r = self.engine.call(ToolName.MATRIX_OPS, {
            "matrix": [[1, 2], [3, 4]],
            "operation": "not_real",
        })
        assert r.success is False


class TestNumericalTools:
    def setup_method(self):
        self.engine = MathEngine()

    def test_numerical_solve_fsolve(self):
        r = self.engine.call(ToolName.NUMERICAL_SOLVE, {
            "latex": r"x^2 - 2",
            "x0": 1.5,
        })
        assert r.success is True
        assert "1.414" in r.result

    def test_numerical_solve_brentq(self):
        r = self.engine.call(ToolName.NUMERICAL_SOLVE, {
            "latex": r"x^3 - 1",
            "method": "brentq",
            "bracket": [0.0, 2.0],
        })
        assert r.success is True

    def test_numerical_integrate(self):
        # integral of x from 0 to 1 = 0.5
        r = self.engine.call(ToolName.NUMERICAL_INTEGRATE, {
            "latex": r"x",
            "lower": 0.0,
            "upper": 1.0,
        })
        assert r.success is True
        assert "0.5" in r.result

    def test_statistics_describe(self):
        r = self.engine.call(ToolName.STATISTICS, {
            "data": [1, 2, 3, 4, 5, 6, 7, 8, 9, 10],
        })
        assert r.success is True
        assert "mean" in r.result
        assert "5.5" in r.result

    def test_statistics_zscore(self):
        r = self.engine.call(ToolName.STATISTICS, {
            "data": [10, 20, 30],
            "operations": ["zscore"],
        })
        assert r.success is True
        assert "z-scores" in r.result

    def test_plot_function(self):
        r = self.engine.call(ToolName.PLOT_FUNCTION, {
            "expressions": [r"x^2"],
            "x_range": [-5, 5],
        })
        assert r.success is True
        assert "base64" in r.result


# ── PythonSandbox ───────────────────────────────────────────────────────

class TestPythonSandbox:
    def setup_method(self):
        self.sandbox = PythonSandbox(timeout=3)

    def test_basic_exec(self):
        output, report = self.sandbox.run("print(2 + 3)")
        assert report == "Done"
        assert "5" in output

    def test_sympy_available(self):
        output, report = self.sandbox.run(
            "from sympy import symbols, expand\nx = symbols('x')\nprint(expand((x+1)**2))"
        )
        assert report == "Done"
        assert "x**2" in output

    def test_numpy_available(self):
        output, report = self.sandbox.run(
            "import numpy as np\nprint(np.linalg.det(np.array([[1,2],[3,4]])))"
        )
        assert report == "Done"
        assert "-2" in output

    def test_scipy_available(self):
        output, report = self.sandbox.run(
            "import scipy.optimize\nroot = scipy.optimize.brentq(lambda x: x**2 - 2, 1, 2)\nprint(f'{root:.6f}')"
        )
        assert report == "Done"
        assert "1.414" in output

    def test_mpmath_available(self):
        output, report = self.sandbox.run(
            "import mpmath\nmpmath.mp.dps = 30\nprint(mpmath.pi)"
        )
        assert report == "Done"
        assert "3.14159265358979" in output

    def test_pandas_available(self):
        output, report = self.sandbox.run(
            "import pandas as pd\nprint(pd.Series([1,2,3]).mean())"
        )
        assert report == "Done"
        assert "2" in output

    def test_matplotlib_agg(self):
        output, report = self.sandbox.run(
            "import matplotlib\nprint(matplotlib.get_backend())"
        )
        assert report == "Done"
        assert "Agg" in output

    def test_blocked_input(self):
        output, report = self.sandbox.run("x = input('>')")
        assert report != "Done"

    def test_timeout(self):
        sandbox = PythonSandbox(timeout=1)
        output, report = sandbox.run("import time; time.sleep(10)")
        assert report != "Done"
