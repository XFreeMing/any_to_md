"""Print and refresh the one current Markdown directory for each book."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ...application.library import library_dir_for, list_current, matches, materialize


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="any-to-md-library",
        description="按 catalog.sqlite 与导出目录定位每本书的当前 Markdown，并可在仓库 library/ 下建立稳定链接。",
    )
    parser.add_argument("--data-dir", type=Path, default=Path("var"), help="持久数据目录，默认 ./var")
    parser.add_argument(
        "--library-dir",
        type=Path,
        help="链接目录。默认是数据目录的同级 library/（--data-dir ./var 时为 ./library）",
    )
    parser.add_argument("--query", help="书名、作者、原始文件名或 book_id")
    parser.add_argument("--link", action="store_true", help="创建或刷新 library/<名称> 到当前导出目录的链接")
    return parser


def _display(path: Path) -> str:
    try:
        return path.resolve().relative_to(Path.cwd().resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.link:
            resolved, missing = materialize(args.data_dir, args.library_dir)
        else:
            resolved, missing = list_current(args.data_dir)
        library = library_dir_for(args.data_dir, args.library_dir)
    except (OSError, ValueError) as exc:
        print(f"无法定位当前语料：{exc}", file=sys.stderr)
        return 1
    if args.query:
        matched = [book for book in resolved if matches(book, args.query)]
        if len(matched) != 1:
            print(f"未唯一匹配：{args.query}", file=sys.stderr)
            for book in matched:
                print(f"{book.library_name}\t{book.title}\t{book.book_id}", file=sys.stderr)
            return 1
        resolved = matched
        missing = []
    if not resolved and not missing:
        print("没有可定位的当前语料。", file=sys.stderr)
        return 1
    for book in sorted(resolved, key=lambda item: item.library_name):
        link = library / book.library_name
        print(f"{book.library_name}\t{book.book_id}\t{book.export_id}\t{_display(link)} -> {_display(book.directory)}")
    if missing:
        for book in missing:
            print(f"无当前语料：{book['title']}\t{book['book_id']}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
