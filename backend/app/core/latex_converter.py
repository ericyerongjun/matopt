"""
Wrapper around Qwen2.5-Math's latex2sympy library.

Provides a safe, timeout-guarded conversion layer:
  LaTeX string  ──►  SymPy expression  ──►  canonical LaTeX

Falls back gracefully on parse failures.
Uses mpmath for arbitrary-precision numerical post-processing.
"""

from __future__ import annotations

import logging
import signal
from dataclasses import dataclass, field
from typing import Any, Optional

import mpmath
import numpy as np
import sympy as sp
from sympy.printing.latex import latex as sympy_to_latex

from app.config import settings
from app.utils.latex_utils import clean_latex, validate_latex

logger = logging.getLogger(__name__)

# ── Try to import Qwen2.5-Math latex2sympy ──────────────────────────────
# The library lives in Qwen2.5-Math/evaluation/latex2sympy/
# It must be on sys.path or installed as a package.
_HAS_LATEX2SYMPY = False
try:
    from latex2sympy2 import latex2sympy as _qwen_latex2sympy  # type: ignore[import-untyped]
    _HAS_LATEX2SYMPY = True
except ImportError:
    logger.warning(
        "latex2sympy2 not found – falling back to sympy.parsing.latex.  "
        "Install with:  pip install latex2sympy2  or add Qwen2.5-Math/evaluation/latex2sympy to PYTHONPATH."
    )

# Fallback: SymPy's built-in LaTeX parser (less capable but always available)
from sympy.parsing.latex import parse_latex as _sympy_parse_latex


# ── Timeout helper (POSIX only – harmless no-op on Windows) ────────────
class _Timeout:
    def __init__(self, seconds: int):
        self.seconds = seconds

    def __enter__(self):
        if hasattr(signal, "SIGALRM"):
            signal.signal(signal.SIGALRM, self._handler)
            signal.alarm(self.seconds)
        return self

    def __exit__(self, *_: Any):
        if hasattr(signal, "SIGALRM"):
            signal.alarm(0)

    @staticmethod
    def _handler(signum: int, frame: Any):
        raise TimeoutError("LaTeX → SymPy conversion timed out")


# ── Public data class ───────────────────────────────────────────────────
@dataclass
class ConversionResult:
    """Result of a LaTeX → SymPy conversion attempt."""
    success: bool
    expr: Optional[sp.Basic] = None
    canonical_latex: Optional[str] = None
    error: Optional[str] = None
    source: str = "unknown"  # "latex2sympy2" | "sympy_parse_latex" | "failed"
    free_symbols: list[str] = field(default_factory=list)


# ── Core converter ──────────────────────────────────────────────────────
class LatexConverter:
    """
    Stateless converter: LaTeX string → SymPy expression.

    Usage::

        converter = LatexConverter()
        result = converter.parse(r"\\frac{1}{2} x^2 + 3x - 7")
        if result.success:
            print(result.expr, result.canonical_latex)
    """

    def __init__(self, timeout: int | None = None):
        self.timeout = timeout or settings.sympy_timeout

    # ── public API ──────────────────────────────────────────────────────

    def parse(self, latex_str: str) -> ConversionResult:
        """Convert a LaTeX math string to a SymPy expression."""
        latex_str = latex_str.strip()
        if not latex_str:
            return ConversionResult(success=False, error="Empty input", source="failed")

        # Normalise first
        cleaned = clean_latex(latex_str)

        # Try latex2sympy2 (Qwen) first, then SymPy built-in
        for parser_fn, name in self._parsers():
            try:
                with _Timeout(self.timeout):
                    expr = parser_fn(cleaned)
                if expr is not None:
                    canonical = sympy_to_latex(expr)
                    symbols = sorted(str(s) for s in expr.free_symbols)
                    return ConversionResult(
                        success=True,
                        expr=expr,
                        canonical_latex=canonical,
                        source=name,
                        free_symbols=symbols,
                    )
            except Exception as exc:
                logger.debug("Parser %s failed on %r: %s", name, cleaned[:80], exc)
                continue

        return ConversionResult(
            success=False,
            error=f"All parsers failed for: {latex_str[:120]}",
            source="failed",
        )

    def parse_many(self, latex_strings: list[str]) -> list[ConversionResult]:
        """Batch convert multiple LaTeX strings."""
        return [self.parse(s) for s in latex_strings]

    def to_canonical_latex(self, latex_str: str) -> str | None:
        """Convenience: return canonical LaTeX or None."""
        result = self.parse(latex_str)
        return result.canonical_latex if result.success else None

    def to_numpy_func(self, latex_str: str, variable: str = "x"):
        """
        Parse LaTeX and return a numpy-callable function.

        Returns ``(func, ConversionResult)`` where ``func`` accepts a numpy
        array and returns a numpy array.  Returns ``(None, result)`` on
        parse failure.
        """
        result = self.parse(latex_str)
        if not result.success:
            return None, result
        var = sp.Symbol(variable)
        func = sp.lambdify(var, result.expr, modules=["numpy", "scipy"])
        return func, result

    def evaluate_hp(self, latex_str: str, precision: int = 50) -> str | None:
        """
        High-precision numerical evaluation using mpmath.

        Returns a string representation with *precision* significant digits,
        or ``None`` on failure.
        """
        result = self.parse(latex_str)
        if not result.success or result.expr is None:
            return None
        try:
            with mpmath.workdps(precision + 5):
                val = result.expr.evalf(precision, chop=True)
                return str(val)
        except Exception as exc:
            logger.debug("High-precision eval failed: %s", exc)
            return None

    # ── private ─────────────────────────────────────────────────────────

    @staticmethod
    def _parsers():
        """Yield (callable, name) for each available parser, in priority order."""
        if _HAS_LATEX2SYMPY:
            yield _qwen_latex2sympy, "latex2sympy2"
        yield _safe_sympy_parse, "sympy_parse_latex"


def _safe_sympy_parse(latex_str: str) -> sp.Basic | None:
    """Wrapper around SymPy's parse_latex with common fixups."""
    latex_str = latex_str.replace("dfrac", "frac").replace("tfrac", "frac")
    expr = _sympy_parse_latex(latex_str)
    # Replace symbol named 'pi' with actual pi
    if "\\pi" in latex_str:
        expr = expr.subs({sp.Symbol("pi"): sp.pi})
    expr = expr.subs({sp.Symbol("i"): sp.I})
    return expr


# ── Module-level singleton for convenience ──────────────────────────────
latex_converter = LatexConverter()
