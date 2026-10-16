"""PDF to Markdown conversion with automatic OCR fallback."""

import asyncio
from pathlib import Path
from typing import Callable, Optional

from any_to_md.readers.pdf.analysis import analyze_pdf


async def convert_pdf_to_markdown(
    pdf_path: Path | str,
    output_dir: Path | str,
    force_ocr: bool = False,
    progress_callback: Optional[Callable[[float, str], None]] = None,
) -> Path:
    """
    Convert PDF to Markdown, automatically detecting if OCR is needed.

    Args:
        pdf_path: Path to the PDF file
        output_dir: Directory for output files
        force_ocr: Force OCR even for text-based PDFs
        progress_callback: Optional callback for progress updates (0-1, message)

    Returns:
        Path to the generated Markdown file
    """
    pdf_path = Path(pdf_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    def report_progress(pct: float, msg: str):
        if progress_callback:
            progress_callback(pct, msg)

    # Check if PDF needs OCR
    report_progress(0.1, "Analyzing PDF...")

    analysis = await asyncio.to_thread(analyze_pdf, pdf_path)
    if force_ocr or analysis.needs_ocr:
        # Use OCR for scanned PDFs
        report_progress(0.2, "Detected scanned PDF, using OCR...")

        from ocr_to_md import convert_with_ocr

        md_path, images_dir = await convert_with_ocr(pdf_path, output_dir)

        report_progress(1.0, "OCR conversion complete")
        return md_path

    else:
        # Use pymupdf4llm for text-based PDFs
        report_progress(0.2, "Extracting text from PDF...")

        md_path = await asyncio.to_thread(_write_native_markdown, pdf_path, output_dir)

        report_progress(1.0, "Text extraction complete")
        return md_path


def convert_pdf_to_markdown_sync(
    pdf_path: Path | str,
    output_dir: Path | str,
    force_ocr: bool = False,
) -> Path:
    """
    Synchronous version of convert_pdf_to_markdown.

    For text-based PDFs only. Scanned PDFs require async OCR.
    """
    pdf_path = Path(pdf_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    analysis = analyze_pdf(pdf_path)
    if force_ocr or analysis.needs_ocr:
        raise ValueError(
            "Scanned PDF detected. Use async convert_pdf_to_markdown() for OCR support."
        )

    return _write_native_markdown(pdf_path, output_dir)


def _write_native_markdown(pdf_path: Path, output_dir: Path) -> Path:
    import pymupdf4llm

    md_path = output_dir / f"{pdf_path.stem}.md"
    md_path.write_text(pymupdf4llm.to_markdown(str(pdf_path)), encoding="utf-8")
    return md_path
