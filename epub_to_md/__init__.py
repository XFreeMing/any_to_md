"""EPUB to Markdown conversion helpers."""

from __future__ import annotations

import hashlib
import posixpath
import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Iterable, Iterator, List, Optional, Sequence

import ebooklib
from bs4 import BeautifulSoup, NavigableString, ProcessingInstruction, Tag
from ebooklib import epub
from markdownify import markdownify as to_markdown


@dataclass
class Chapter:
    """A converted chapter of an EPUB book."""

    index: int
    title: str
    content: str
    file_name: str
    output_path: Optional[Path] = None


def _clean_join(chunks: Iterable[str]) -> str:
    filtered = [chunk.strip() for chunk in chunks if chunk and chunk.strip()]
    return "\n\n".join(filtered)


def _first_metadata(book: epub.EpubBook, field: str) -> Optional[str]:
    values = book.get_metadata("DC", field)
    if not values:
        return None
    candidate = values[0]
    if isinstance(candidate, tuple):
        return candidate[0]
    return candidate


def _yaml_escape(value: str) -> str:
    if not value:
        return ""
    if re.search(r"[\s:]", value):
        escaped = value.replace('"', '\\"')
        return f'"{escaped}"'
    return value


def _metadata_block(book: epub.EpubBook, chapter_title: str, index: int) -> str:
    lines: List[str] = ["---"]

    title = _first_metadata(book, "title")
    if title:
        lines.append(f"book_title: {_yaml_escape(title)}")

    authors = book.get_metadata("DC", "creator")
    if authors:
        names = ", ".join(author[0] for author in authors if author and author[0])
        if names:
            lines.append(f"authors: {_yaml_escape(names)}")

    language = _first_metadata(book, "language")
    if language:
        lines.append(f"language: {_yaml_escape(language)}")

    lines.append(f"chapter: {index}")
    lines.append(f"chapter_title: {_yaml_escape(chapter_title)}")
    lines.append("---")
    return "\n".join(lines)


def _slugify(value: str) -> str:
    value = value.strip().lower()
    value = re.sub(r"[\s_]+", "-", value)
    value = re.sub(r"[^a-z0-9\-]", "", value)
    return value or "chapter"


def _chapter_slug(item: epub.EpubHtml, chapter_title: str) -> str:
    candidates: List[str] = []
    item_id = item.get_id() or ""
    if item_id:
        if item_id.startswith("chapter"):
            candidates.append(item_id)
        else:
            candidates.append(f"chapter-{item_id}")
            candidates.append(item_id)
    file_stem = Path(item.file_name).stem
    if file_stem:
        candidates.append(file_stem)
    candidates.append(chapter_title)

    for candidate in candidates:
        slug = _slugify(candidate)
        if slug:
            return slug
    return "chapter"


def _normalize_href(base: str, href: str) -> str:
    href = href.split("#", 1)[0]
    if not href:
        return href
    base_dir = PurePosixPath(base).parent
    combined = base_dir.joinpath(PurePosixPath(href))
    return posixpath.normpath(str(combined))


def _hash_name(href: str) -> str:
    digest = hashlib.sha1(href.encode("utf-8")).hexdigest()[:8]
    original = PurePosixPath(href)
    stem = re.sub(r"[^a-zA-Z0-9]+", "-", original.stem) or "asset"
    suffix = original.suffix or ""
    return f"{stem}-{digest}{suffix}"


