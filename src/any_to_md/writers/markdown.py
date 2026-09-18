"""Render whole-book and split Markdown views from one export plan."""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path, PurePosixPath
from urllib.parse import quote

from ..domain.artifacts import ExportPlan, ExportUnit
from ..domain.book import BookRevision, StructureNode
from ..readers.epub.reader import asset_token


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content.rstrip() + "\n", encoding="utf-8")


def _write_json(path: Path, payload: object) -> None:
    _write_text(path, json.dumps(payload, ensure_ascii=False, indent=2))


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _escape_label(value: str) -> str:
    return value.replace("[", "\\[").replace("]", "\\]")


def _markdown_link(path: str, anchor: str | None = None) -> str:
    encoded = quote(path, safe="/._-")
    return f"{encoded}#{anchor}" if anchor else encoded


def _shift_markdown_headings(markdown: str, offset: int) -> str:
    if offset <= 0:
        return markdown
    lines: list[str] = []
    fence: tuple[str, int] | None = None
    for line in markdown.splitlines():
        if fence is not None:
            closing = re.match(r"^ {0,3}(`+|~+)[ \t]*$", line)
            if (
                closing
                and closing.group(1)[0] == fence[0]
                and len(closing.group(1)) >= fence[1]
            ):
                fence = None
            lines.append(line)
            continue
        opening = re.match(r"^ {0,3}(`{3,}|~{3,})(.*)$", line)
        if opening:
            marker = opening.group(1)
            info = opening.group(2)
            if marker[0] == "~" or "`" not in info:
                fence = (marker[0], len(marker))
                lines.append(line)
                continue
        heading = re.match(r"^(#{1,6})([ \t]+.+)$", line)
        if heading:
            level = min(len(heading.group(1)) + offset, 6)
            line = f"{'#' * level}{heading.group(2)}"
        lines.append(line)
    return "\n".join(lines)


def _render_content(
    book: BookRevision,
    node: StructureNode,
    output_file: Path,
    root: Path,
    heading_offset: int,
) -> str:
    content = node.content_markdown
    for asset_id, asset in book.assets.items():
        target = root / "assets" / asset.file_name
        relative = PurePosixPath(os.path.relpath(target, output_file.parent)).as_posix()
        content = content.replace(asset_token(asset_id), quote(relative, safe="/._-"))
    return _shift_markdown_headings(content, heading_offset).strip()


def _node_heading(book: BookRevision, node_id: str, output_file: Path, root: Path, level: int) -> str:
    node = book.nodes[node_id]
    parts = [f'<a id="{node.anchor}"></a>', f"{'#' * min(level, 6)} {node.title_display}"]
    content = _render_content(book, node, output_file, root, heading_offset=level)
    if content:
        parts.append(content)
    return "\n\n".join(parts)


def _toc_lines(
    book: BookRevision,
    plan: ExportPlan,
    *,
    context: str,
    node_ids: list[str] | None = None,
    base_depth: int = 1,
) -> list[str]:
    lines: list[str] = []

    def visit(node_id: str, depth: int) -> None:
        node = book.nodes[node_id]
        single = plan.single_locations[node_id]
        split = plan.split_locations[node_id]
        if context == "toc":
            single_link = _markdown_link(single.path, single.anchor)
            split_link = _markdown_link(split.path, split.anchor)
            target = f"[整体版]({single_link}) · [分章版]({split_link})"
        elif context == "book":
            target = f"[定位](#{single.anchor})"
        else:
            raise ValueError(f"未知目录上下文：{context}")
        lines.append(f"{'  ' * (depth - base_depth)}- {_escape_label(node.title_display)} — {target}")
        for child_id in node.children_ids:
            visit(child_id, depth + 1)

    for node_id in node_ids or book.roots:
        visit(node_id, book.nodes[node_id].depth)
    return lines


def _front_matter(book: BookRevision) -> str:
    lines = ["---", f"title: {json.dumps(book.metadata.title, ensure_ascii=False)}"]
    if book.metadata.authors:
        lines.append("authors:")
        lines.extend(
            f"  - {json.dumps(author, ensure_ascii=False)}" for author in book.metadata.authors
        )
    if book.metadata.language:
        lines.append(f"language: {json.dumps(book.metadata.language, ensure_ascii=False)}")
    lines.extend(
        [
            f"book_id: {book.book_id}",
            f"edition_id: {book.edition_id}",
            f"revision_id: {book.revision_id}",
            "---",
        ]
    )
    return "\n".join(lines)


