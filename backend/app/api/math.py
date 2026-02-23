# Direct symbolic math operations (no LLM required)
"""
POST /api/math/{operation} — invoke the math engine directly.
Useful for real-time formula validation / simplification in the frontend.

Covers both symbolic (SymPy) and numerical (numpy/scipy) operations.
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, Field
from typing import Optional

from app.core.math_engine import math_engine, ToolName

router = APIRouter(prefix="/api/math", tags=["math"])


# ── Request / Response models ───────────────────────────────────────────

class MathRequest(BaseModel):
    latex: str
    variable: Optional[str] = None
    order: Optional[int] = 1
    lower: Optional[str] = None
    upper: Optional[str] = None
    substitutions: Optional[dict] = None
    precision: Optional[int] = 15


class MatrixRequest(BaseModel):
    matrix: list[list[float]]
    operation: str
    rhs: Optional[list[float]] = None


class NumericalSolveRequest(BaseModel):
    latex: str
    variable: Optional[str] = "x"
    x0: Optional[float] = 1.0
    method: Optional[str] = "fsolve"
    bracket: Optional[list[float]] = None


class NumericalIntegrateRequest(BaseModel):
    latex: str
    lower: float
    upper: float
    variable: Optional[str] = "x"


class StatisticsRequest(BaseModel):
    data: list[float]
    operations: Optional[list[str]] = None


class PlotRequest(BaseModel):
    expressions: list[str]
    variable: Optional[str] = "x"
    x_range: Optional[list[float]] = None
    num_points: Optional[int] = 1000
    title: Optional[str] = None


class SeriesRequest(BaseModel):
    latex: str
    variable: Optional[str] = "x"
    point: Optional[str] = "0"
    order: Optional[int] = 6


class MathResponse(BaseModel):
    success: bool
    result: str
    error: Optional[str] = None


# ── Symbolic endpoints ──────────────────────────────────────────────────

@router.post("/parse", response_model=MathResponse)
async def parse_latex(req: MathRequest):
    r = math_engine.call(ToolName.PARSE_LATEX, {"latex": req.latex})
    return MathResponse(success=r.success, result=r.result, error=r.error)


@router.post("/simplify", response_model=MathResponse)
async def simplify(req: MathRequest):
    r = math_engine.call(ToolName.SIMPLIFY, {"latex": req.latex})
    return MathResponse(success=r.success, result=r.result, error=r.error)


@router.post("/solve", response_model=MathResponse)
async def solve(req: MathRequest):
    args: dict = {"latex": req.latex}
    if req.variable:
        args["variable"] = req.variable
    r = math_engine.call(ToolName.SOLVE, args)
    return MathResponse(success=r.success, result=r.result, error=r.error)


@router.post("/differentiate", response_model=MathResponse)
async def differentiate(req: MathRequest):
    args: dict = {"latex": req.latex}
    if req.variable:
        args["variable"] = req.variable
    if req.order:
        args["order"] = req.order
    r = math_engine.call(ToolName.DIFFERENTIATE, args)
    return MathResponse(success=r.success, result=r.result, error=r.error)


@router.post("/integrate", response_model=MathResponse)
async def integrate(req: MathRequest):
    args: dict = {"latex": req.latex}
    if req.variable:
        args["variable"] = req.variable
    if req.lower:
        args["lower"] = req.lower
    if req.upper:
        args["upper"] = req.upper
    r = math_engine.call(ToolName.INTEGRATE, args)
    return MathResponse(success=r.success, result=r.result, error=r.error)


@router.post("/series", response_model=MathResponse)
async def series_expand(req: SeriesRequest):
    args: dict = {"latex": req.latex}
    if req.variable:
        args["variable"] = req.variable
    if req.point:
        args["point"] = req.point
    if req.order:
        args["order"] = req.order
    r = math_engine.call(ToolName.SERIES_EXPAND, args)
    return MathResponse(success=r.success, result=r.result, error=r.error)


@router.post("/evaluate", response_model=MathResponse)
async def evaluate(req: MathRequest):
    args: dict = {"latex": req.latex}
    if req.substitutions:
        args["substitutions"] = req.substitutions
    if req.precision:
        args["precision"] = req.precision
    r = math_engine.call(ToolName.EVALUATE, args)
    return MathResponse(success=r.success, result=r.result, error=r.error)


# ── Numerical / numpy / scipy endpoints ─────────────────────────────────

@router.post("/matrix", response_model=MathResponse)
async def matrix_ops(req: MatrixRequest):
    args: dict = {"matrix": req.matrix, "operation": req.operation}
    if req.rhs is not None:
        args["rhs"] = req.rhs
    r = math_engine.call(ToolName.MATRIX_OPS, args)
    return MathResponse(success=r.success, result=r.result, error=r.error)


@router.post("/numerical-solve", response_model=MathResponse)
async def numerical_solve(req: NumericalSolveRequest):
    args: dict = {"latex": req.latex}
    if req.variable:
        args["variable"] = req.variable
    if req.x0 is not None:
        args["x0"] = req.x0
    if req.method:
        args["method"] = req.method
    if req.bracket:
        args["bracket"] = req.bracket
    r = math_engine.call(ToolName.NUMERICAL_SOLVE, args)
    return MathResponse(success=r.success, result=r.result, error=r.error)


@router.post("/numerical-integrate", response_model=MathResponse)
async def numerical_integrate(req: NumericalIntegrateRequest):
    args: dict = {
        "latex": req.latex,
        "lower": req.lower,
        "upper": req.upper,
    }
    if req.variable:
        args["variable"] = req.variable
    r = math_engine.call(ToolName.NUMERICAL_INTEGRATE, args)
    return MathResponse(success=r.success, result=r.result, error=r.error)


@router.post("/statistics", response_model=MathResponse)
async def statistics_compute(req: StatisticsRequest):
    args: dict = {"data": req.data}
    if req.operations:
        args["operations"] = req.operations
    r = math_engine.call(ToolName.STATISTICS, args)
    return MathResponse(success=r.success, result=r.result, error=r.error)


@router.post("/plot", response_model=MathResponse)
async def plot_function(req: PlotRequest):
    args: dict = {"expressions": req.expressions}
    if req.variable:
        args["variable"] = req.variable
    if req.x_range:
        args["x_range"] = req.x_range
    if req.num_points:
        args["num_points"] = req.num_points
    if req.title:
        args["title"] = req.title
    r = math_engine.call(ToolName.PLOT_FUNCTION, args)
    return MathResponse(success=r.success, result=r.result, error=r.error)
