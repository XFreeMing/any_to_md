"""CLI for the M01 book conversion workflow."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ...application.convert_book import convert_book


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="any-to-md-book",
        description=(
            "将一本 EPUB 同时转换为整体版和分章节版 Markdown，并生成统一目录、"
            "位置索引和 SQLite 记录。M01 当前优先支持目录结构有效的 EPUB。"
        ),
    )
    parser.add_argument("--input", required=True, type=Path, help="EPUB 文件路径")
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path("var"),
        help="持久数据目录，默认 ./var",
    )
    parser.add_argument(
        "--view",
        choices=("both",),
        default="both",
        help="M01 固定生成整体版和分章节版",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = convert_book(args.input, args.data_dir)
    except Exception as exc:
        print(f"转换失败：{exc}", file=sys.stderr)
        return 1

    print(f"状态：{result.status}")
    print(f"job_id：{result.job_id}")
    print(f"book_id：{result.book_id}")
    print(f"revision_id：{result.revision_id}")
    print(f"export_id：{result.export_id}")
    print(f"警告数量：{result.warning_count}")
    print(f"输出目录：{result.output_dir}")
    print(f"报告：{result.report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
