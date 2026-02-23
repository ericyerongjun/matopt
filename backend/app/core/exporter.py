# Export the generated contents in LaTex, PDF, Markdown, Jupyter Notebook formats for users to download and use in their projects.
"""
Exporter: converts Markdown + LaTeX content into downloadable files
using pypandoc (which wraps Pandoc).

Supported formats: PDF (via LaTeX engine), LaTeX source, Markdown.
"""

from __future__ import annotations

import logging
import uuid
from pathlib import Path
from typing import Optional

from app.config import settings
from app.schemas.export import ExportFormat, ExportRequest, ExportResponse

logger = logging.getLogger(__name__)


class Exporter:
    """
    Converts Markdown content (with LaTeX math) into export files.

    TODO: Implement the export methods below using pypandoc.
    """

    async def export(self, request: ExportRequest) -> tuple[Path, ExportResponse]:
        """
        Generate an export file and return (file_path, metadata).

        TODO: Add your pypandoc conversion logic here. Example skeleton:

            import pypandoc
            output_file = settings.export_dir / f"{uuid.uuid4().hex}.{ext}"
            pypandoc.convert_text(
                request.content,
                to=pandoc_format,
                format="markdown+tex_math_dollars",
                outputfile=str(output_file),
                extra_args=extra_args,
            )
        """
        raise NotImplementedError(
            "Exporter.export() is not yet implemented. "
            "Use pypandoc to convert the content."
        )

    def _get_pandoc_format(self, fmt: ExportFormat) -> str:
        return {
            ExportFormat.pdf: "pdf",
            ExportFormat.latex: "latex",
            ExportFormat.markdown: "markdown",
        }[fmt]

    def _get_extension(self, fmt: ExportFormat) -> str:
        return {
            ExportFormat.pdf: "pdf",
            ExportFormat.latex: "tex",
            ExportFormat.markdown: "md",
        }[fmt]

    def _get_content_type(self, fmt: ExportFormat) -> str:
        return {
            ExportFormat.pdf: "application/pdf",
            ExportFormat.latex: "application/x-tex",
            ExportFormat.markdown: "text/markdown",
        }[fmt]


# Module-level singleton
exporter = Exporter()