def _write_whole_book(book: BookRevision, plan: ExportPlan, root: Path) -> None:
    output_file = root / "book.md"
    parts = [
        _front_matter(book),
        f"# {book.metadata.title}",
        "## 目录\n\n" + "\n".join(_toc_lines(book, plan, context="book")),
    ]
    for node_id in book.reading_order_ids:
        node = book.nodes[node_id]
        parts.append(_node_heading(book, node_id, output_file, root, level=node.depth + 1))
    _write_text(output_file, "\n\n".join(parts))


def _relative_link(from_path: str, to_path: str, anchor: str | None = None) -> str:
    relative = PurePosixPath(os.path.relpath(to_path, PurePosixPath(from_path).parent)).as_posix()
    return _markdown_link(relative, anchor)


def _write_split_unit(
    book: BookRevision,
    plan: ExportPlan,
    unit: ExportUnit,
    root: Path,
    previous: ExportUnit | None,
    following: ExportUnit | None,
) -> None:
    output_file = root / unit.relative_path
    root_node = book.nodes[unit.root_node_id]
    parts = [
        (
            f"[总目录]({_relative_link(unit.relative_path, 'TOC.md')}) · "
            f"[整体版对应位置]({_relative_link(unit.relative_path, 'book.md', root_node.anchor)})"
        )
    ]
    if unit.is_index:
        parts.append(_node_heading(book, unit.root_node_id, output_file, root, level=1))
        if root_node.children_ids:
            lines = ["## 本部分目录"]
            for child_id in root_node.children_ids:
                location = plan.split_locations[child_id]
                link = _relative_link(unit.relative_path, location.path, location.anchor)
                lines.append(f"- [{_escape_label(book.nodes[child_id].title_display)}]({link})")
            parts.append("\n".join(lines))
    else:
        for node_id in unit.node_ids:
            node = book.nodes[node_id]
            level = min(1 + node.depth - root_node.depth, 6)
            parts.append(_node_heading(book, node_id, output_file, root, level=level))

    navigation: list[str] = []
    if previous:
        title = book.nodes[previous.root_node_id].title_display
        navigation.append(
            f"[← {_escape_label(title)}]({_relative_link(unit.relative_path, previous.relative_path, book.nodes[previous.root_node_id].anchor)})"
        )
    if following:
        title = book.nodes[following.root_node_id].title_display
        navigation.append(
            f"[{_escape_label(title)} →]({_relative_link(unit.relative_path, following.relative_path, book.nodes[following.root_node_id].anchor)})"
        )
    if navigation:
        parts.append("---\n\n" + " · ".join(navigation))
    _write_text(output_file, "\n\n".join(parts))


def _toc_tree(book: BookRevision, node_id: str) -> dict[str, object]:
    node = book.nodes[node_id]
    return {
        **node.to_dict(),
        "children": [_toc_tree(book, child_id) for child_id in node.children_ids],
    }


def _artifact_role(relative: str) -> str:
    if relative == "book.md":
        return "markdown_single"
    if relative == "TOC.md":
        return "human_toc"
    if relative.startswith("chapters/"):
        return "markdown_chapter"
    if relative.startswith("assets/"):
        return "asset"
    if relative == "report.json":
        return "report"
    if relative == "manifest.json":
        return "manifest"
    return "metadata"


def _validate_locations(book: BookRevision, plan: ExportPlan, root: Path) -> list[str]:
    errors: list[str] = []
    for node_id in book.walk_ids():
        for view, location in (
            ("single", plan.single_locations.get(node_id)),
            ("split", plan.split_locations.get(node_id)),
        ):
            if location is None:
                errors.append(f"{view} 缺少节点位置：{node_id}")
                continue
            target = root / location.path
            if not target.is_file():
                errors.append(f"{view} 目标文件不存在：{location.path}")
                continue
            if f'id="{location.anchor}"' not in target.read_text(encoding="utf-8"):
                errors.append(f"{view} 目标锚点不存在：{location.path}#{location.anchor}")
    for markdown_file in root.rglob("*.md"):
        content = markdown_file.read_text(encoding="utf-8")
        if re.search(r"ANYTOMD[A-Z0-9]*TOKEN", content):
            errors.append(f"内部占位符未解析：{markdown_file.relative_to(root)}")
    return errors


