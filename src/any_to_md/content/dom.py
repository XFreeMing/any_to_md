"""Balanced DOM partitioning without converting the source to Markdown."""

from __future__ import annotations

import copy
import re
from collections.abc import Iterator

from bs4 import BeautifulSoup, Comment, NavigableString, Tag

_VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}


def can_replace_with_text(tag: Tag) -> bool:
    """Text cleanup must not discard media, references, tables or math semantics."""
    return not (
        tag.find(["img", "picture", "svg", "math", "video", "audio", "canvas", "object", "embed", "table", "sup", "sub", "input", "select", "textarea", "button"])
        or tag.find("a", href=True)
    )


def _opening(tag: Tag, *, clone: bool = False) -> str:
    shell = copy.copy(tag)
    shell.clear()
    if clone:
        for attribute in ("id", "name"):
            shell.attrs.pop(attribute, None)
    rendered = str(shell)
    return rendered if tag.name in _VOID else rendered[:rendered.index(">") + 1]


def partition(container: Tag | BeautifulSoup, boundaries: dict[int, str], default_owner: str, *, split_boundaries: bool = False, keep_empty_boundaries: bool = False) -> list[tuple[str, str]]:
    """Split before boundary tags, closing/reopening ancestors without duplicate IDs."""
    owner = default_owner
    stack: list[Tag] = []
    pieces: list[str] = []
    result: list[tuple[str, str]] = []
    boundary_owners = set(boundaries.values())

    def flush() -> None:
        if not pieces:
            return
        html = "".join([*pieces, *(f"</{tag.name}>" for tag in reversed(stack))])
        soup = BeautifulSoup(html, "html.parser")
        if soup.get_text(strip=True) or soup.find(["img", "svg", "hr", "a", "video", "audio"]) or (keep_empty_boundaries and owner in boundary_owners):
            result.append((owner, html))

    def visit(element: Tag | NavigableString) -> None:
        nonlocal owner, pieces
        if isinstance(element, Comment):
            return
        if isinstance(element, NavigableString):
            pieces.append(element.output_ready(formatter="minimal"))
            return
        next_owner = boundaries.get(id(element))
        if next_owner is not None and (next_owner != owner or split_boundaries):
            flush()
            owner = next_owner
            pieces = [_opening(tag, clone=True) for tag in stack]
        pieces.append(_opening(element))
        if element.name not in _VOID:
            stack.append(element)
            for child in element.children:
                visit(child)
            stack.pop()
            pieces.append(f"</{element.name}>")

    for child in container.children:
        visit(child)
    flush()
    return result


def strip_title(html: str, title: str) -> str:
    """Remove only a leading source title that the generated node heading replaces."""
    soup = BeautifulSoup(html, "html.parser")
    normalized = lambda value: re.sub(r"\s+", "", value).casefold()
    target = normalized(title)
    combined = ""
    candidates = []
    for heading in soup.find_all(re.compile(r"^h[1-6]$"))[:4]:
        if not can_replace_with_text(heading):
            # A generated text heading cannot replace source media or link semantics.
            break
        before = []
        for item in soup.descendants:
            if item is heading:
                break
            if isinstance(item, NavigableString) and item.find_parent(re.compile(r"^h[1-6]$")) not in candidates:
                before.append(str(item))
        if "".join(before).strip():
            break
        candidate = combined + normalized(heading.get_text())
        if not target.startswith(candidate):
            break
        combined = candidate
        candidates.append(heading)
        if combined == target:
            for item in candidates:
                # Preserve source anchors and their aliases while removing title text.
                anchors = [tag for tag in [item, *item.find_all(True)] if tag.get("id")]
                for tag in anchors:
                    anchor = soup.new_tag("a", id=tag["id"])
                    item.insert_before(anchor)
                item.decompose()
            break
    return str(soup)


def iter_flow(book, selected: set[str] | None = None) -> Iterator[tuple[str, object]]:
    """Emit each node heading once while keeping fragment source order intact."""
    selected = selected if selected is not None else set(book.nodes)
    emitted: set[str] = set()

    def headings(node_id: str):
        parent = book.nodes[node_id].parent_id
        if parent is not None:
            yield from headings(parent)
        if node_id in selected and node_id not in emitted:
            emitted.add(node_id)
            yield "heading", book.nodes[node_id]

    for fragment in book.content:
        if fragment.node_id in selected:
            if fragment.heading_before:
                yield from headings(fragment.node_id)
            yield "content", fragment
    for node_id in book.walk_ids():
        if node_id in selected:
            yield from headings(node_id)
