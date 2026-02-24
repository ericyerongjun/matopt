"""
OCR service: converts images of handwritten or printed formulas into LaTeX
using pix2tex (LaTeX-OCR), then validates the result through latex2sympy.
"""

from __future__ import annotations

import logging
from pathlib import Path

from app.config import settings
from app.core.latex_converter import latex_converter
from app.schemas.ocr import OcrResponse

logger = logging.getLogger(__name__)


class OcrService:
    """
    Handles image -> LaTeX conversion for handwritten formulas.

    TODO: Implement the model loading and prediction below.
    """

    def __init__(self):
        self._model = None  # lazy-loaded

    def _load_model(self):
        """
        Lazy-load the pix2tex LaTeX-OCR model.

        TODO: Add your model loading logic here. Example:

            from pix2tex.cli import LatexOCR
            self._model = LatexOCR()
        """
        raise NotImplementedError(
            "OcrService._load_model() is not yet implemented. "
            "Load the pix2tex LatexOCR model here."
        )

    async def recognise(self, image_path: Path) -> OcrResponse:
        """
        Recognise a formula from an image file.

        TODO: Add your OCR prediction logic here. Example skeleton:

            from PIL import Image
            img = Image.open(image_path).convert("RGB")
            latex_str = self._model(img)

        After OCR, we validate through latex2sympy for a confidence boost.
        """
        raise NotImplementedError(
            "OcrService.recognise() is not yet implemented. "
            "Run pix2tex prediction and return OcrResponse."
        )

    def _validate_with_sympy(self, latex: str) -> tuple[bool, str | None]:
        """
        Post-OCR validation: try to parse the LaTeX through latex2sympy.
        Returns (is_valid, canonical_latex_or_None).
        """
        result = latex_converter.parse(latex)
        return result.success, result.canonical_latex


# Module-level singleton
ocr_service = OcrService()