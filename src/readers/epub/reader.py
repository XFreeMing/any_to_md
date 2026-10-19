"""Read an EPUB into the book-oriented intermediate representation."""

from __future__ import annotations

import json
import posixpath
import re
import unicodedata
import uuid
from collections import defaultdict
from pathlib import Path, PurePosixPath
from typing import Any, Iterable
from urllib.parse import unquote, urlsplit
from zipfile import ZipFile

import ebooklib
from bs4 import BeautifulSoup, Comment, NavigableString, ProcessingInstruction, Tag
from ebooklib import epub

from ...content.dom import can_replace_with_text, partition, strip_title
from ...content.references import source_anchor
from ...content.document import DocumentStyleSources, document_styles, prepare_html_document, scoped_fragment
from ...content.typography import FontEvidence, author_font_evidence
from ...content.media import ResourceState
from ...domain.book import (
    ContentFragment,
    AssetRef,
    BookMetadata,
    BookRevision,
    Diagnostic,
    SourceLocator,
    StructureNode,
)

PIPELINE_VERSION = "epub-dom-v3"
_SOURCE_ANCHORS_ATTRIBUTE = "data-any-to-md-source-anchors"
_SPLIT_DOCUMENT_RE = re.compile(
    r"^(?P<family>.+)_split_(?P<part>\d+)(?P<suffix>\.[^./]+)$",
    re.IGNORECASE,
)


def _metadata_value(book: epub.EpubBook, field: str) -> str | None:
    values = book.get_metadata("DC", field)
    if not values:
        return None
    value = values[0]
    return str(value[0] if isinstance(value, tuple) else value).strip() or None


def _authors(book: epub.EpubBook) -> tuple[str, ...]:
    return tuple(
        str(value[0] if isinstance(value, tuple) else value).strip()
        for value in book.get_metadata("DC", "creator")
        if value and str(value[0] if isinstance(value, tuple) else value).strip()
    )


def _normalize_package_path(path: str) -> str:
    value = unquote(urlsplit(path).path).lstrip("/")
    return posixpath.normpath(value) if value else ""


def _resolve_package_path(document_path: str, target: str) -> str:
    parsed = urlsplit(target)
    if parsed.scheme or parsed.netloc or not parsed.path:
        return ""
    base = PurePosixPath(document_path).parent
    return _normalize_package_path(str(base / PurePosixPath(unquote(parsed.path))))


def _split_href(href: str | None) -> tuple[str | None, str | None]:
    if not href:
        return None, None
    parsed = urlsplit(href)
    if parsed.scheme or parsed.netloc:
        return None, None
    path = _normalize_package_path(parsed.path)
    fragment = unquote(parsed.fragment).strip() or None
    return path or None, fragment


def _style_sources_by_document(
    source_path: Path,
    document_paths: Iterable[str],
) -> dict[str, DocumentStyleSources]:
    sources: dict[str, DocumentStyleSources] = {}
    with ZipFile(source_path) as archive:
        available = set(archive.namelist())
        container = BeautifulSoup(
            archive.read("META-INF/container.xml"),
            "xml",
        )
        rootfiles = container.find_all("rootfile", attrs={"full-path": True})
        package_rootfiles = [
            rootfile
            for rootfile in rootfiles
            if str(rootfile.get("media-type") or "").lower()
            == "application/oebps-package+xml"
        ]
        candidates = package_rootfiles or rootfiles
        if not candidates:
            raise ValueError("EPUB container.xml 缺少 OPF rootfile")
        rootfile = candidates[-1]
        opf_path = _normalize_package_path(str(rootfile.get("full-path")))
        opf_dir = PurePosixPath(opf_path).parent
        for document_path in document_paths:
            normalized = _normalize_package_path(document_path)
            archive_path = _normalize_package_path(str(opf_dir / normalized))
            if archive_path not in available and normalized in available:
                archive_path = normalized
            if archive_path not in available:
                sources[normalized] = DocumentStyleSources()
                continue
            raw = archive.read(archive_path).decode("utf-8", errors="replace")
            soup = BeautifulSoup(raw, "html.parser")
            sources[normalized] = document_styles(soup, normalized, raw)
    return sources


