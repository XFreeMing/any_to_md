"""Existing PDF callers consume the single exhaustive page analysis."""

from pathlib import Path

from any_to_md.readers.pdf.analysis import analyze_pdf


def is_scanned_pdf(pdf_path: Path | str) -> bool:
    """Whether any page needs OCR; errors propagate to the durable job report."""
    return analyze_pdf(pdf_path).needs_ocr


def get_pdf_info(pdf_path: Path | str) -> dict:
    """Whole-source facts from the same page classifications as conversion."""
    analysis = analyze_pdf(pdf_path)
    return {**analysis.to_dict(), 'is_encrypted': False,
            'avg_chars_per_page': sum(page.native_characters for page in analysis.pages)/len(analysis.pages),
            'has_images': any(page.image_count for page in analysis.pages), 'is_scanned': analysis.needs_ocr}
