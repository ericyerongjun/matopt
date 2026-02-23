# Utilize MinerU to parse documents and extract structured data for the model to use in conversations.
"""
Document parser: wraps MinerU to convert PDF / image files into
Markdown + LaTeX, then extracts standalone LaTeX blocks.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from app.schemas.document import DocumentParseResponse
from app.utils.latex_utils import extract_latex_blocks

logger = logging.getLogger(__name__)


class DocumentParser:
    """
    Converts uploaded documents (PDF, images) into structured Markdown
    with embedded LaTeX formulas using MinerU.

    TODO: Implement the actual MinerU integration below.
    """

    async def parse(self, file_path: Path) -> DocumentParseResponse:
        """
        Parse a document at *file_path* and return Markdown + LaTeX blocks.

        TODO: Add your MinerU parsing logic here. Example skeleton:

            from magic_pdf.data.data_reader_writer import FileBasedDataWriter, FileBasedDataReader
            reader = FileBasedDataReader(str(file_path.parent))
            writer = FileBasedDataWriter(str(output_dir))
            ...
        """
        raise NotImplementedError(
            "DocumentParser.parse() is not yet implemented. "
            "Integrate MinerU here to convert the file to Markdown."
        )

    def extract_blocks(self, markdown: str) -> list[str]:
        """Extract all LaTeX math blocks from parsed Markdown."""
        return extract_latex_blocks(markdown)


# Module-level singleton
document_parser = DocumentParser()