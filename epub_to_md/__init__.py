"""EPUB to Markdown conversion helpers focused on TOC leaf nodes."""

from __future__ import annotations

import hashlib
import posixpath
import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Dict, Iterable, Iterator, List, Optional, Sequence

import ebooklib
from bs4 import BeautifulSoup, NavigableString, ProcessingInstruction, Tag
from ebooklib import epub
from markdownify import markdownify as to_markdown

_SPLIT_PREFIX = "__EPUB_SPLIT__"
_SPLIT_END = "__EPUB_SPLIT_END__"


@dataclass
class Chapter:
    """A converted chapter of an EPUB book."""

    index: int
    title: str
    content: str
    file_name: str
    output_path: Optional[Path] = None
    source_path: Optional[str] = None
    source_fragment: Optional[str] = None
    anchor: Optional[str] = None


@dataclass
class TocEntry:
    """Leaf entry extracted from the EPUB table of contents."""

    index: int
    title: str
    href: str
    path: str
    fragment: Optional[str]


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


def _strip_processing_instructions(soup: BeautifulSoup) -> None:
    for node in list(
        soup.find_all(string=lambda text: isinstance(text, ProcessingInstruction))
    ):
        node.extract()


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
    stem = re.sub(r"[^0-9A-Za-z]+", "-", original.stem) or "asset"
    suffix = original.suffix or ""
    return f"{stem}-{digest}{suffix}"