def _table_to_markdown(table: Tag) -> str:
    rows: List[tuple[List[str], bool]] = []
    for tr in table.find_all("tr"):
        cells: List[str] = []
        is_header = False
        for cell in tr.find_all(["th", "td"]):
            text = cell.get_text(separator=" ", strip=True)
            text = text.replace("|", "\\|")
            cells.append(text)
            if cell.name == "th":
                is_header = True
        if cells:
            rows.append((cells, is_header))

    if not rows:
        return ""

    max_cols = max(len(cells) for cells, _ in rows)
    padded_rows = []
    for cells, is_header in rows:
        if len(cells) < max_cols:
            cells = cells + [""] * (max_cols - len(cells))
        padded_rows.append((cells, is_header))

    header_cells = padded_rows[0][0]
    if not padded_rows[0][1]:
        header_cells = [" " for _ in range(max_cols)]
        body_rows = [cells for cells, _ in padded_rows]
    else:
        body_rows = [cells for cells, _ in padded_rows[1:]]

    header_line = "| " + " | ".join(header_cells) + " |"
    separator_line = "| " + " | ".join(["---"] * max_cols) + " |"
    body_lines = ["| " + " | ".join(row) + " |" for row in body_rows]
    return "\n".join([header_line, separator_line, *body_lines])


def _replace_tables_with_markdown(soup: BeautifulSoup) -> None:
    for table in soup.find_all("table"):
        markdown = _table_to_markdown(table)
        replacement = NavigableString(f"\n{markdown}\n")
        table.replace_with(replacement)


def _strip_processing_instructions(soup: BeautifulSoup) -> None:
    for node in list(
        soup.find_all(string=lambda text: isinstance(text, ProcessingInstruction))
    ):
        node.extract()


def _flatten_toc(toc: Sequence) -> List[epub.Link]:
    def _walk(nodes: Sequence) -> Iterator[epub.Link]:
        for node in nodes:
            if isinstance(node, epub.Link):
                yield node
            elif isinstance(node, epub.Section):
                yield from _walk(node.subitems)
            elif isinstance(node, tuple):
                _, children = node
                yield from _walk(children)
            elif isinstance(node, list):
                yield from _walk(node)

    return list(_walk(toc))


def _group_links_by_base(links: List[epub.Link]) -> dict[str, List[epub.Link]]:
    grouped: dict[str, List[epub.Link]] = defaultdict(list)
    for link in links:
        base, *_fragment = link.href.split("#", 1)
        grouped[base].append(link)
    return grouped


def _anchor_parent(node: Tag) -> Tag:
    if node.name == "a" and node.parent is not None:
        return node.parent
    return node


def _find_anchor_node(
    soup: BeautifulSoup,
    fragment: Optional[str],
    title: str,
) -> Tag:
    body = soup.body or soup
    if fragment:
        anchor = soup.find(id=fragment) or soup.find(attrs={"name": fragment})
        if anchor:
            return _anchor_parent(anchor)
        lowered = fragment.lower()
        if lowered.startswith("filepos"):
            anchor = soup.find(id=lowered)
            if anchor:
                return _anchor_parent(anchor)

    title_text = title.strip()
    if title_text:
        heading = soup.find(
            lambda tag: isinstance(tag, Tag)
            and tag.name in {"h1", "h2", "h3", "h4", "h5", "h6"}
            and tag.get_text(strip=True) == title_text
        )
        if heading:
            return heading

    return body


def _iterate_nodes(start: Tag | NavigableString) -> Iterator[Tag | NavigableString]:
    yield start
    yield from start.next_elements


def _html_fragment_between(start: Tag, stop: Optional[Tag]) -> str:
    fragments: List[str] = []
    for node in _iterate_nodes(start):
        if node is stop:
            break
        fragments.append(str(node))
    return "".join(fragments)


def _iter_spine_documents(book: epub.EpubBook) -> Iterator[epub.EpubHtml]:
    visited: set[str] = set()
    for entry in book.spine:
        item_id = entry[0] if isinstance(entry, tuple) else entry
        if not item_id:
            continue
        item = book.get_item_with_id(item_id)
        if not isinstance(item, epub.EpubHtml):
            continue
        if isinstance(item, epub.EpubNav):
            continue
        resolved_id = item.get_id()
        if resolved_id in visited:
            continue
        visited.add(resolved_id)
        yield item

    for item in book.get_items_of_type(ebooklib.ITEM_DOCUMENT):
        if isinstance(item, epub.EpubNav):
            continue
        resolved_id = item.get_id()
        if resolved_id in visited:
            continue
        visited.add(resolved_id)
        yield item