def _role_for(title: str, depth: int, has_children: bool, has_href: bool) -> str:
    normalized = unicodedata.normalize("NFKC", title).strip().lower()
    if re.match(r"^(第.{1,10}卷|卷[一二三四五六七八九十百0-9]+|volume\b)", normalized):
        return "volume"
    if re.match(r"^(第.{1,10}[部篇]|part\b|book\s+[ivxlcdm0-9一二三四五六七八九十]+)", normalized):
        return "part"
    if re.match(r"^(附录|appendix\b)", normalized):
        return "appendix"
    if re.match(r"^(序|序言|前言|引言|preface\b|foreword\b|introduction\b)", normalized):
        return "frontmatter"
    if re.match(r"^(后记|跋|致谢|afterword\b|acknowledg)", normalized):
        return "backmatter"
    if re.match(r"^(第.{1,10}章|chapter\b)", normalized):
        return "chapter"
    if re.match(r"^(第.{1,10}节|section\b)", normalized):
        return "section"
    if has_children and not has_href:
        return "part"
    return "chapter" if depth == 1 else "section"


def _uuid5(namespace: str, value: str) -> str:
    return str(uuid.uuid5(uuid.UUID(namespace), value))


def _title_and_href(entry: Any, fallback: str) -> tuple[str, str | None]:
    title = str(getattr(entry, "title", "") or fallback).strip()
    href = getattr(entry, "href", None)
    return title or fallback, str(href).strip() if href else None


def _normalized_heading_text(value: str) -> str:
    value = unicodedata.normalize("NFKC", value)
    value = re.sub(r"[*_`~]", "", value)
    return re.sub(r"\s+", "", value).strip().casefold()


def _heading_text(heading: Tag) -> str:
    parts: list[str] = []
    for descendant in heading.descendants:
        if isinstance(descendant, NavigableString):
            parts.append(str(descendant))
        elif isinstance(descendant, Tag) and descendant.name == "br":
            parts.append(" ")
    value = unicodedata.normalize("NFKC", "".join(parts))
    return re.sub(r"\s+", " ", value).strip()








def _source_font_evidence(
    book: epub.EpubBook,
    soup: BeautifulSoup,
    style_sources: DocumentStyleSources,
    report,
) -> FontEvidence:
    styles = []
    for kind, value, media in style_sources.ordered_css:
        if kind == 'path':
            item = next((item for item in book.get_items_of_type(ebooklib.ITEM_STYLE) if _normalize_package_path(item.file_name) == value), None)
            if item is None:
                report('字体样式文件缺失，保留源编号。')
                return {}
            value = item.get_content().decode('utf-8', errors='replace')
        styles.append((value, bool(media and media.lower() != 'all')))
    return author_font_evidence(soup, styles, report)


