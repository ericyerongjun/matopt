"""Pydantic schemas for the export endpoint."""

from enum import Enum
from typing import Optional
from pydantic import BaseModel


class ExportFormat(str, Enum):
    pdf = "pdf"
    latex = "latex"
    markdown = "markdown"


class ExportRequest(BaseModel):
    content: str
    format: ExportFormat
    title: Optional[str] = "MatOpt Export"
    template: Optional[str] = None   # custom Pandoc template name


class ExportResponse(BaseModel):
    filename: str
    content_type: str
    size_bytes: int