def _process_images(
    book: epub.EpubBook,
    item: epub.EpubHtml,
    soup: BeautifulSoup,
    assets_dir: Optional[Path],
    exported: dict[str, Path],
) -> None:
    if assets_dir is None:
        return

    assets_dir.mkdir(parents=True, exist_ok=True)
    image_map = {
        posixpath.normpath(str(PurePosixPath(img.file_name))): img
        for img in book.get_items_of_type(ebooklib.ITEM_IMAGE)
    }

    for img_tag in soup.find_all("img"):
        src = img_tag.get("src")
        if not src:
            continue
        normalized = _normalize_href(item.file_name, src)
        if not normalized:
            continue
        image_item = image_map.get(normalized)
        if not image_item:
            continue
        if normalized in exported:
            relative_path = exported[normalized]
        else:
            dest_name = _hash_name(normalized)
            relative_path = Path("assets") / dest_name
            destination = assets_dir / dest_name
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(image_item.get_content())
            exported[normalized] = relative_path
        img_tag["src"] = relative_path.as_posix()


def _sanitize_filename(title: str) -> str:
    cleaned = title.strip().replace("/", "-").replace("\\", "-")
    cleaned = cleaned or "chapter"
    return cleaned


def _prepare_document(
    book: epub.EpubBook,
    item: epub.EpubHtml,
    assets_dir: Optional[Path],
    exported_images: dict[str, Path],
) -> BeautifulSoup:
    html = item.get_content().decode("utf-8", errors="ignore")
    soup = BeautifulSoup(html, "html.parser")
    _strip_processing_instructions(soup)
    _replace_tables_with_markdown(soup)
    _process_images(book, item, soup, assets_dir, exported_images)
    return soup


def _html_to_markdown(html: str) -> str:
    wrapped = f"<div>{html}</div>"
    return to_markdown(
        wrapped,
        heading_style="ATX",
        escape_asterisks=False,
        bullets="-",
    ).strip()


def convert_epub_to_markdown(
    source: str | Path,
    *,
    include_metadata: bool = True,
    output_dir: str | Path | None = None,
) -> List[Chapter]:
    source_path = Path(source)
    if not source_path.is_file():
        raise FileNotFoundError(f"EPUB file not found: {source_path}")

    book = epub.read_epub(str(source_path))

    output_path: Optional[Path] = None
    assets_dir: Optional[Path] = None
    if output_dir is not None:
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)
        assets_dir = output_path / "assets"

    exported_images: dict[str, Path] = {}
    chapters: List[Chapter] = []

    toc_links = _flatten_toc(book.toc)
    grouped_links = _group_links_by_base(toc_links)
    processed_item_ids: set[str] = set()

    def _emit_chapter(idx: int, title: str, body_html: str, filename: str) -> Chapter:
        markdown_body = _html_to_markdown(body_html)
        parts: List[str] = []
        if include_metadata:
            parts.append(_metadata_block(book, title, idx))
        parts.append(markdown_body)
        chapter_content = _clean_join(parts)
        chapter = Chapter(
            index=idx,
            title=title,
            content=chapter_content,
            file_name=filename,
        )
        if output_path is not None:
            destination = output_path / filename
            destination.write_text(chapter_content, encoding="utf-8")
            chapter.output_path = destination
        chapters.append(chapter)
        return chapter

    chapter_index = 1

    for base_href, links in grouped_links.items():
        item = book.get_item_with_href(base_href)
        if not isinstance(item, epub.EpubHtml):
            continue
        processed_item_ids.add(item.get_id())
        soup = _prepare_document(book, item, assets_dir, exported_images)

        start_nodes: List[Tag] = []
        metadata: List[tuple[str, Optional[str]]] = []
        for link in links:
            fragment = link.href.split("#", 1)[1] if "#" in link.href else None
            start_nodes.append(
                _find_anchor_node(soup, fragment, link.title or "Untitled")
            )
            metadata.append((link.title or "Untitled", fragment))

        for idx, (start_node, meta) in enumerate(zip(start_nodes, metadata)):
            title, fragment = meta
            stop_node = start_nodes[idx + 1] if idx + 1 < len(start_nodes) else None
            html_fragment = _html_fragment_between(start_node, stop_node)
            safe_title = _sanitize_filename(title)
            filename = f"{chapter_index:02d}-{safe_title}.md"
            _emit_chapter(chapter_index, title, html_fragment, filename)
            chapter_index += 1

    if not chapters:
        for item in _iter_spine_documents(book):
            soup = _prepare_document(book, item, assets_dir, exported_images)
            html_fragment = str(soup.body or soup)
            slug = _chapter_slug(item, item.get_id() or "chapter")
            filename = f"{chapter_index:02d}-{slug}.md"
            title = item.get_id() or Path(item.file_name).stem
            _emit_chapter(chapter_index, title, html_fragment, filename)
            chapter_index += 1
    else:
        for item in _iter_spine_documents(book):
            if item.get_id() in processed_item_ids:
                continue
            soup = _prepare_document(book, item, assets_dir, exported_images)
            html_fragment = str(soup.body or soup)
            slug = _chapter_slug(item, item.get_id() or "chapter")
            filename = f"{chapter_index:02d}-{slug}.md"
            title = item.get_id() or Path(item.file_name).stem
            _emit_chapter(chapter_index, title, html_fragment, filename)
            chapter_index += 1

    return chapters