def _has_monospace_style(tag: Tag, evidence: FontEvidence) -> bool:
    mono_text, other_text, source_text, fallback_positions = [], [], [], []
    offset = 0
    for text in tag.descendants:
        if isinstance(text, Tag):
            if text.name == 'br':
                offset += 1
                source_text.append('\n')
            continue
        if isinstance(text, (Comment, ProcessingInstruction)):
            continue
        font = evidence.get(id(text.parent))
        if font is None and str(text).strip():
            return False
        (mono_text if font is True else other_text).append(str(text))
        source_text.append(str(text))
        if font is not True:
            fallback_positions.extend(offset+index for index, character in enumerate(str(text)) if character.isalnum())
        offset += len(str(text))
    mono = ''.join(mono_text)
    other = ''.join(other_text)
    if not mono.strip():
        return False
    if not any(character.isalnum() for character in other):
        return True
    # Known proportional text is accepted only inside a comment or string,
    # allowing CJK annotations in separate spans without consuming plain prose.
    if not re.search(r'[(){}\[\];=/*#$%<>:"\'\\]', mono):
        return False
    full_text = ''.join(source_text)
    literals = [match.span() for match in re.finditer(r'/\*.*?\*/|//[^\n]*|(?<!\S)#[^\n]*|"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'', full_text, re.DOTALL)]
    return all(any(left <= position < right for left, right in literals) for position in fallback_positions)


def _preserved_element_text(tag: Tag) -> str:
    parts: list[str] = []
    for descendant in tag.descendants:
        if isinstance(descendant, NavigableString):
            parts.append(str(descendant))
        elif isinstance(descendant, Tag) and descendant.name == "br":
            parts.append("\n")
    return "".join(parts).replace("\r\n", "\n").replace("\r", "\n")


def _preserve_source_anchors(tag: Tag) -> bool:
    own = {
        str(tag.get(attribute))
        for attribute in ("id", "name")
        if tag.get(attribute)
    }
    nested = {
        str(target.get(attribute))
        for target in tag.find_all(True)
        for attribute in ("id", "name")
        if target.get(attribute)
    }
    aliases = nested - own
    for target in tag.find_all(attrs={_SOURCE_ANCHORS_ATTRIBUTE: True}):
        nested_encoded = target.get(_SOURCE_ANCHORS_ATTRIBUTE)
        if not isinstance(nested_encoded, str):
            continue
        try:
            nested_aliases = json.loads(nested_encoded)
        except (TypeError, json.JSONDecodeError):
            continue
        if isinstance(nested_aliases, list):
            aliases.update(str(value) for value in nested_aliases)
    encoded = tag.get(_SOURCE_ANCHORS_ATTRIBUTE)
    if isinstance(encoded, str):
        try:
            existing = json.loads(encoded)
        except (TypeError, json.JSONDecodeError):
            existing = []
        if isinstance(existing, list):
            aliases.update(str(value) for value in existing)
    if aliases:
        tag[_SOURCE_ANCHORS_ATTRIBUTE] = json.dumps(
            sorted(aliases), ensure_ascii=False, separators=(",", ":")
        )
    return bool(own or nested or aliases)


def _code_line_payload(
    tag: Tag,
    font_evidence: FontEvidence,
) -> str | None:
    if tag.find_parent(["pre", "code", "table"]) is not None:
        return None
    if not can_replace_with_text(tag):
        return None
    if not _has_monospace_style(tag, font_evidence):
        return None
    text = _preserved_element_text(tag)
    match = re.fullmatch(
        r"[ \t\u00a0]*(\d+)(?:[ \t\u00a0](.*))?",
        text,
        re.DOTALL,
    )
    if not match:
        return None
    return (match.group(2) or "").replace("\u00a0", " ")


def _mark_code_listing_lines(
    soup: BeautifulSoup,
    book: epub.EpubBook,
    style_sources: DocumentStyleSources,
    report,
) -> None:
    rules = _source_font_evidence(book, soup, style_sources, report)

    def following_block(current: Tag) -> Tag | None:
        tail = current
        while isinstance(tail, Tag) and tail.contents:
            tail = tail.contents[-1]
        following = tail.next_element
        while following is not None:
            if isinstance(following, Tag):
                if following.name in {"p", "h1", "h2", "h3", "h4", "h5", "h6"}:
                    return following
                if following.name not in {"div", "section", "article", "main"}:
                    return None
            elif not isinstance(following, Comment) and str(following).strip():
                return None
            following = following.next_element
        return None

    candidates = list(soup.find_all(["p", "h1", "h2", "h3", "h4", "h5", "h6"]))
    consumed: set[int] = set()
    for tag in candidates:
        if id(tag) in consumed or tag.parent is None:
            continue
        payload = _code_line_payload(tag, rules)
        if payload is None:
            continue
        run = [(tag, payload)]
        number = int(re.match(r"\s*(\d+)", _preserved_element_text(tag)).group(1))
        current = tag
        while True:
            following = following_block(current)
            if following is None:
                break
            value = _code_line_payload(following, rules)
            match = re.match(r"\s*(\d+)", _preserved_element_text(following))
            if value is None or match is None or int(match.group(1)) != number + 1:
                break
            run.append((following, value))
            current = following
            number += 1
        if len(run) < 2:
            continue
        pre = soup.new_tag("pre")
        code = soup.new_tag("code")
        language = _infer_code_language([value for _, value in run])
        if language:
            code["class"] = [f"language-{language}"]
        code.string = "\n".join(value for _, value in run) + "\n"
        pre.append(code)
        tag.insert_before(pre)
        for original, _ in run:
            _preserve_source_anchors(original)
            aliases = set(json.loads(original.get(_SOURCE_ANCHORS_ATTRIBUTE) or "[]"))
            aliases.update(str(original.get(key)) for key in ("id", "name") if original.get(key))
            for alias in sorted(aliases):
                pre.insert_before(soup.new_tag("a", id=alias))
            consumed.add(id(original))
            original.decompose()



def _normalize_code_listing_captions(soup: BeautifulSoup) -> None:
    for tag in soup.find_all(["p", "figcaption"]):
        if not can_replace_with_text(tag):
            continue
        text = _heading_text(tag)
        if not re.match(r"^(?:代码清单|程序清单|code\s+listing\b|listing\b)", text, re.I):
            continue
        text = re.sub(r"^(代码清单|程序清单)(?=\d)", r"\1 ", text)
        _preserve_source_anchors(tag)
        tag.clear()
        strong = soup.new_tag("strong")
        strong.string = text
        tag.append(strong)


def _infer_code_language(lines: list[str]) -> str:
    sample = "\n".join(lines)
    if re.search(
        r"^\s*#include\b|\b(?:int|void|struct|char|long|static)\s+\w+\s*[({=*;]",
        sample,
        re.MULTILINE,
    ):
        return "c"
    if re.search(
        r"(?:^|\s)(?:mov[bwlq]?|jmp[lq]?|rep|push[lq]?|pop[lq]?)\s"
        r"|(?:^|\s)int\s+(?:\$?0x[0-9a-f]+|\$?\d+)\b"
        r"|(?:^|\n)\s*\.(?:code16|text|global)\b",
        sample,
        re.IGNORECASE | re.MULTILINE,
    ):
        return "asm"
    if re.search(r"^\s*(?:#!.*\b(?:sh|bash)|\$\s+)", sample, re.MULTILINE):
        return "bash"
    if re.search(r"^\s*(?:def|class|from|import)\s+\w+", sample, re.MULTILINE):
        return "python"
    return ""




def _merge_adjacent_emphasis(soup: BeautifulSoup) -> None:
    for parent in soup.find_all(True):
        while True:
            children = list(parent.children)
            merged_run = False
            for index, child in enumerate(children):
                if not isinstance(child, Tag) or child.name not in {"b", "strong"}:
                    continue
                run: list[Tag | NavigableString] = [child]
                emphasis_count = 1
                cursor = index + 1
                pending_space: list[NavigableString] = []
                while cursor < len(children):
                    candidate = children[cursor]
                    if isinstance(candidate, NavigableString) and not str(candidate).strip():
                        pending_space.append(candidate)
                        cursor += 1
                        continue
                    if isinstance(candidate, Tag) and candidate.name in {"b", "strong"}:
                        run.extend(pending_space)
                        pending_space = []
                        run.append(candidate)
                        emphasis_count += 1
                        cursor += 1
                        continue
                    break
                if emphasis_count < 2:
                    continue
                _preserve_source_anchors(parent)
                merged = soup.new_tag("strong")
                child.insert_before(merged)
                for member in run:
                    if isinstance(member, NavigableString):
                        merged.append(member.extract())
                        continue
                    for content in list(member.contents):
                        merged.append(content.extract())
                    member.decompose()
                merged_run = True
                break
            if not merged_run:
                break


def _is_entirely_emphasized(tag: Tag) -> bool:
    text_nodes = [
        node
        for node in tag.find_all(string=True)
        if str(node).strip()
    ]
    return bool(text_nodes) and all(
        node.find_parent(["b", "strong"]) is not None for node in text_nodes
    )


def _promote_numbered_emphasis_blocks(soup: BeautifulSoup) -> None:
    current_heading_level: int | None = None
    for tag in list(soup.find_all(["h1", "h2", "h3", "h4", "h5", "h6", "p"])):
        if tag.name and re.fullmatch(r"h[1-6]", tag.name):
            current_heading_level = int(tag.name[1])
            continue
        if current_heading_level is None or tag.name != "p":
            continue
        text = _heading_text(tag)
        if len(text) > 100 or not re.match(r"^\d{1,3}\s*[.．、)]\s*\S", text):
            continue
        if not _is_entirely_emphasized(tag):
            continue
        _preserve_source_anchors(tag)
        tag.name = f"h{min(current_heading_level + 1, 6)}"




def _prepare_document(
    book: epub.EpubBook,
    item: epub.EpubHtml,
    assets: dict[str, AssetRef],
    asset_items: dict[str, Any],
    style_sources: DocumentStyleSources,
    styles: dict[str, str],
    diagnostics: list[Diagnostic],
    resource_state: ResourceState | None = None,
) -> BeautifulSoup:
    soup = BeautifulSoup(style_sources.source_html or item.content.decode("utf-8", errors="replace"), "html.parser")
    _mark_code_listing_lines(soup, book, style_sources, lambda message: diagnostics.append(
        Diagnostic('code_font_uncertain', message, source_path=_normalize_package_path(item.file_name))
    ))
    _normalize_code_listing_captions(soup)
    _merge_adjacent_emphasis(soup)
    _promote_numbered_emphasis_blocks(soup)
    def load_resource(path):
        resource = asset_items.get(path)
        return (resource.get_content(), getattr(resource, "media_type", None)) if resource is not None else None

    return prepare_html_document(soup, _normalize_package_path(item.file_name), assets, load_resource, style_sources, styles, diagnostics, resource_state=resource_state)


def _render_and_split_document(
    *,
    book: epub.EpubBook,
    item: epub.EpubHtml,
    node_ids: list[str],
    nodes: dict[str, StructureNode],
    assets: dict[str, AssetRef],
    asset_items: dict[str, Any],
    style_sources: DocumentStyleSources,
    styles: dict[str, str],
    diagnostics: list[Diagnostic],
    resource_state: ResourceState | None = None,
) -> list[tuple[str, str]]:
    soup = _prepare_document(book, item, assets, asset_items, style_sources, styles, diagnostics, resource_state)
    container = soup.body or soup
    boundaries: dict[int, str] = {}
    seen: set[str] = set()
    for node_id in node_ids:
        node = nodes[node_id]
        key = node.source.key
        if key in seen:
            diagnostics.append(Diagnostic("duplicate_source_target", "重复目录目标仅保留一份正文。", node_id=node_id, source_path=item.file_name))
            continue
        seen.add(key)
        target = container.find(id=source_anchor(_normalize_package_path(item.file_name), node.source.fragment or ""))
        if target is None:
            diagnostics.append(Diagnostic("missing_toc_anchor", f"目录锚点不存在：#{node.source.fragment}", node_id=node_id, source_path=item.file_name))
        else:
            boundaries[id(target)] = node_id
    return [(owner, scoped_fragment(soup, html, _normalize_package_path(item.file_name))) for owner, html in partition(container, boundaries, node_ids[0])]



def _render_complete_document(
    *, book: epub.EpubBook, item: epub.EpubHtml, assets: dict[str, AssetRef],
    asset_items: dict[str, Any], style_sources: DocumentStyleSources,
    styles: dict[str, str], diagnostics: list[Diagnostic], resource_state: ResourceState | None = None,
) -> str:
    soup = _prepare_document(book, item, assets, asset_items, style_sources, styles, diagnostics, resource_state)
    return scoped_fragment(soup, (soup.body or soup).decode_contents(), _normalize_package_path(item.file_name))



def _split_document_family(path: str) -> tuple[str, str] | None:
    match = _SPLIT_DOCUMENT_RE.match(path)
    if not match:
        return None
    return match.group("family"), match.group("suffix").lower()


def _first_heading(item: epub.EpubHtml, fallback: str) -> str:
    soup = BeautifulSoup(item.content.decode("utf-8", errors="replace"), "html.parser")
    heading = soup.find(["h1", "h2", "h3"])
    if heading and _heading_text(heading):
        return _heading_text(heading)
    if soup.title:
        title = _heading_text(soup.title)
        if title.casefold() not in {"", "unknown", "untitled", "未知", "无标题"} and not re.fullmatch(r".*\.(?:x?html?|xml)", title, re.I):
            return title
    return fallback


def read_epub_book(
    source_path: Path,
    *,
    fallback_title: str | None = None,
    source_sha256: str,
    source_id: str,
    book_id: str,
    edition_id: str,
    revision_id: str,
) -> BookRevision:
    """Read a valid EPUB, retaining nested navigation and source locations."""
    book = epub.read_epub(str(source_path), options={"ignore_ncx": False})
    metadata = BookMetadata(
        title=_metadata_value(book, "title") or fallback_title or source_path.stem,
        authors=_authors(book),
        language=_metadata_value(book, "language"),
        identifier=_metadata_value(book, "identifier"),
    )
    nodes: dict[str, StructureNode] = {}
    roots: list[str] = []
    diagnostics: list[Diagnostic] = []
    occurrence_by_key: dict[str, int] = defaultdict(int)

    def add_node(entry: Any, parent_id: str | None, ordinal_path: tuple[int, ...]) -> str:
        title, href = _title_and_href(entry, f"未命名章节 {'.'.join(map(str, ordinal_path))}")
        path, fragment = _split_href(href)
        base_key = f"epub:{path}{'#' + fragment if fragment else ''}" if path else (
            "toc:" + ".".join(f"{value:03d}" for value in ordinal_path)
        )
        occurrence = occurrence_by_key[base_key]
        occurrence_by_key[base_key] += 1
        source_key = base_key if occurrence == 0 else f"{base_key}|occurrence:{occurrence + 1}"
        node_id = _uuid5(edition_id, source_key)
        node = StructureNode(
            node_id=node_id,
            parent_id=parent_id,
            children_ids=[],
            depth=len(ordinal_path),
            sibling_order=ordinal_path[-1],
            ordinal_path=ordinal_path,
            title_original=title,
            title_display=title,
            role="unknown",
            source_key=source_key,
            source=SourceLocator(path=path, fragment=fragment),
        )
        nodes[node_id] = node
        if parent_id:
            nodes[parent_id].children_ids.append(node_id)
        else:
            roots.append(node_id)
        return node_id

    def parse_entries(entries: Iterable[Any], parent_id: str | None, prefix: tuple[int, ...]) -> None:
        logical_entries: list[Any] = []
        for entry in entries:
            if isinstance(entry, list):
                logical_entries.extend(entry)
            else:
                logical_entries.append(entry)
        for sibling_order, entry in enumerate(logical_entries, start=1):
            ordinal = (*prefix, sibling_order)
            if isinstance(entry, tuple) and len(entry) == 2:
                header, children = entry
                node_id = add_node(header, parent_id, ordinal)
                parse_entries(children or [], node_id, ordinal)
            elif isinstance(entry, (epub.Link, epub.Section)):
                add_node(entry, parent_id, ordinal)
            else:
                diagnostics.append(
                    Diagnostic(
                        code="unknown_toc_entry",
                        message=f"无法识别的目录条目类型：{type(entry).__name__}",
                    )
                )

    parse_entries(book.toc or [], None, ())

    spine_items: list[epub.EpubHtml] = []
    spine_index: dict[str, int] = {}
    for position, entry in enumerate(book.spine):
        item_id = entry[0] if isinstance(entry, tuple) else entry
        item = book.get_item_with_id(item_id) if item_id else None
        if not isinstance(item, epub.EpubHtml):
            continue
        if getattr(item, "get_type", lambda: None)() == ebooklib.ITEM_NAVIGATION:
            continue
        normalized = _normalize_package_path(item.file_name)
        spine_index[normalized] = position
        spine_items.append(item)

    referenced_nodes_by_path: dict[str, list[str]] = defaultdict(list)
    for node_id, node in nodes.items():
        if node.source.path:
            referenced_nodes_by_path[node.source.path].append(node_id)
    referenced = set(referenced_nodes_by_path)
    continuation_owner_by_path: dict[str, str] = {}
    split_family_owner: dict[tuple[str, str], str] = {}

    for item in spine_items:
        normalized = _normalize_package_path(item.file_name)
        if normalized in referenced:
            family = _split_document_family(normalized)
            if family:
                split_family_owner[family] = min(
                    referenced_nodes_by_path[normalized],
                    key=lambda node_id: nodes[node_id].depth,
                )
            continue

        family = _split_document_family(normalized)
        owner_id = split_family_owner.get(family) if family else None
        if owner_id:
            continuation_owner_by_path[normalized] = owner_id
            nodes[owner_id].continuation_sources.append(
                SourceLocator(path=normalized, spine_index=spine_index.get(normalized))
            )
            diagnostics.append(
                Diagnostic(
                    code="physical_document_merged",
                    message="EPUB 物理分片未创建独立章节，正文已并入前一个目录章节。",
                    node_id=owner_id,
                    source_path=normalized,
                )
            )
            continue

        ordinal = (len(roots) + 1,)
        title = _first_heading(item, f"正文 {spine_index[normalized] + 1}")
        node_id = add_node(epub.Link(normalized, title), None, ordinal)
        nodes[node_id].role = "frontmatter" if len(roots) == 1 else "chapter"
        if family:
            split_family_owner[family] = node_id
        diagnostics.append(
            Diagnostic(
                code="unlisted_spine_document",
                message="该正文文件未列入 EPUB 目录，已作为顶层章节追加。",
                node_id=node_id,
                source_path=normalized,
            )
        )

    if not roots:
        raise ValueError("EPUB 中没有可读取的目录或正文文件")

    for node in nodes.values():
        node.source = SourceLocator(
            path=node.source.path,
            fragment=node.source.fragment,
            spine_index=spine_index.get(node.source.path or ""),
        )
        node.role = _role_for(
            node.title_display,
            node.depth,
            bool(node.children_ids),
            bool(node.source.path),
        )

    item_by_path = {
        _normalize_package_path(item.file_name): item
        for item in book.get_items_of_type(ebooklib.ITEM_DOCUMENT)
        if isinstance(item, epub.EpubHtml)
    }
    style_sources_by_path = _style_sources_by_document(
        source_path,
        item_by_path,
    )
    asset_items = {
        _normalize_package_path(item.file_name): item
        for item in book.get_items()
    }
    assets: dict[str, AssetRef] = {}
    node_ids_by_path: dict[str, list[str]] = defaultdict(list)

    def walk(node_id: str) -> None:
        node = nodes[node_id]
        if node.source.path:
            node_ids_by_path[node.source.path].append(node_id)
        for child_id in node.children_ids:
            walk(child_id)

    for root_id in roots:
        walk(root_id)

    source_order: list[str] = []
    content: list[ContentFragment] = []
    styles: dict[str, str] = {}
    anchor_owners: dict[str, str] = {}
    processed_paths: set[str] = set()
    resource_state = ResourceState()
    ordered_paths = [_normalize_package_path(item.file_name) for item in spine_items]
    ordered_paths.extend(path for path in node_ids_by_path if path not in spine_index)
    for path in ordered_paths:
        if path in processed_paths:
            continue
        processed_paths.add(path)
        item = item_by_path.get(path)
        if item is None:
            diagnostics.append(Diagnostic("missing_content_document", "目录指向的正文文件不存在。", severity="error", source_path=path))
            continue
        continuation_owner = continuation_owner_by_path.get(path)
        style_sources = style_sources_by_path.get(path, DocumentStyleSources())
        if continuation_owner:
            pieces = [(continuation_owner, _render_complete_document(
                book=book, item=item, assets=assets, asset_items=asset_items,
                style_sources=style_sources, styles=styles, diagnostics=diagnostics, resource_state=resource_state,
            ))]
        elif path in node_ids_by_path:
            pieces = _render_and_split_document(
                book=book, item=item, node_ids=node_ids_by_path[path], nodes=nodes,
                assets=assets, asset_items=asset_items, style_sources=style_sources,
                styles=styles, diagnostics=diagnostics, resource_state=resource_state,
            )
        else:
            continue
        for index, (node_id, html) in enumerate(pieces):
            node = nodes[node_id]
            if not node.content_ids:
                html = strip_title(html, node.title_display)
            scoped = BeautifulSoup(html, "html.parser")
            fragment_id = _uuid5(edition_id, f"fragment:{path}:{index}:{node_id}")
            fragment = ContentFragment(fragment_id, node_id, SourceLocator(path=path, spine_index=spine_index.get(path)), str(scoped))
            content.append(fragment)
            node.content_ids.append(fragment_id)
            source_order.append(node_id)
            for target in scoped.find_all(id=True):
                anchor = str(target["id"])
                if anchor in anchor_owners:
                    raise ValueError(f"规范内容锚点重复：{anchor}")
                anchor_owners[anchor] = node_id

    for fragment in content:
        soup = BeautifulSoup(fragment.html, "html.parser")
        for link in soup.find_all("a", href=True):
            href = str(link["href"])
            if href.startswith("#src-") and href[1:] not in anchor_owners:
                diagnostics.append(Diagnostic("unresolved_internal_reference", f"源引用目标不存在：{href}", node_id=fragment.node_id, source_path=fragment.source.path))

    reading_order_ids: list[str] = []
    emitted: set[str] = set()

    def ancestry(node_id: str) -> list[str]:
        result: list[str] = []
        current: str | None = node_id
        while current:
            result.append(current)
            current = nodes[current].parent_id
        return list(reversed(result))

    for node_id in source_order:
        for candidate in ancestry(node_id):
            if candidate not in emitted:
                reading_order_ids.append(candidate)
                emitted.add(candidate)
    for node_id in [item for root_id in roots for item in _walk_node_ids(nodes, root_id)]:
        if node_id not in emitted:
            reading_order_ids.append(node_id)
            emitted.add(node_id)

    reading_position = {
        node_id: index for index, node_id in enumerate(reading_order_ids)
    }

    def subtree_position(node_id: str) -> int:
        positions = [reading_position.get(node_id, len(reading_position))]
        positions.extend(subtree_position(child_id) for child_id in nodes[node_id].children_ids)
        return min(positions)

    def reorder_children(node_id: str) -> None:
        node = nodes[node_id]
        original = list(node.children_ids)
        node.children_ids.sort(key=subtree_position)
        if node.children_ids != original:
            diagnostics.append(
                Diagnostic(
                    code="toc_order_adjusted_to_reading_order",
                    message="目录子节点顺序与正文顺序不一致，导出时已按正文阅读顺序调整。",
                    node_id=node_id,
                    source_path=node.source.path,
                )
            )
        for child_id in node.children_ids:
            reorder_children(child_id)

    original_roots = list(roots)
    roots.sort(key=subtree_position)
    if roots != original_roots:
        diagnostics.append(
            Diagnostic(
                code="root_order_adjusted_to_reading_order",
                message="顶层目录顺序与正文顺序不一致，导出时已按正文阅读顺序调整。",
            )
        )
    for root_id in roots:
        reorder_children(root_id)

    def assign_ordinals(node_id: str, ordinal_path: tuple[int, ...]) -> None:
        node = nodes[node_id]
        node.ordinal_path = ordinal_path
        node.depth = len(ordinal_path)
        node.sibling_order = ordinal_path[-1]
        for index, child_id in enumerate(node.children_ids, start=1):
            assign_ordinals(child_id, (*ordinal_path, index))

    for index, root_id in enumerate(roots, start=1):
        assign_ordinals(root_id, (index,))

    return BookRevision(
        book_id=book_id,
        edition_id=edition_id,
        revision_id=revision_id,
        source_id=source_id,
        source_path=source_path,
        source_sha256=source_sha256,
        metadata=metadata,
        roots=roots,
        nodes=nodes,
        reading_order_ids=reading_order_ids,
        assets=assets,
        diagnostics=diagnostics,
        pipeline_version=PIPELINE_VERSION,
        content=content,
        styles=styles,
        anchor_owners=anchor_owners,
    )




def _walk_node_ids(nodes: dict[str, StructureNode], root_id: str) -> list[str]:
    ordered = [root_id]
    for child_id in nodes[root_id].children_ids:
        ordered.extend(_walk_node_ids(nodes, child_id))
    return ordered
