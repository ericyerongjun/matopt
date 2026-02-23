"""Pydantic schemas for the OCR endpoint."""

from pydantic import BaseModel, Field


class OcrResponse(BaseModel):
    latex: str
    confidence: float = Field(0.0, ge=0.0, le=1.0)
    sympy_valid: bool = False         # True if latex2sympy parsed successfully
    canonical_latex: str | None = None  # re-rendered from SymPy (if valid)
