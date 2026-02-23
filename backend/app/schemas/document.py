"""Pydantic schemas for the document parsing endpoint."""

from typing import Optional
from pydantic import BaseModel


class DocumentParseResponse(BaseModel):
    markdown: str
    latex_blocks: list[str] = []      # standalone LaTeX blocks extracted
    metadata: Optional[dict] = None   # page count, title, etc.