def _process_images(
    book: epub.EpubBook,
    item: epub.EpubHtml,
    soup: BeautifulSoup,
    assets_dir: Optional[Path],
    exported: Dict[str, Path],
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


def _rewrite_internal_links(
    item: epub.EpubHtml,
    soup: BeautifulSoup,
    link_map: Optional[Dict[str, str]],
) -> None:
    if not link_map:
        return

    scheme_pattern = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*:")

    for anchor in soup.find_all("a"):
        href = anchor.get("href")
        if not href or href.startswith("#"):
            continue
        if scheme_pattern.match(href):
            continue
        normalized_path = _normalize_href(item.file_name, href)
        fragment = None
        if "#" in href:
            _, fragment = href.split("#", 1)
        target = None
        if fragment:
            target = link_map.get(f"{normalized_path}#{fragment}")
        if target is None:
            target = link_map.get(normalized_path)
        if target:
            anchor["href"] = f"#{target}"


def _prepare_document(
    book: epub.EpubBook,
    item: epub.EpubHtml,
    assets_dir: Optional[Path],
    exported_images: Dict[str, Path],
    link_map: Optional[Dict[str, str]] = None,
) -> BeautifulSoup:
    html = item.get_content().decode("utf-8", errors="ignore")
    soup = BeautifulSoup(html, "html.parser")
    _strip_processing_instructions(soup)
    _replace_tables_with_markdown(soup)
    _process_images(book, item, soup, assets_dir, exported_images)
    _rewrite_internal_links(item, soup, link_map)
    return soup


def _flatten_toc(nodes: Iterable) -> Iterator[epub.Link]:
    for node in nodes:
        if isinstance(node, epub.Link):
            yield node
        elif isinstance(node, epub.Section):
            yield from _flatten_toc(node.subitems)
        elif isinstance(node, tuple) and len(node) == 2:
            _, children = node
            yield from _flatten_toc(children)
        elif isinstance(node, list):
            yield from _flatten_toc(node)


def _toc_leaf_entries(book: epub.EpubBook) -> List[TocEntry]:
    entries: List[TocEntry] = []
    for idx, link in enumerate(_flatten_toc(book.toc), start=1):
        href = link.href or ""
        if not href:
            continue
        if "#" in href:
            path, fragment = href.split("#", 1)
        else:
            path, fragment = href, None
        entries.append(
            TocEntry(
                index=idx,
                title=link.title or f"Section {idx}",
                href=href,
                path=str(PurePosixPath(path)),
                fragment=fragment or None,
            )
        )
    return entries


def _group_entries_by_path(entries: List[TocEntry]) -> Dict[str, List[TocEntry]]:
    grouped: Dict[str, List[TocEntry]] = defaultdict(list)
    for entry in entries:
        grouped[entry.path].append(entry)
    return grouped


def _extract_bracket_title(title: str) -> Optional[str]:
    match = re.search(r"\[([^\]]+)\]", title)
    if match:
        candidate = match.group(1).strip()
        if candidate:
            return candidate
    return None


def _sanitize_filename(value: str) -> str:
    text = value.strip()
    text = re.sub(r"[\\/:*?\"<>|]", "_", text)
    text = re.sub(r"\s+", "_", text)
    text = text.strip("._")
    return text


def _build_chapter_filename(
    title: str,
    index: int,
    used_names: Dict[str, int],
) -> str:
    candidate = _extract_bracket_title(title) or title.strip()
    if not candidate:
        candidate = f"chapter_{index:04d}"
    sanitized = _sanitize_filename(candidate) or f"chapter_{index:04d}"
    count = used_names.get(sanitized, 0)
    used_names[sanitized] = count + 1
    suffix = "" if count == 0 else f"_{count}"
    return f"{sanitized}{suffix}.md"


def _build_anchor_id(title: str, index: int) -> str:
    base = _sanitize_filename(title).replace("_", "-")
    base = re.sub(r"-+", "-", base).strip("-")
    if not base:
        base = "chapter"
    return f"{base.lower()}-{index:04d}"


def _find_anchor_node(
    container: Tag | BeautifulSoup, fragment: Optional[str]
) -> Optional[Tag]:
    if not fragment:
        if isinstance(container, Tag) and container.contents:
            for child in container.contents:
                if isinstance(child, Tag):
                    return child
        return container if isinstance(container, Tag) else None

    target = fragment.lstrip("#")
    node = container.find(id=target)
    if node:
        return node
    node = container.find(attrs={"name": target})
    if node:
        return node
    node = container.find("a", attrs={"href": f"#{target}"})
    return node


def _split_document_into_fragments(
    soup: BeautifulSoup,
    entries: List[TocEntry],
) -> Dict[int, str]:
    container = soup.body or soup
    pristine_html = str(container)

    if len(entries) == 1 and not entries[0].fragment:
        return {entries[0].index: pristine_html}

    working = BeautifulSoup(pristine_html, "html.parser")
    body = working if not working.body else working.body

    for entry in entries:
        marker = working.new_string(f"{_SPLIT_PREFIX}{entry.index}__")
        anchor = _find_anchor_node(body, entry.fragment)
        if anchor is not None:
            anchor.insert_before(marker)
        else:
            body.append(marker)

    body.append(working.new_string(_SPLIT_END))
    html_with_tokens = str(body)

    fragments: Dict[int, str] = {}
    for i, entry in enumerate(entries):
        start_token = f"{_SPLIT_PREFIX}{entry.index}__"
        token_idx = html_with_tokens.find(start_token)
        if token_idx == -1:
            fragments[entry.index] = ""
            continue
        start_idx = token_idx + len(start_token)
        prefix = ""
        if i == 0 and token_idx > 0:
            prefix = html_with_tokens[:token_idx]
        if i + 1 < len(entries):
            end_token = f"{_SPLIT_PREFIX}{entries[i + 1].index}__"
        else:
            end_token = _SPLIT_END
        end_idx = html_with_tokens.find(end_token, start_idx)
        if end_idx == -1:
            end_idx = len(html_with_tokens)
        fragments[entry.index] = prefix + html_with_tokens[start_idx:end_idx]
    return fragments


def _html_fragment_to_markdown(fragment_html: str) -> str:
    soup = BeautifulSoup(fragment_html, "html.parser")
    markdown = to_markdown(
        str(soup),
        heading_style="ATX",
        escape_asterisks=False,
        bullets="-",
    ).strip()
    return markdown


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
        if not isinstance(item, epub.EpubHtml):
            continue
        resolved_id = item.get_id()
        if resolved_id in visited:
            continue
        visited.add(resolved_id)
        yield item


def _chapter_slug(item: epub.EpubHtml, fallback: str) -> str:
    candidates = [item.get_id() or "", Path(item.file_name).stem, fallback]
    for candidate in candidates:
        if not candidate:
            continue
        slug = re.sub(r"[^0-9A-Za-z]+", "-", candidate.strip().lower())
        slug = slug.strip("-")
        if slug:
            return slug
    return "chapter"


def _convert_by_spine(
    book: epub.EpubBook,
    *,
    include_metadata: bool,
    output_path: Optional[Path],
    assets_dir: Optional[Path],
) -> List[Chapter]:
    exported_images: Dict[str, Path] = {}
    chapters: List[Chapter] = []
    for index, item in enumerate(_iter_spine_documents(book), start=1):
        if getattr(item, "get_type", lambda: None)() == ebooklib.ITEM_NAVIGATION:
            continue
        soup = _prepare_document(
            book,
            item,
            assets_dir,
            exported_images,
        )
        html_fragment = str(soup.body or soup)
        markdown_body = _html_fragment_to_markdown(html_fragment)
        parts: List[str] = []
        item_id = item.get_id()
        if item_id in processed_item_ids:
            continue
        title = item_id or f"Chapter {index}"
        if include_metadata:
            parts.append(_metadata_block(book, title, index))
        anchor_id = _build_anchor_id(title, index)
        parts.append(f'<a id="{anchor_id}"></a>')
        parts.append(markdown_body)
        chapter_content = _clean_join(parts)
        filename = f"{index:02d}-{_chapter_slug(item, f'chapter-{index:02d}')}.md"
        chapter = Chapter(
            index=index,
            title=title,
            content=chapter_content,
            file_name=filename,
            source_path=Path(item.file_name).as_posix(),
            anchor=anchor_id,
        )
        if output_path is not None:
            destination = output_path / filename
            destination.write_text(chapter_content, encoding="utf-8")
            chapter.output_path = destination
        chapters.append(chapter)
    return chapters


def _convert_by_toc(
    book: epub.EpubBook,
    entries: List[TocEntry],
    *,
    include_metadata: bool,
    output_path: Optional[Path],
    assets_dir: Optional[Path],
) -> List[Chapter]:
    grouped = _group_entries_by_path(entries)
    exported_images: Dict[str, Path] = {}
    fragment_cache: Dict[str, Dict[int, str]] = {}
    html_cache: Dict[str, str] = {}
    item_cache: Dict[str, epub.EpubHtml] = {}
    processed_item_ids: set[str] = set()

    link_targets: Dict[str, str] = {}
    anchor_by_entry: Dict[int, str] = {}
    for entry in entries:
        anchor_id = _build_anchor_id(entry.title, entry.index)
        anchor_by_entry[entry.index] = anchor_id
        link_targets[entry.path] = anchor_id
        if entry.fragment:
            link_targets[f"{entry.path}#{entry.fragment}"] = anchor_id

    used_names: Dict[str, int] = {}
    chapters: List[Chapter] = []
    chapter_no = 1

    for entry in entries:
        if entry.path not in fragment_cache:
            item = book.get_item_with_href(entry.path)
            if not isinstance(item, epub.EpubHtml):
                fragment_cache[entry.path] = {}
                continue
            if getattr(item, "get_type", lambda: None)() == ebooklib.ITEM_NAVIGATION:
                fragment_cache[entry.path] = {}
                continue
            processed_item_ids.add(item.get_id())
            soup = _prepare_document(
                book,
                item,
                assets_dir,
                exported_images,
                link_targets,
            )
            html_cache[entry.path] = str(soup.body or soup)
            fragments = _split_document_into_fragments(soup, grouped[entry.path])
            fragment_cache[entry.path] = fragments
            item_cache[entry.path] = item

        item = item_cache.get(entry.path)
        if not isinstance(item, epub.EpubHtml):
            continue

        fragment_html = fragment_cache.get(entry.path, {}).get(entry.index)
        if not fragment_html:
            fragment_html = html_cache.get(entry.path, "")
        markdown_body = _html_fragment_to_markdown(fragment_html)

        parts: List[str] = []
        if include_metadata:
            parts.append(_metadata_block(book, entry.title, chapter_no))
        anchor_token = anchor_by_entry.get(entry.index)
        if anchor_token:
            parts.append(f'<a id="{anchor_token}"></a>')
        parts.append(markdown_body)
        chapter_content = _clean_join(parts)

        filename = _build_chapter_filename(entry.title, chapter_no, used_names)
        chapter = Chapter(
            index=chapter_no,
            title=entry.title,
            content=chapter_content,
            file_name=filename,
            source_path=entry.path,
            source_fragment=entry.fragment,
            anchor=anchor_by_entry.get(entry.index),
        )

        if output_path is not None:
            destination = output_path / filename
            destination.write_text(chapter_content, encoding="utf-8")
            chapter.output_path = destination

        chapters.append(chapter)
        chapter_no += 1

    for item in _iter_spine_documents(book):
        item_id = item.get_id()
        if item_id in processed_item_ids:
            continue
        if getattr(item, "get_type", lambda: None)() == ebooklib.ITEM_NAVIGATION:
            continue
        title = item_id or Path(item.file_name).stem or f"Chapter {chapter_no}"
        anchor_id = _build_anchor_id(title, chapter_no)
        normalized_item_path = posixpath.normpath(str(PurePosixPath(item.file_name)))
        link_targets[normalized_item_path] = anchor_id
        soup = _prepare_document(
            book,
            item,
            assets_dir,
            exported_images,
            link_targets,
        )
        html_fragment = str(soup.body or soup)
        markdown_body = _html_fragment_to_markdown(html_fragment)
        parts: List[str] = []
        if include_metadata:
            parts.append(_metadata_block(book, title, chapter_no))
        parts.append(f'<a id="{anchor_id}"></a>')
        parts.append(markdown_body)
        chapter_content = _clean_join(parts)
        filename = _build_chapter_filename(title, chapter_no, used_names)
        chapter = Chapter(
            index=chapter_no,
            title=title,
            content=chapter_content,
            file_name=filename,
            source_path=Path(item.file_name).as_posix(),
            anchor=anchor_id,
        )
        if output_path is not None:
            destination = output_path / filename
            destination.write_text(chapter_content, encoding="utf-8")
            chapter.output_path = destination
        chapters.append(chapter)
        chapter_no += 1

    return chapters


def convert_epub_to_markdown(
    source: str | Path,
    *,
    include_metadata: bool = True,
    output_dir: str | Path | None = None,
    assets_dir: str | Path | None = None,
) -> List[Chapter]:
    source_path = Path(source)
    if not source_path.is_file():
        raise FileNotFoundError(f"EPUB file not found: {source_path}")

    book = epub.read_epub(str(source_path))

    output_path: Optional[Path] = None
    if output_dir is not None:
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)

    assets_path: Optional[Path]
    if assets_dir is not None:
        assets_path = Path(assets_dir)
    elif output_path is not None:
        assets_path = output_path / "assets"
    else:
        assets_path = None

    if assets_path is not None:
        assets_path.mkdir(parents=True, exist_ok=True)

    toc_entries = _toc_leaf_entries(book)
    if toc_entries:
        return _convert_by_toc(
            book,
            toc_entries,
            include_metadata=include_metadata,
            output_path=output_path,
            assets_dir=assets_path,
        )

    return _convert_by_spine(
        book,
        include_metadata=include_metadata,
        output_path=output_path,
        assets_dir=assets_path,
    )


def convert_epub_to_single_markdown(
    source: str | Path,
    *,
    include_metadata: bool = True,
    output_file: str | Path | None = None,
    assets_dir: str | Path | None = None,
    chapter_separator: str = "\n\n---\n\n",
) -> str:
    chapters = convert_epub_to_markdown(
        source,
        include_metadata=include_metadata,
        output_dir=None,
        assets_dir=assets_dir,
    )
    combined = chapter_separator.join(
        chapter.content.strip()
        for chapter in chapters
        if chapter.content and chapter.content.strip()
    )
    if output_file is not None:
        destination = Path(output_file)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(combined, encoding="utf-8")
    return combined


__all__: Sequence[str] = [
    "Chapter",
    "convert_epub_to_markdown",
    "convert_epub_to_single_markdown",
]
