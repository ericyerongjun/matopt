"""Verified-computation helpers that do not need the kernel."""

from __future__ import annotations

from typing import Any, Optional

from app.agent.events import ToolResult
from app.agent.tools.base import Tool, ToolContext


def _wrap_latex(s: str) -> str:
    s = (s or "").strip()
    if "$" in s or "\\boxed" in s or "\\(" in s or "\\[" in s:
        return s
    return f"${s}$"


def check_equivalence_sync(expr_a: str, expr_b: str) -> dict:
    """Return {'equivalent': bool|None, 'method': str, 'detail': str}."""
    # 1) math-verify (LaTeX aware, handles sets, intervals, matrices, numeric tolerance)
    try:
        from math_verify import parse, verify  # type: ignore

        gold = parse(_wrap_latex(expr_a))
        pred = parse(_wrap_latex(expr_b))
        if gold and pred:
            eq = bool(verify(gold, pred))
            return {"equivalent": eq, "method": "math-verify", "detail": f"parsed A={gold!r}, B={pred!r}"}
    except Exception as exc:  # noqa: BLE001
        mv_error = str(exc)
    else:
        mv_error = "math-verify could not parse one of the expressions"
    # 2) sympy fallback (plain syntax, then LaTeX)
    try:
        import sympy as sp
        from sympy.parsing.sympy_parser import parse_expr, standard_transformations, implicit_multiplication_application

        tf = standard_transformations + (implicit_multiplication_application,)

        def _to_sympy(expr: str):
            raw = (expr or "").strip().strip("$")
            if "\\" in raw:
                from sympy.parsing.latex import parse_latex
                return parse_latex(raw)
            return parse_expr(raw.replace("^", "**"), transformations=tf)

        a = _to_sympy(expr_a)
        b = _to_sympy(expr_b)
        diff = sp.simplify(a - b)
        if diff == 0:
            return {"equivalent": True, "method": "sympy", "detail": "simplify(A - B) == 0"}
        syms = sorted(a.free_symbols | b.free_symbols, key=str)
        if syms:
            import random

            f = sp.lambdify(syms, a - b, "math")
            for _ in range(8):
                vals = [random.uniform(-3, 3) for _ in syms]
                try:
                    if abs(complex(f(*vals))) > 1e-8:
                        return {"equivalent": False, "method": "sympy-numeric", "detail": f"A - B ≠ 0 at {dict(zip(map(str, syms), vals))}"}
                except Exception:  # noqa: BLE001
                    continue
            return {"equivalent": True, "method": "sympy-numeric", "detail": "A - B vanished at 8 random points"}
        return {"equivalent": bool(sp.N(diff) == 0), "method": "sympy-numeric", "detail": f"A - B = {sp.N(diff)}"}
    except Exception as exc:  # noqa: BLE001
        return {"equivalent": None, "method": "none", "detail": f"math-verify: {mv_error}; sympy: {exc}"}


class CheckEquivalenceTool(Tool):
    name = "check_equivalence"
    read_only = True
    concurrent_safe = True
    side_effect_scope = "none"
    risk_level = "low"
    ui_kind = "math"
    description = (
        "Check whether two mathematical expressions/answers are equivalent (LaTeX or plain syntax), e.g. "
        "'\\frac{1}{2}' vs '0.5', 'x^2-1' vs '(x-1)(x+1)', '{1,2}' vs '{2,1}'. Uses math-verify with a SymPy fallback. "
        "Use it to verify a final answer against an independent computation."
    )
    parameters = {
        "type": "object",
        "properties": {
            "expression_a": {"type": "string"},
            "expression_b": {"type": "string"},
        },
        "required": ["expression_a", "expression_b"],
    }

    async def run(self, ctx: ToolContext, expression_a: str, expression_b: str, **_: Any) -> ToolResult:
        res = check_equivalence_sync(expression_a, expression_b)
        eq = res["equivalent"]
        verdict = "EQUIVALENT" if eq else ("NOT equivalent" if eq is False else "UNDETERMINED")
        return ToolResult(ok=eq is not None, output=f"{verdict} ({res['method']}): {res['detail']}", meta=res)


class WolframAlphaTool(Tool):
    name = "wolfram_alpha"
    # A remote read: no local effect, but the query leaves the machine — scope
    # "none" because nothing durable changes here, and the outbound call is the
    # same class of side effect as web_search (which is also scope "none").
    read_only = True
    destructive = False
    concurrent_safe = True
    side_effect_scope = "none"
    risk_level = "low"
    needs_approval = False
    timeout_ms = 30_000          # httpx.AsyncClient(timeout=30)
    ui_kind = "math"
    description = "Query Wolfram|Alpha (short answer) for a fact or computation to cross-check your own result."
    parameters = {
        "type": "object",
        "properties": {"query": {"type": "string"}},
        "required": ["query"],
    }

    def __init__(self, app_id: Optional[str]):
        self.app_id = app_id

    async def run(self, ctx: ToolContext, query: str, **_: Any) -> ToolResult:
        if not self.app_id:
            return ToolResult(ok=False, output="", error="WOLFRAM_APP_ID is not configured.")
        import httpx

        url = "https://api.wolframalpha.com/v1/result"
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.get(url, params={"appid": self.app_id, "i": query})
        if r.status_code != 200:
            return ToolResult(ok=False, output="", error=f"Wolfram|Alpha returned {r.status_code}: {r.text[:200]}")
        return ToolResult(ok=True, output=r.text.strip())
