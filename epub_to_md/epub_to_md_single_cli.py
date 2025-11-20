#!/usr/bin/env python3
"""Convert each EPUB in the working directory into a single Markdown document."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Iterable, List

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from epub_to_md import convert_epub_to_single_markdown  # noqa: E402


def _decode_escape_sequences(value: str) -> str:
    try:
        return value.encode("utf-8").decode("unicode_escape")
    except UnicodeDecodeError:
        return value


def _iter_epub_files(paths: List[str]) -> Iterable[Path]:
    if not paths:
        yield from sorted(SCRIPT_DIR.glob("*.epub"))
        return

    seen: set[Path] = set()
    for raw in paths:
        candidate = Path(raw).expanduser().resolve()
        targets: List[Path]
        if candidate.is_file() and candidate.suffix.lower() == ".epub":
            targets = [candidate]
        elif candidate.is_dir():
            targets = sorted(candidate.glob("*.epub"))
        else:
            continue
        for target in targets:
            resolved = target.resolve()
            if resolved not in seen:
                seen.add(resolved)
                yield resolved


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "paths",
        nargs="*",
        help="Optional EPUB files or directories to process. Defaults to script directory.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=SCRIPT_DIR / "single_markdown_output",
        help="Directory where Markdown files (and assets) should be written.",
    )
    parser.add_argument(
        "--separator",
        default="\n\n---\n\n",
        help="String inserted between chapters in the combined Markdown output.",
    )
    metadata_group = parser.add_mutually_exclusive_group()
    metadata_group.add_argument(
        "--include-metadata",
        dest="include_metadata",
        action="store_true",
        help="Include chapter-level YAML metadata blocks (default).",
    )
    metadata_group.add_argument(
        "--no-metadata",
        dest="include_metadata",
        action="store_false",
        help="Strip chapter-level YAML metadata blocks.",
    )
    parser.set_defaults(include_metadata=True)
    return parser.parse_args()


def main(argv: list[str] | None = None) -> int:
    args = _parse_args()
    output_root = args.output_dir.expanduser().resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    separator = _decode_escape_sequences(args.separator)

    epub_files = list(_iter_epub_files(args.paths))
    if not epub_files:
        print("No EPUB files found to convert.")
        return 0

    success = 0
    for epub_path in epub_files:
        book_dir = output_root / epub_path.stem
        book_dir.mkdir(parents=True, exist_ok=True)
        output_file = book_dir / f"{epub_path.stem}.md"
        assets_dir = book_dir / "assets"
        try:
            combined = convert_epub_to_single_markdown(
                epub_path,
                include_metadata=args.include_metadata,
                output_file=output_file,
                assets_dir=assets_dir,
                chapter_separator=separator,
            )
        except Exception as exc:  # noqa: BLE001
            print(f"Conversion failed for {epub_path.name}: {exc}", file=sys.stderr)
            continue
        line_count = combined.count("\n") + (1 if combined else 0)
        success += 1
        print(f"Saved {output_file} ({line_count} lines)")

    print(f"Done. Converted {success} of {len(epub_files)} files.")
    return 0 if success == len(epub_files) else 1


if __name__ == "__main__":
    raise SystemExit(main())