__all__: Sequence[str] = ["Chapter", "convert_epub_to_markdown"]
    if not href:
        return href
    base_dir = PurePosixPath(base).parent
    combined = base_dir.joinpath(PurePosixPath(href))
    return posixpath.normpath(str(combined))


def _hash_name(href: str) -> str:
    digest = hashlib.sha1(href.encode("utf-8")).hexdigest()[:8]
    original = PurePosixPath(href)
    stem = re.sub(r"[^a-zA-Z0-9]+", "-", original.stem) or "asset"
    suffix = original.suffix or ""
    return f"{stem}-{digest}{suffix}"


def _table_to_markdown(table) -> str:
    rows: List[tuple[List[str], bool]] = []
    for tr in table.find_all("tr"):
        cells: List[str] = []
        is_header = False
        for cell in tr.find_all(["th", "td"]):
            text = cell.get_text(separator=" ", strip=True)
            text = text.replace("|", "\\|")
            cells.append(text)
            if cell.name == "th":
                is_header = True
        if cells:
            rows.append((cells, is_header))

    if not rows:
        return ""

    max_cols = max(len(cells) for cells, _ in rows)
    padded_rows = []
    for cells, is_header in rows:
        if len(cells) < max_cols:
            cells = cells + [""] * (max_cols - len(cells))
        padded_rows.append((cells, is_header))

    header_cells = padded_rows[0][0]
    if not padded_rows[0][1]:
        header_cells = [" " for _ in range(max_cols)]
        body_rows = [cells for cells, _ in padded_rows]
    else:
        body_rows = [cells for cells, _ in padded_rows[1:]]

    header_line = "| " + " | ".join(header_cells) + " |"
    separator_line = "| " + " | ".join(["---"] * max_cols) + " |"
    body_lines = ["| " + " | ".join(row) + " |" for row in body_rows]
    return "\n".join([header_line, separator_line, *body_lines])


def _replace_tables_with_markdown(soup: BeautifulSoup) -> None:
    for table in soup.find_all("table"):
        markdown = _table_to_markdown(table)
        replacement = NavigableString(f"\n{markdown}\n")
        table.replace_with(replacement)


def _strip_processing_instructions(soup: BeautifulSoup) -> None:
    for node in list(
        soup.find_all(string=lambda text: isinstance(text, ProcessingInstruction))
    ):
        node.extract()


