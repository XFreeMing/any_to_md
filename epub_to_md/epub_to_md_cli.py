#!/usr/bin/env python3
"""Batch convert EPUB files located alongside this script into Markdown chapters."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Iterable

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from epub_to_md import (
    convert_epub_to_markdown,  # noqa: E402 - import after path adjustment
)


def _discover_epubs(folder: Path) -> Iterable[Path]:
    return sorted(path for path in folder.glob("*.epub") if path.is_file())


def main(argv: list[str] | None = None) -> int:  # noqa: ARG001 - CLI ignores argv
    script_dir = SCRIPT_DIR
    epub_files = list(_discover_epubs(script_dir))

    if not epub_files:
        print(f"No .epub files found in {script_dir}")
        return 0

    output_root = script_dir / "markdown_output"
    output_root.mkdir(exist_ok=True)

    success_count = 0
    for epub_path in epub_files:
        book_output_dir = output_root / epub_path.stem
        try:
            chapters = convert_epub_to_markdown(
                epub_path,
                output_dir=book_output_dir,
            )
        except Exception as exc:  # noqa: BLE001 - surface any conversion errors to CLI
            print(f"Conversion failed for {epub_path.name}: {exc}", file=sys.stderr)
            continue
        success_count += 1
        for chapter in chapters:
            destination = chapter.output_path or (book_output_dir / chapter.file_name)
            line_count = chapter.content.count("\n") + 1 if chapter.content else 0
            print(f"Wrote {destination} ({line_count} lines)")
        print(f"Saved {len(chapters)} chapters to {book_output_dir}")

    print(f"Done. Converted {success_count} of {len(epub_files)} files.")
    return 0 if success_count == len(epub_files) else 1


if __name__ == "__main__":
    raise SystemExit(main())
