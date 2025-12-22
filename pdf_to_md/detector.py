"""PDF type detection - text-based vs scanned/image-based."""

from pathlib import Path

import pymupdf


def is_scanned_pdf(
    pdf_path: Path | str,
    threshold: int = 50,
    sample_pages: int = 5,
) -> bool:
    """
    Detect if a PDF is scanned (image-based) or text-based.

    Args:
        pdf_path: Path to the PDF file
        threshold: Minimum average characters per page to consider as text PDF
        sample_pages: Number of pages to sample for detection

    Returns:
        True if the PDF appears to be scanned/image-based, False if text-based
    """
    pdf_path = Path(pdf_path)

    try:
        doc = pymupdf.open(pdf_path)
        total_chars = 0
        pages_to_check = min(len(doc), sample_pages)

        if pages_to_check == 0:
            doc.close()
            return True  # Empty PDF, treat as scanned

        for i in range(pages_to_check):
            page = doc[i]
            text = page.get_text()
            # Count non-whitespace characters
            total_chars += len(text.strip())

        doc.close()

        avg_chars = total_chars / pages_to_check
        return avg_chars < threshold

    except Exception:
        # If we can't read the PDF, assume it needs OCR
        return True


def get_pdf_info(pdf_path: Path | str) -> dict:
    """
    Get information about a PDF file.

    Args:
        pdf_path: Path to the PDF file

    Returns:
        Dictionary with PDF information
    """
    pdf_path = Path(pdf_path)

    try:
        doc = pymupdf.open(pdf_path)

        info = {
            "page_count": len(doc),
            "is_encrypted": doc.is_encrypted,
            "metadata": doc.metadata,
        }

        # Sample text from first few pages
        sample_pages = min(len(doc), 3)
        total_chars = 0
        has_images = False

        for i in range(sample_pages):
            page = doc[i]
            total_chars += len(page.get_text().strip())
            if page.get_images():
                has_images = True

        info["avg_chars_per_page"] = total_chars / sample_pages if sample_pages > 0 else 0
        info["has_images"] = has_images
        info["is_scanned"] = info["avg_chars_per_page"] < 50

        doc.close()
        return info

    except Exception as e:
        return {
            "error": str(e),
            "is_scanned": True,
        }