def write_markdown_bundle(book: BookRevision, plan: ExportPlan, root: Path) -> dict[str, object]:
    root.mkdir(parents=True, exist_ok=True)
    (root / "chapters").mkdir(exist_ok=True)
    assets_dir = root / "assets"
    assets_dir.mkdir(exist_ok=True)
    for asset in book.assets.values():
        (assets_dir / asset.file_name).write_bytes(asset.content)

    _write_whole_book(book, plan, root)
    for index, unit in enumerate(plan.units):
        _write_split_unit(
            book,
            plan,
            unit,
            root,
            plan.units[index - 1] if index > 0 else None,
            plan.units[index + 1] if index + 1 < len(plan.units) else None,
        )

    toc_content = "\n".join(
        [f"# {book.metadata.title}：目录", "", *_toc_lines(book, plan, context="toc")]
    )
    _write_text(root / "TOC.md", toc_content)
    _write_json(
        root / "toc.json",
        {
            "schema_version": 1,
            "book_id": book.book_id,
            "edition_id": book.edition_id,
            "revision_id": book.revision_id,
            "roots": [_toc_tree(book, node_id) for node_id in book.roots],
        },
    )
    _write_json(
        root / "locations.json",
        {
            "schema_version": 1,
            "export_id": plan.export_id,
            "revision_id": plan.revision_id,
            "nodes": {
                node_id: {
                    "single": vars(plan.single_locations[node_id]),
                    "split": vars(plan.split_locations[node_id]),
                    "source": book.nodes[node_id].source_documents_dict(),
                }
                for node_id in book.walk_ids()
            },
        },
    )
    _write_json(root / "book.json", book.to_dict())

    validation_errors = _validate_locations(book, plan, root)
    errors = [item for item in book.diagnostics if item.severity == "error"]
    if validation_errors:
        status = "failed"
    elif errors:
        status = "partial"
    elif book.diagnostics:
        status = "completed_with_warnings"
    else:
        status = "completed"
    report = {
        "schema_version": 1,
        "status": status,
        "book_id": book.book_id,
        "edition_id": book.edition_id,
        "revision_id": book.revision_id,
        "export_id": plan.export_id,
        "pipeline_version": book.pipeline_version,
        "split_policy": plan.split_policy,
        "summary": {
            "section_count": len(book.nodes),
            "chapter_file_count": len(plan.units),
            "asset_count": len(book.assets),
            "warning_count": sum(item.severity == "warning" for item in book.diagnostics),
            "error_count": len(errors) + len(validation_errors),
        },
        "diagnostics": [item.to_dict() for item in book.diagnostics],
        "validation_errors": validation_errors,
        "m01_limitations": [
            "当前优先支持目录有效的 EPUB；常见 *_split_NNN 物理分片会并回目录章节，其他复杂章节边界在 M02 扩展。",
            "正文标题按章节节点保存相对层级，并在整体版与分章版中映射到对应级别；异常排版仍会在 M02 继续完善。",
            "逐字拆分的相邻粗体会合并；整段加粗的短编号主题会提升为当前标题的下一层标题。",
            "使用等宽字体并带连续行号的 EPUB 代码清单会转换为围栏代码块；无法可靠识别的特殊排版会保留原文。",
            "基础图片和简单表格已处理；复杂表格、脚注和跨章原始链接在 M03 完善。",
            "显式 HTML 锚点需要 Markdown 阅读器保留原始 HTML。",
        ],
    }
    _write_json(root / "report.json", report)
    if validation_errors:
        raise ValueError("导出位置校验失败：" + "; ".join(validation_errors))

    manifest_files: list[dict[str, object]] = []
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        relative = path.relative_to(root).as_posix()
        manifest_files.append(
            {
                "role": _artifact_role(relative),
                "path": relative,
                "sha256": _hash_file(path),
                "size_bytes": path.stat().st_size,
            }
        )
    _write_json(
        root / "manifest.json",
        {
            "schema_version": 1,
            "export_id": plan.export_id,
            "revision_id": plan.revision_id,
            "primary_artifact": "book.md",
            "files": manifest_files,
        },
    )
    return report


def artifact_role(relative_path: str) -> str:
    return _artifact_role(relative_path)
