# Mathematics knowledge database integration from Wolfram Alpha, Math Stack Exchange, MathWorld, Sympy
"""
Math knowledge: a higher-level layer for querying external math knowledge
sources (Wolfram Alpha, MathWorld, etc.) and combining with SymPy.

For the core symbolic operations (simplify, solve, differentiate, …)
see :mod:`app.core.math_engine`.  This module focuses on *knowledge lookup*.
"""

from __future__ import annotations

import logging
from typing import Optional

from app.config import settings

logger = logging.getLogger(__name__)


class MathKnowledge:
    """
    External math knowledge integration.

    TODO: Implement the methods below to query knowledge sources.
    """

    async def wolfram_short_answer(self, query: str) -> Optional[str]:
        """
        Get a short textual answer from Wolfram Alpha.

        TODO: Add your Wolfram Alpha short-answers API call here.
        """
        raise NotImplementedError(
            "MathKnowledge.wolfram_short_answer() is not yet implemented."
        )

    async def wolfram_step_by_step(self, query: str) -> Optional[str]:
        """
        Get a step-by-step solution from Wolfram Alpha (Pro feature).

        TODO: Add your Wolfram Alpha step-by-step API call here.
        """
        raise NotImplementedError(
            "MathKnowledge.wolfram_step_by_step() is not yet implemented."
        )

    async def lookup_formula(self, name: str) -> Optional[str]:
        """
        Look up a named formula / theorem from a knowledge base.

        TODO: Build or integrate a formula database (MathWorld scrape,
        local JSON, or vector DB).
        """
        raise NotImplementedError(
            "MathKnowledge.lookup_formula() is not yet implemented."
        )


# Module-level singleton
math_knowledge = MathKnowledge()