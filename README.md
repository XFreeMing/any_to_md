# any_to_md

Utility scripts that turn EPUB books (and their exported HTML) into Markdown/PDF. Available workflows:

- `epub_to_md_cli.py`: splits every EPUB into chapter-level Markdown files.
- `epub_to_md_single_cli.py`: produces one Markdown file per EPUB, keeping images under an `assets/` directory.
- `html_to_md_cli.py`: converts HTML exports (for example, the output of `epub_to_html.py`) into Markdown, generating both chapter-per-file and single-file variants.
- `html_to_pdf_cli.py`: renders HTML exports to PDF with Chinese, emoji, images, and tables preserved.

## Requirements

- Python 3.13+ (uses [uv](https://github.com/astral-sh/uv) for dependency management)
- Dependencies listed in `pyproject.toml`

## Quick Start (Web Interface)

```bash
# Install dependencies
uv sync

# Start the web server
uv run any-to-md
```

Open http://localhost:8888 to access the web interface. API documentation is available at http://localhost:8888/docs.

### Configuration

Set via environment variables or `.env` file:

| Variable | Default | Description |
|----------|---------|-------------|
| `HOST` | `0.0.0.0` | Server bind address |
| `PORT` | `8888` | Server port |
| `TEMP_DIR` | `temp` | Temporary file storage |
| `MAX_UPLOAD_SIZE` | `104857600` | Max upload size (100MB) |
| `FILE_EXPIRE_SECONDS` | `3600` | File expiration time (1 hour) |

### Alternative Start Methods

```bash
# Using uvicorn directly
uv run uvicorn web.app:app --reload --host 0.0.0.0 --port 8888

# Using Python module
uv run python -m web.app
```

## CLI Tools

### Batch chapter conversion

```bash
cd epub_to_md
python epub_to_md_cli.py
```

All `.epub` files next to the script are converted into `markdown_output/<book>/<chapter>.md` directories.

### Single-file conversion

```bash
cd epub_to_md
python epub_to_md_single_cli.py --output-dir ./single_output
```

Each EPUB becomes `single_output/<book>/<book>.md`. Images are exported to `single_output/<book>/assets/`. Useful flags:

- `--no-metadata`: omit per-chapter YAML front matter.
- `--separator "\\n\\n***\\n\\n"`: customize the text inserted between chapters (escape sequences are supported).

### HTML exports to Markdown

```bash
cd html_to_md
python html_to_md_cli.py --mode both --input-dir ../epub_to_html/output
```

For every `.html` file in `--input-dir`, the script creates `<book>_md/chapters/*.md` (one Markdown file per section) and `<book>_md/single/<book>.md` (a combined document). Referenced assets (for example `*_assets/...`) are copied into `<book>_md/assets/`, and image/link URLs are rewritten so the Markdown files continue to point to the exported resources.

### HTML exports to PDF

```bash
cd html_to_pdf
python html_to_pdf_cli.py --input-dir ../epub_to_html/output --output-dir ../pdf_output
```

Key details:

- Uses [WeasyPrint](https://weasyprint.org/) for standards-compliant rendering; install system packages noted in their docs if required.
- On macOS, install the GTK stack before running conversions (for example `brew install cairo pango gdk-pixbuf libffi`).
- Built-in styles embed the CJK fonts under `md_to_any/fonts` so Chinese text renders crisply. Emoji fall back to system fonts (`Apple Color Emoji`, `Segoe UI Emoji`, `Noto Color Emoji`).
- Images, tables, and code blocks inherit the HTML semantics. Pass `--css extra.css` to append project-specific tweaks (for example, page headers).
