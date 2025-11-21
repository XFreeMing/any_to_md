#!/usr/bin/env python3
"""
Batch converter tool: EPUB -> HTML -> MD (Single & Chapters) + PDF.
Reads from 'books/' and writes to 'output/'.
"""

import logging
import sys
from pathlib import Path

from epub_to_html import convert_epub
from html_to_md import (
    convert_html_to_markdown_chapters,
    convert_html_to_markdown_single,
)
from html_to_pdf import convert_html_to_pdf

# Ensure project root is in sys.path
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="[%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
LOGGER = logging.getLogger("convert_books")

BOOKS_DIR = PROJECT_ROOT / "books"
OUTPUT_DIR = PROJECT_ROOT / "output"


def process_book(epub_path: Path) -> None:
    book_name = epub_path.stem
    LOGGER.info("Processing book: %s", book_name)

    # 1. Prepare output directories
    book_output_dir = OUTPUT_DIR / book_name
    # We use a 'source' folder for the intermediate HTML to keep the root clean
    html_source_dir = book_output_dir / "source"

    # Clean up previous run for this book if needed?
    # For now, we just ensure directories exist.
    html_source_dir.mkdir(parents=True, exist_ok=True)

    # 2. Convert EPUB -> HTML
    LOGGER.info("Step 1: Converting EPUB to HTML...")
    try:
        html_file = convert_epub(epub_path, html_source_dir)
    except Exception as e:
        LOGGER.error("Failed to convert EPUB to HTML: %s", e)
        return

    # 3. Convert HTML -> PDF
    LOGGER.info("Step 2: Converting HTML to PDF...")
    pdf_output_path = book_output_dir / f"{book_name}.pdf"
    try:
        convert_html_to_pdf(html_file, output_path=pdf_output_path)
    except Exception as e:
        LOGGER.error("Failed to convert HTML to PDF: %s", e)

    # 4. Convert HTML -> MD (Single & Chapters)
    LOGGER.info("Step 3: Converting HTML to Markdown...")
    # We want the MD output to be directly in book_output_dir (containing single/, chapters/, assets/)
    # html_to_md logic creates 'single', 'chapters', 'assets' inside the output_root.
    try:
        convert_html_to_markdown_single(html_file, output_root=book_output_dir)
        convert_html_to_markdown_chapters(html_file, output_root=book_output_dir)
    except Exception as e:
        LOGGER.error("Failed to convert HTML to Markdown: %s", e)

    LOGGER.info("Completed processing: %s", book_name)


def main() -> int:
    if not BOOKS_DIR.exists():
        LOGGER.error("Books directory not found: %s", BOOKS_DIR)
        return 1

    epub_files = sorted(list(BOOKS_DIR.glob("*.epub")))
    if not epub_files:
        LOGGER.warning("No EPUB files found in %s", BOOKS_DIR)
        return 0

    LOGGER.info("Found %d EPUB files.", len(epub_files))

    for epub_file in epub_files:
        process_book(epub_file)

    return 0


if __name__ == "__main__":
    sys.exit(main())
