"""Render whole-book and split Markdown views from one export plan."""

from __future__ import annotations

import json
import os
from pathlib import Path, PurePosixPath
from urllib.parse import quote

from ..domain.artifacts import ExportPlan, ExportUnit
from ..domain.book import BookRevision
from ..content.dom import iter_flow
from .rendering import RenderContext
from .serialization import to_markdown


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content.rstrip() + "\n", encoding="utf-8")


def _escape_label(value: str) -> str:
    return value.replace("[", "\\[").replace("]", "\\]")


def _markdown_link(path: str, anchor: str | None = None) -> str:
    encoded = quote(path, safe="/._-")
    return f"{encoded}#{anchor}" if anchor else encoded


def _render_flow(book: BookRevision, plan: ExportPlan, output_path: str, *, node_ids=None, base_depth: int = 0, view: str = "single") -> str:
    context = RenderContext(book, plan)
    parts = []
    for kind, item in iter_flow(book, set(node_ids) if node_ids is not None else None):
        if kind == "heading":
            level = min(item.depth + 1 - base_depth, 6)
            parts.append(f'<a id="{item.anchor}"></a>\n\n' + "#" * level + " " + item.title_display)
        else:
            level = book.nodes[item.node_id].depth + 1 - base_depth
            parts.append(to_markdown(context.fragment(item, output_path, view, level)))
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
        lines.append(f"{'    ' * (depth - base_depth)}- {_escape_label(node.title_display)} — {target}")
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
    parts.append(_render_flow(book, plan, "book.md"))
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
    parts.append(_render_flow(book, plan, unit.relative_path, node_ids=unit.node_ids, base_depth=root_node.depth, view="split"))
    if unit.is_index and root_node.children_ids:
        lines = ["## 本部分目录"]
        for child_id in root_node.children_ids:
            location = plan.split_locations[child_id]
            link = _relative_link(unit.relative_path, location.path, location.anchor)
            lines.append(f"- [{_escape_label(book.nodes[child_id].title_display)}]({link})")
        parts.append("\n".join(lines))

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


def write_markdown(book: BookRevision, plan: ExportPlan, root: Path) -> None:
    (root / "chapters").mkdir(parents=True, exist_ok=True)
    _write_whole_book(book, plan, root)
    for index, unit in enumerate(plan.units):
        _write_split_unit(book, plan, unit, root, plan.units[index - 1] if index else None, plan.units[index + 1] if index + 1 < len(plan.units) else None)
    _write_text(root / "TOC.md", "\n".join([f"# {book.metadata.title}：目录", "", *_toc_lines(book, plan, context="toc")]))