def _flatten_toc(toc: Sequence) -> List[epub.Link]:
    def _walk(nodes: Sequence) -> Iterator[epub.Link]:
        for node in nodes:
            if isinstance(node, epub.Link):
                yield node
            elif isinstance(node, epub.Section):
                yield from _walk(node.subitems)
            elif isinstance(node, tuple):
                _, children = node
                yield from _walk(children)
            elif isinstance(node, list):
                yield from _walk(node)

    return list(_walk(toc))


def _group_links_by_base(links: List[epub.Link]) -> dict[str, List[epub.Link]]:
    grouped: dict[str, List[epub.Link]] = defaultdict(list)
    for link in links:
        base, *_fragment = link.href.split("#", 1)
        grouped[base].append(link)
    return grouped


def _anchor_parent(node):
    if node.name == "a" and node.parent is not None:
        return node.parent
    return node


def _find_anchor_node(
    soup: BeautifulSoup,
    fragment: Optional[str],
    title: str,
):
    body = soup.body or soup
    if fragment:
        anchor = soup.find(id=fragment) or soup.find(attrs={"name": fragment})
        if anchor:
            return _anchor_parent(anchor)
        if fragment.lower().startswith("filepos"):
            anchor = soup.find(id=fragment.lower())
            if anchor:
                return _anchor_parent(anchor)

    title_text = title.strip()
    if title_text:
        heading = soup.find(
            lambda tag: (
                tag.name in {"h1", "h2", "h3", "h4", "h5", "h6"}
                and tag.get_text(strip=True) == title_text
                if hasattr(tag, "get_text")
                else False
            )
        )
        if heading:
            return heading

    return body


def _iterate_nodes(start: BeautifulSoup) -> Iterator:
    yield start
    yield from start.next_elements


def _html_fragment_between(start, stop) -> str:
    fragments: List[str] = []
    for node in _iterate_nodes(start):
        if node is stop:
            break
        fragments.append(str(node))
    return "".join(fragments)


def _iter_spine_documents(book: epub.EpubBook) -> Iterator[epub.EpubHtml]:
    visited: set[str] = set()
    for entry in book.spine:
        item_id = entry[0] if isinstance(entry, tuple) else entry
        if not item_id:
            continue
        item = book.get_item_with_id(item_id)
        if not isinstance(item, epub.EpubHtml):
            continue
        resolved_id = item.get_id()
        if resolved_id in visited:
            continue
        visited.add(resolved_id)
        yield item

    for item in book.get_items_of_type(ebooklib.ITEM_DOCUMENT):
        if isinstance(item, epub.EpubNav):
            continue
        resolved_id = item.get_id()
        if resolved_id in visited:
            continue
        visited.add(resolved_id)
        yield item


def _process_images(
    book: epub.EpubBook,
    item: epub.EpubHtml,
    soup: BeautifulSoup,
    assets_dir: Optional[Path],
    exported: dict[str, Path],
) -> None:
    if assets_dir is None:
        return

    assets_dir.mkdir(parents=True, exist_ok=True)
    image_map = {
        posixpath.normpath(str(PurePosixPath(img.file_name))): img
        for img in book.get_items_of_type(ebooklib.ITEM_IMAGE)
    }

    for img_tag in soup.find_all("img"):
        src = img_tag.get("src")
        if not src:
            continue
        normalized = _normalize_href(item.file_name, src)
        if not normalized:
            continue
        image_item = image_map.get(normalized)
        if not image_item:
            continue
        if normalized in exported:
            relative_path = exported[normalized]
        else:
            dest_name = _hash_name(normalized)
            relative_path = Path("assets") / dest_name
            destination = assets_dir / dest_name
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(image_item.get_content())
            exported[normalized] = relative_path
        img_tag["src"] = relative_path.as_posix()


def _replace_tables_images_and_clean(
    book: epub.EpubBook,
    item: epub.EpubHtml,
    soup: BeautifulSoup,
    assets_dir: Optional[Path],
    exported_images: dict[str, Path],
) -> None:
    _strip_processing_instructions(soup)
    _replace_tables_with_markdown(soup)
    _process_images(book, item, soup, assets_dir, exported_images)


