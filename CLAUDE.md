# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

A Python toolkit for converting EPUB books to various formats (HTML, Markdown, PDF). The pipeline typically flows: EPUB → HTML → Markdown/PDF.

## Commands

### Setup
```bash
# Uses uv for dependency management (Python 3.13+)
uv sync
```

### Conversion Workflows

**Batch EPUB → chapter-level Markdown:**
```bash
cd epub_to_md
python epub_to_md_cli.py
# Outputs to markdown_output/<book>/<chapter>.md
```

**EPUB → single Markdown file:**
```bash
cd epub_to_md
python epub_to_md_single_cli.py --output-dir ./single_output
# Options: --no-metadata, --separator "\\n\\n***\\n\\n"
```

**Full pipeline (EPUB → HTML → MD + PDF):**
```bash
# Place .epub files in books/ directory
python convert_books.py
# Outputs to output/<book>/ with source/, single/, chapters/, assets/
```

**HTML → Markdown:**
```bash
cd html_to_md
python html_to_md_cli.py --mode both --input-dir ../epub_to_html/output
```

**HTML → PDF:**
```bash
cd html_to_pdf
python html_to_pdf_cli.py --input-dir ../epub_to_html/output --output-dir ../pdf_output
# Requires WeasyPrint system deps: brew install cairo pango gdk-pixbuf libffi (macOS)
```

## Architecture

### Module Structure

- `epub_to_md/` - Core EPUB→Markdown conversion
  - `__init__.py` - Main library: `convert_epub_to_markdown()`, `convert_epub_to_single_markdown()`
  - Uses TOC leaf nodes to split chapters; falls back to spine order if no TOC
  - Handles tables, images, internal links, and YAML metadata blocks

- `epub_to_html/` - EPUB→HTML conversion
  - `epub_to_html.py` - Concatenates spine documents into single HTML with `<section>` wrappers
  - Rewrites internal links/images, extracts assets to `<book>_assets/`

- `html_to_md/` - HTML→Markdown conversion
  - `converter.py` - `convert_html_to_markdown_chapters()`, `convert_html_to_markdown_single()`
  - Splits on `<section>` elements, copies assets, rewrites URLs

- `html_to_pdf/` - HTML→PDF via WeasyPrint
  - `converter.py` - Embeds CJK fonts from `md_to_any/fonts/`, handles emoji fallback

- `md_to_any/` - Legacy Java services for MD→PDF/Word (reference only, not actively used in Python workflows)

### Key Data Classes

- `Chapter` (epub_to_md): index, title, content, file_name, output_path, source_path, anchor
- `TocEntry` (epub_to_md): Parsed TOC leaf with index, title, href, path, fragment
- `MarkdownDocument` (html_to_md): index, title, content, file_name, output_path
- `ManifestItem` (epub_to_html): item_id, href, path, media_type

### Dependencies

Core: `ebooklib`, `beautifulsoup4`, `markdownify`, `weasyprint`
