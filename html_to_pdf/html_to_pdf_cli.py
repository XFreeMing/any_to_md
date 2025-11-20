#!/usr/bin/env python3
"""CLI tool to batch convert HTML exports into PDFs."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from html_to_pdf.converter import convert_html_to_pdf  # noqa: E402 - runtime path tweak

DEFAULT_INPUT_DIR = PROJECT_ROOT / "epub_to_html" / "output"


def _discover_html(folder: Path) -> list[Path]:
    return sorted(path for path in folder.glob("*.html") if path.is_file())


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Convert HTML files to PDF")
    parser.add_argument(
        "--input-dir",
        type=Path,
        default=None,
        help="Directory containing *.html files (default: epub_to_html/output)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="Directory to store generated PDFs (default: alongside HTML)",
    )
    parser.add_argument(
        "--css",
        type=Path,
        default=None,
        help="Optional extra CSS file to merge into the rendered PDF",
    )
    return parser


def _prepare_output_path(html_path: Path, override_dir: Path | None) -> Path:
    if override_dir is None:
        return html_path.with_suffix(".pdf")
    override_dir.mkdir(parents=True, exist_ok=True)
    return override_dir / f"{html_path.stem}.pdf"


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    input_dir = args.input_dir or DEFAULT_INPUT_DIR
    input_dir = input_dir.expanduser().resolve()
    if not input_dir.is_dir():
        print(f"Input directory {input_dir} does not exist", file=sys.stderr)
        return 2

    html_files = _discover_html(input_dir)
    if not html_files:
        print(f"No HTML files in {input_dir}")
        return 0

    extra_css = args.css.read_text(encoding="utf-8") if args.css else None
    success = True
    for html_file in html_files:
        output_path = _prepare_output_path(html_file, args.output_dir)
        try:
            pdf_path = convert_html_to_pdf(
                html_file,
                output_path=output_path,
                extra_css=extra_css,
            )
        except Exception as exc:  # noqa: BLE001 - show rendering errors
            success = False
            print(f"Failed to convert {html_file.name}: {exc}", file=sys.stderr)
            continue
        print(f"Saved {pdf_path}")
    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main())