def _html_to_markdown(html: str) -> str:
    wrapped = f"<div>{html}</div>"
    return to_markdown(
        wrapped,
        heading_style="ATX",
        escape_asterisks=False,
        bullets="-",
    ).strip()


def convert_epub_to_markdown(
    source: str | Path,
    *,
    include_metadata: bool = True,
    output_dir: str | Path | None = None,
) -> List[Chapter]:
    source_path = Path(source)
    if not source_path.is_file():
        raise FileNotFoundError(f"EPUB file not found: {source_path}")

    book = epub.read_epub(str(source_path))

    output_path: Optional[Path] = None
    assets_dir: Optional[Path] = None
    if output_dir is not None:
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)
        assets_dir = output_path / "assets"

    exported_images: dict[str, Path] = {}
    chapters: List[Chapter] = []

    toc_links = _flatten_toc(book.toc)
    grouped_links = _group_links_by_base(toc_links)
    processed_item_ids: set[str] = set()

    def _emit_chapter(idx: int, title: str, body_html: str, filename: str) -> Chapter:
        markdown_body = _html_to_markdown(body_html)
        parts: List[str] = []
        if include_metadata:
            parts.append(_metadata_block(book, title, idx))
        parts.append(markdown_body)
        chapter_content = _clean_join(parts)
        chapter = Chapter(
            index=idx,
            title=title,
            content=chapter_content,
            file_name=filename,
        )
        if output_path is not None:
            destination = output_path / filename
            destination.write_text(chapter_content, encoding="utf-8")
            chapter.output_path = destination
        chapters.append(chapter)
        return chapter

    chapter_index = 1

    for base_href, links in grouped_links.items():
        item = book.get_item_with_href(base_href)
        if not isinstance(item, epub.EpubHtml):
            continue
        processed_item_ids.add(item.get_id())
        html = item.get_content().decode("utf-8", errors="ignore")
        soup = BeautifulSoup(html, "html.parser")
        _replace_tables_images_and_clean(book, item, soup, assets_dir, exported_images)

        start_nodes = []
        for link in links:
            fragment = link.href.split("#", 1)[1] if "#" in link.href else None
            start_nodes.append(
                _find_anchor_node(soup, fragment, link.title or "Untitled")
            )

        for idx, (link, start_node) in enumerate(zip(links, start_nodes)):
            stop_node = start_nodes[idx + 1] if idx + 1 < len(start_nodes) else None
            html_fragment = _html_fragment_between(start_node, stop_node)
            filename = f"{chapter_index:02d}-{_slugify(link.title or link.href)}.md"
            _emit_chapter(
                chapter_index, link.title or "Untitled", html_fragment, filename
            )
            chapter_index += 1

    if not chapters:
        for item in _iter_spine_documents(book):
            html = item.get_content().decode("utf-8", errors="ignore")
            soup = BeautifulSoup(html, "html.parser")
            _replace_tables_images_and_clean(
                book, item, soup, assets_dir, exported_images
            )
            html_fragment = str(soup.body or soup)
            filename = (
                f"{chapter_index:02d}-{_chapter_slug(item, item.get_id() or '')}.md"
            )
            _emit_chapter(
                chapter_index, item.get_id() or "Untitled", html_fragment, filename
            )
            chapter_index += 1
    else:
        for item in _iter_spine_documents(book):
            if item.get_id() in processed_item_ids:
                continue
            html = item.get_content().decode("utf-8", errors="ignore")
            soup = BeautifulSoup(html, "html.parser")
            _replace_tables_images_and_clean(
                book, item, soup, assets_dir, exported_images
            )
            html_fragment = str(soup.body or soup)
            filename = (
                f"{chapter_index:02d}-{_chapter_slug(item, item.get_id() or '')}.md"
            )
            _emit_chapter(
                chapter_index, item.get_id() or "Untitled", html_fragment, filename
            )
            chapter_index += 1

    return chapters


__all__: Sequence[str] = ["Chapter", "convert_epub_to_markdown"]
