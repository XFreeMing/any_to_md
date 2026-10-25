"""Single and batch command line entry to the shared conversion application."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ...application.convert import ConversionRequest, convert
from ...application.capabilities import CAPABILITIES, input_format


def build_parser(*, shortcut: bool = False) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="e2m" if shortcut else "any-to-md-book",
        description=(
            "将 EPUB、HTML（含资源 ZIP）或 DOCX转换为整体版和分章节版 Markdown，并生成统一目录、"
            "位置索引和 SQLite 记录；也可生成 HTML、PDF。所有入口共用同一转换核心。"
        ),
    )
    if shortcut:
        parser.add_argument("input", type=Path, help="EPUB 文件路径（含空格时请加引号）")
    if not shortcut:
        inputs = parser.add_mutually_exclusive_group(required=True)
        inputs.add_argument("--input", nargs="+", type=Path, help="一个或多个 EPUB、HTML、HTML 资源 ZIP 或 DOCX")
    if not shortcut:
        inputs.add_argument("--input-dir", type=Path, help="批量读取目录中已接入共同 Reader 的输入")
        inputs.add_argument("--url", nargs="+", help="一个或多个网页 URL；保存响应与资源快照后转换")
    else:
        parser.set_defaults(input_dir=None, url=None)
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
        help="Markdown 固定生成共享内容的整体版与分章版",
    )
    parser.add_argument("--target", nargs="+", choices=("markdown", "html", "pdf"), default=["markdown"], help="输出格式，可一次选择多个")
    return parser


def main(argv: list[str] | None = None, *, shortcut: bool = False) -> int:
    args = build_parser(shortcut=shortcut).parse_args(argv)
    if shortcut:
        args.input = [args.input]
    if args.input_dir is not None:
        if not args.input_dir.is_dir():
            print(f"输入目录不存在：{args.input_dir}", file=sys.stderr)
            return 1
        sources = sorted(path for path in args.input_dir.iterdir() if path.is_file() and input_format(path.name) is not None and CAPABILITIES[input_format(path.name)].reader is not None)
    else:
        sources = args.url or args.input
    if not sources:
        print("未找到支持的输入。", file=sys.stderr)
        return 1
    failed = False
    partial = False
    for source in sources:
        try:
            result = convert(ConversionRequest(source, args.data_dir, tuple(args.target)))
        except Exception as exc:
            print(f"转换失败：{source}：{exc}", file=sys.stderr)
            failed = True
            continue
        print(f"输入：{source}\n状态：{result.status}\njob_id：{result.job_id}\nbook_id：{result.book_id}\nrevision_id：{result.revision_id}\nexport_id：{result.export_id}\n警告数量：{result.warning_count}\n输出目录：{result.output_dir}\n报告：{result.report_path}")
        if result.output_dir is not None and "markdown" in args.target:
            print(f"整本 Markdown：{result.output_dir / 'book.md'}\n章节目录：{result.output_dir / 'TOC.md'}")
        partial = partial or result.status == "partial"
    return 1 if failed else 2 if partial else 0


def e2m(argv: list[str] | None = None) -> int:
    """Short EPUB command using the shared conversion workflow."""
    return main(argv, shortcut=True)


if __name__ == "__main__":
    raise SystemExit(main())
