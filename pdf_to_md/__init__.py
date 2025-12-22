"""PDF to Markdown conversion module with OCR support."""

from .converter import convert_pdf_to_markdown
from .detector import is_scanned_pdf

__all__ = ["convert_pdf_to_markdown", "is_scanned_pdf"]
