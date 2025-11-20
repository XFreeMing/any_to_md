#!/usr/bin/env python3
"""CLI for converting HTML files into Markdown (single or multi-file variants)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from html_to_md.converter import (  # noqa: E402 - added to path at runtime
    convert_html_to_markdown_chapters,
    convert_html_to_markdown_single,
)

DEFAULT_INPUT_DIR = PROJECT_ROOT / "epub_to_html" / "output"
if not DEFAULT_INPUT_DIR.is_dir():
    DEFAULT_INPUT_DIR = SCRIPT_DIR


def _discover_html_files(folder: Path) -> list[Path]:
    return sorted(path for path in folder.glob("*.html") if path.is_file())


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Convert HTML exports to Markdown")
    parser.add_argument(
        "--input-dir",
        type=Path,
        default=None,
        help="Directory containing *.html exports (default: autodetect)",
    )
    parser.add_argument(
        "--mode",
        choices=("chapters", "single", "both"),
        default="both",
        help="Which Markdown format to generate",
    )
    return parser


def _convert_file(html_path: Path, mode: str) -> None:
    print(f"Processing {html_path}")
    if mode in ("chapters", "both"):
        chapters = convert_html_to_markdown_chapters(html_path)
        target_dir = chapters[0].output_path.parent if chapters else html_path.parent
        print(f"  Chapters: wrote {len(chapters)} files under {target_dir}")
    if mode in ("single", "both"):
        single_doc = convert_html_to_markdown_single(html_path)
        print(f"  Single file: {single_doc.output_path}")


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    input_dir = args.input_dir
    if input_dir is None:
        input_dir = DEFAULT_INPUT_DIR
    input_dir = input_dir.expanduser().resolve()

    if not input_dir.is_dir():
        print(f"Input directory {input_dir} does not exist", file=sys.stderr)
        return 2

    html_files = _discover_html_files(input_dir)
    if not html_files:
        print(f"No .html files found in {input_dir}")
        return 0

    success = True
    for html_path in html_files:
        try:
            _convert_file(html_path, args.mode)
        except Exception as exc:  # noqa: BLE001 - surface conversion errors
            success = False
            print(f"Failed to convert {html_path.name}: {exc}", file=sys.stderr)
    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main())
