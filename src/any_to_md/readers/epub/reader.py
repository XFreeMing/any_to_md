"""Read an EPUB into the book-oriented intermediate representation."""

from __future__ import annotations

import hashlib
import json
import posixpath
import re
import unicodedata
import uuid
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterable
from urllib.parse import unquote, urlsplit
from zipfile import ZipFile

import ebooklib
from bs4 import BeautifulSoup, NavigableString, ProcessingInstruction, Tag
from ebooklib import epub
from markdownify import markdownify

from ...domain.book import (
    AssetRef,
    BookMetadata,
    BookRevision,
    Diagnostic,
    SourceLocator,
    StructureNode,
)

PIPELINE_VERSION = "epub-book-v7"
_ASSET_TOKEN_PREFIX = "ANYTOMDASSETTOKEN"
_ASSET_TOKEN_SUFFIX = "ENDTOKEN"
_SECTION_TOKEN_PREFIX = "ANYTOMDSECTIONTOKEN"
_SECTION_TOKEN_SUFFIX = "ENDTOKEN"
_END_TOKEN = "ANYTOMDDOCUMENTENDTOKEN"
_HEADING_TOKEN_PREFIX = "ANYTOMDHEADINGTOKEN"
_HEADING_TOKEN_SUFFIX = "ENDTOKEN"
_CODE_LINE_TOKEN_PREFIX = "ANYTOMDCODELINETOKEN"
_CODE_LINE_TOKEN_SUFFIX = "ENDTOKEN"
_SOURCE_ANCHORS_ATTRIBUTE = "data-any-to-md-source-anchors"
_SPLIT_DOCUMENT_RE = re.compile(
    r"^(?P<family>.+)_split_(?P<part>\d+)(?P<suffix>\.[^./]+)$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class _DocumentStyleSources:
    linked_paths: frozenset[str] = frozenset()
    inline_css: tuple[str, ...] = ()


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
) -> dict[str, _DocumentStyleSources]:
    sources: dict[str, _DocumentStyleSources] = {}
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
                sources[normalized] = _DocumentStyleSources()
                continue
            raw = archive.read(archive_path).decode("utf-8", errors="replace")
            soup = BeautifulSoup(raw, "html.parser")
            paths: set[str] = set()
            for link in soup.find_all("link", href=True):
                rel = link.get("rel") or []
                rel_values = [rel] if isinstance(rel, str) else list(rel)
                if "stylesheet" not in {str(value).lower() for value in rel_values}:
                    continue
                resolved = _resolve_package_path(normalized, str(link.get("href")))
                if resolved:
                    paths.add(resolved)
            inline_css = tuple(
                style.get_text("", strip=False) for style in soup.find_all("style")
            )
            sources[normalized] = _DocumentStyleSources(
                linked_paths=frozenset(paths),
                inline_css=inline_css,
            )
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


def _strip_leading_title_headings(markdown: str, title: str) -> str:
    text = markdown.strip()
    if not text:
        return ""
    target = _normalized_heading_text(title)
    consumed = 0
    combined = ""
    pattern = re.compile(
        rf"\A\s*(#{{1,6}})[ \t]+"
        rf"(?:{_HEADING_TOKEN_PREFIX}\d{_HEADING_TOKEN_SUFFIX})?"
        r"(.+?)[ \t]*(?:\n+|\Z)"
    )
    for _ in range(4):
        match = pattern.match(text[consumed:])
        if not match:
            break
        candidate = combined + _normalized_heading_text(match.group(2))
        if not target.startswith(candidate):
            break
        combined = candidate
        consumed += match.end()
        if combined == target:
            return text[consumed:].strip()
    return text


def _normalize_relative_headings(markdown: str) -> str:
    """Map source h-levels to levels relative to the owning structure node."""
    pattern = re.compile(
        rf"^(#{{1,6}})[ \t]+{_HEADING_TOKEN_PREFIX}"
        rf"(?P<level>[1-6]){_HEADING_TOKEN_SUFFIX}"
        r"(?P<title>.*)$",
        re.MULTILINE,
    )
    matches = list(pattern.finditer(markdown))
    if not matches:
        return markdown.strip()
    base_level = min(int(match.group("level")) for match in matches)

    def replace(match: re.Match[str]) -> str:
        relative_level = min(int(match.group("level")) - base_level + 1, 6)
        return f"{'#' * relative_level} {match.group('title').strip()}"

    return pattern.sub(replace, markdown).strip()


def _table_to_markdown(table: Tag) -> str:
    rows: list[tuple[list[str], bool]] = []
    for row in table.find_all("tr"):
        cells: list[str] = []
        header = False
        for cell in row.find_all(["th", "td"]):
            cells.append(cell.get_text(" ", strip=True).replace("|", "\\|"))
            header = header or cell.name == "th"
        if cells:
            rows.append((cells, header))
    if not rows:
        return ""
    width = max(len(cells) for cells, _ in rows)
    padded = [(cells + [""] * (width - len(cells)), header) for cells, header in rows]
    first = padded[0][0] if padded[0][1] else [" "] * width
    body = [cells for cells, _ in (padded[1:] if padded[0][1] else padded)]
    lines = [
        "| " + " | ".join(first) + " |",
        "| " + " | ".join(["---"] * width) + " |",
    ]
    lines.extend("| " + " | ".join(cells) + " |" for cells in body)
    return "\n".join(lines)


def _monospace_rules(
    book: epub.EpubBook,
    soup: BeautifulSoup,
    style_sources: _DocumentStyleSources,
) -> set[tuple[str | None, str]]:
    rules: set[tuple[str | None, str]] = set()
    family_pattern = re.compile(
        r"font-family\s*:\s*[^;}]*(?:mono|courier|consolas|source\s+code|menlo)",
        re.IGNORECASE,
    )
    rule_pattern = re.compile(r"([^{}]+)\{([^{}]*)\}")
    css_sources = [*style_sources.inline_css]
    css_sources.extend(style.get_text("", strip=False) for style in soup.find_all("style"))
    css_sources.extend(
        style_item.get_content().decode("utf-8", errors="replace")
        for style_item in book.get_items_of_type(ebooklib.ITEM_STYLE)
        if _normalize_package_path(style_item.file_name) in style_sources.linked_paths
    )
    for css in css_sources:
        css = re.sub(r"/\*.*?\*/", "", css, flags=re.DOTALL)
        for selector, declarations in rule_pattern.findall(css):
            if not family_pattern.search(declarations):
                continue
            for component in selector.split(","):
                simple = re.fullmatch(
                    r"\s*(?:(?P<tag>[a-zA-Z][\w-]*))?"
                    r"\.(?P<class>[_a-zA-Z][\w-]*)\s*",
                    component,
                )
                if simple:
                    tag_name = simple.group("tag")
                    rules.add((tag_name.lower() if tag_name else None, simple.group("class")))
    return rules


def _element_has_monospace_style(
    tag: Tag,
    monospace_rules: set[tuple[str | None, str]],
) -> bool:
    classes = tag.get("class") or []
    for class_name in classes:
        if (None, str(class_name)) in monospace_rules:
            return True
        if (str(tag.name).lower(), str(class_name)) in monospace_rules:
            return True
    style = str(tag.get("style") or "")
    return bool(
        re.search(
            r"font-family\s*:[^;]*(?:mono|courier|consolas|source\s+code|menlo)",
            style,
            re.IGNORECASE,
        )
    )


def _has_monospace_style(
    tag: Tag,
    monospace_rules: set[tuple[str | None, str]],
) -> bool:
    current: Tag | None = tag
    while current is not None:
        if _element_has_monospace_style(current, monospace_rules):
            return True
        parent = current.parent
        current = parent if isinstance(parent, Tag) else None

    full_text = _preserved_element_text(tag)
    for candidate in tag.find_all(True):
        if not _element_has_monospace_style(candidate, monospace_rules):
            continue
        if _preserved_element_text(candidate) == full_text:
            return True
    return False


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
    monospace_rules: set[tuple[str | None, str]],
) -> str | None:
    if tag.find_parent(["pre", "code", "table"]) is not None:
        return None
    if not _has_monospace_style(tag, monospace_rules):
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
    style_sources: _DocumentStyleSources,
) -> None:
    monospace_rules = _monospace_rules(book, soup, style_sources)
    group_id = 0
    in_group = False
    for tag in list(soup.find_all(["p", "h1", "h2", "h3", "h4", "h5", "h6"])):
        code = _code_line_payload(tag, monospace_rules)
        if code is None:
            in_group = False
            continue
        if not in_group:
            group_id += 1
            in_group = True
        _preserve_source_anchors(tag)
        encoded = code.encode("utf-8").hex()
        tag.name = "p"
        tag.clear()
        tag.append(
            NavigableString(
                f"\n{_CODE_LINE_TOKEN_PREFIX}{group_id}X{encoded}"
                f"{_CODE_LINE_TOKEN_SUFFIX}\n"
            )
        )


def _normalize_code_listing_captions(soup: BeautifulSoup) -> None:
    for tag in soup.find_all(["p", "figcaption"]):
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


def _restore_code_blocks(markdown: str) -> str:
    token_pattern = re.compile(
        rf"^(?P<indent>[ \t]*){_CODE_LINE_TOKEN_PREFIX}(?P<group>\d+)X"
        rf"(?P<code>[0-9a-f]*){_CODE_LINE_TOKEN_SUFFIX}[ \t]*$"
    )
    source_lines = markdown.splitlines()
    output: list[str] = []
    index = 0
    while index < len(source_lines):
        match = token_pattern.fullmatch(source_lines[index])
        if match is None:
            output.append(source_lines[index])
            index += 1
            continue
        group = match.group("group")
        indent = match.group("indent")
        code_lines: list[str] = []
        while match is not None and match.group("group") == group:
            code_lines.append(bytes.fromhex(match.group("code")).decode("utf-8"))
            index += 1
            lookahead = index
            while lookahead < len(source_lines) and not source_lines[lookahead].strip():
                lookahead += 1
            if lookahead >= len(source_lines):
                break
            following = token_pattern.fullmatch(source_lines[lookahead])
            if (
                following is None
                or following.group("group") != group
                or following.group("indent") != indent
            ):
                break
            index = lookahead
            match = following
        longest_fence = max(
            (len(run) for line in code_lines for run in re.findall(r"`+", line)),
            default=0,
        )
        fence = "`" * max(3, longest_fence + 1)
        language = _infer_code_language(code_lines)
        output.extend(
            [
                f"{indent}{fence}{language}",
                *(f"{indent}{line}" for line in code_lines),
                f"{indent}{fence}",
            ]
        )
    return "\n".join(output)


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


def _asset_file_name(source_path: str, content: bytes) -> tuple[str, str]:
    digest = hashlib.sha256(content).hexdigest()
    original = PurePosixPath(source_path)
    stem = re.sub(r"[^0-9A-Za-z]+", "-", original.stem).strip("-") or "image"
    suffix = re.sub(r"[^0-9A-Za-z.]", "", original.suffix.lower())
    return digest, f"{stem}-{digest[:12]}{suffix}"


def _prepare_document(
    book: epub.EpubBook,
    item: epub.EpubHtml,
    assets: dict[str, AssetRef],
    asset_items: dict[str, Any],
    style_sources: _DocumentStyleSources,
) -> BeautifulSoup:
    soup = BeautifulSoup(item.get_content().decode("utf-8", errors="replace"), "html.parser")
    for node in list(soup.find_all(string=lambda value: isinstance(value, ProcessingInstruction))):
        node.extract()
    _mark_code_listing_lines(soup, book, style_sources)
    _normalize_code_listing_captions(soup)
    for node in soup.find_all(["script", "style"]):
        node.decompose()
    for table in soup.find_all("table"):
        table.replace_with(NavigableString(f"\n{_table_to_markdown(table)}\n"))
    _merge_adjacent_emphasis(soup)
    _promote_numbered_emphasis_blocks(soup)
    for heading in soup.find_all(re.compile(r"^h[1-6]$")):
        text = _heading_text(heading)
        has_source_anchor = _preserve_source_anchors(heading)
        if re.fullmatch(r"\d+", text) and not has_source_anchor:
            heading.replace_with(NavigableString(text))
            continue
        level = int(heading.name[1])
        heading.clear()
        heading.append(
            NavigableString(
                f"{_HEADING_TOKEN_PREFIX}{level}{_HEADING_TOKEN_SUFFIX}{text}"
            )
        )
    for image in soup.find_all("img"):
        src = image.get("src")
        if not src or src.startswith("data:"):
            continue
        package_path = _resolve_package_path(item.file_name, src)
        asset_item = asset_items.get(package_path)
        if asset_item is None:
            continue
        content = asset_item.get_content()
        asset_id, file_name = _asset_file_name(package_path, content)
        assets.setdefault(
            asset_id,
            AssetRef(
                asset_id=asset_id,
                source_path=package_path,
                file_name=file_name,
                media_type=getattr(asset_item, "media_type", None),
                content=content,
            ),
        )
        image["src"] = f"{_ASSET_TOKEN_PREFIX}{asset_id}{_ASSET_TOKEN_SUFFIX}"
    return soup


def _find_fragment(container: Tag | BeautifulSoup, fragment: str) -> Tag | None:
    direct = container.find(id=fragment) or container.find(attrs={"name": fragment})
    if direct is not None:
        return direct
    for candidate in container.find_all(attrs={_SOURCE_ANCHORS_ATTRIBUTE: True}):
        encoded = candidate.get(_SOURCE_ANCHORS_ATTRIBUTE)
        if not isinstance(encoded, str):
            continue
        try:
            aliases = json.loads(encoded)
        except (TypeError, json.JSONDecodeError):
            continue
        if isinstance(aliases, list) and fragment in aliases:
            return candidate
    return None


def _render_and_split_document(
    *,
    book: epub.EpubBook,
    item: epub.EpubHtml,
    node_ids: list[str],
    nodes: dict[str, StructureNode],
    assets: dict[str, AssetRef],
    asset_items: dict[str, Any],
    style_sources: _DocumentStyleSources,
    diagnostics: list[Diagnostic],
) -> list[str]:
    soup = _prepare_document(
        book,
        item,
        assets,
        asset_items,
        style_sources,
    )
    container = soup.body or soup
    inserted: set[str] = set()
    index_to_node: dict[int, str] = {}

    for index, node_id in enumerate(node_ids):
        node = nodes[node_id]
        locator = node.source.key
        if locator in inserted:
            diagnostics.append(
                Diagnostic(
                    code="duplicate_source_target",
                    message="多个目录节点指向同一正文位置；正文只归属首次出现的节点。",
                    node_id=node_id,
                    source_path=node.source.path,
                )
            )
            continue
        marker = NavigableString(f"{_SECTION_TOKEN_PREFIX}{index}{_SECTION_TOKEN_SUFFIX}")
        if node.source.fragment:
            target = _find_fragment(container, node.source.fragment)
            if target is None:
                diagnostics.append(
                    Diagnostic(
                        code="missing_toc_anchor",
                        message=f"目录锚点不存在：#{node.source.fragment}",
                        node_id=node_id,
                        source_path=node.source.path,
                    )
                )
                continue
            target.insert_before(marker)
        else:
            container.insert(0, marker)
        inserted.add(locator)
        index_to_node[index] = node_id

    container.append(NavigableString(_END_TOKEN))
    rendered = markdownify(
        str(container),
        heading_style="ATX",
        bullets="-",
        escape_asterisks=False,
        escape_underscores=False,
    )
    end_at = rendered.find(_END_TOKEN)
    if end_at >= 0:
        rendered = rendered[:end_at]
    rendered = _restore_code_blocks(rendered)

    token_pattern = re.compile(
        rf"{_SECTION_TOKEN_PREFIX}(\d+){_SECTION_TOKEN_SUFFIX}"
    )
    matches = list(token_pattern.finditer(rendered))
    if not matches:
        fallback_id = node_ids[0]
        nodes[fallback_id].content_markdown = _strip_leading_title_headings(
            rendered, nodes[fallback_id].title_display
        )
        diagnostics.append(
            Diagnostic(
                code="document_assigned_by_fallback",
                message="正文锚点均无法解析，整篇正文已唯一归入首个目录节点。",
                node_id=fallback_id,
                source_path=item.file_name,
            )
        )
        return [fallback_id]

    prefix = rendered[: matches[0].start()].strip()
    for position, match in enumerate(matches):
        marker_index = int(match.group(1))
        node_id = index_to_node.get(marker_index)
        if node_id is None:
            continue
        end = matches[position + 1].start() if position + 1 < len(matches) else len(rendered)
        content = rendered[match.end() : end].strip()
        if position == 0 and prefix:
            content = f"{prefix}\n\n{content}".strip()
        nodes[node_id].content_markdown = _strip_leading_title_headings(
            content, nodes[node_id].title_display
        )
    return [
        index_to_node[int(match.group(1))]
        for match in matches
        if int(match.group(1)) in index_to_node
    ]


def _render_complete_document(
    *,
    book: epub.EpubBook,
    item: epub.EpubHtml,
    assets: dict[str, AssetRef],
    asset_items: dict[str, Any],
    style_sources: _DocumentStyleSources,
) -> str:
    """Render an unindexed physical continuation without inventing a section."""
    soup = _prepare_document(
        book,
        item,
        assets,
        asset_items,
        style_sources,
    )
    container = soup.body or soup
    container.append(NavigableString(_END_TOKEN))
    rendered = markdownify(
        str(container),
        heading_style="ATX",
        bullets="-",
        escape_asterisks=False,
        escape_underscores=False,
    )
    end_at = rendered.find(_END_TOKEN)
    if end_at >= 0:
        rendered = rendered[:end_at]
    return _restore_code_blocks(rendered).strip()


def _split_document_family(path: str) -> tuple[str, str] | None:
    match = _SPLIT_DOCUMENT_RE.match(path)
    if not match:
        return None
    return match.group("family"), match.group("suffix").lower()


def _first_heading(item: epub.EpubHtml) -> str:
    soup = BeautifulSoup(item.get_content().decode("utf-8", errors="replace"), "html.parser")
    heading = soup.find(["h1", "h2", "h3"])
    if heading and _heading_text(heading):
        return _heading_text(heading)
    return str(item.get_id() or PurePosixPath(item.file_name).stem or "未分章正文")


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
        title = _first_heading(item)
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
        for item in book.get_items_of_type(ebooklib.ITEM_IMAGE)
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
    processed_paths: set[str] = set()
    ordered_paths = [
        _normalize_package_path(item.file_name) for item in spine_items
    ] + [path for path in node_ids_by_path if path not in spine_index]
    for path in ordered_paths:
        if path in processed_paths:
            continue
        continuation_owner = continuation_owner_by_path.get(path)
        if continuation_owner:
            processed_paths.add(path)
            item = item_by_path.get(path)
            if item is None:
                diagnostics.append(
                    Diagnostic(
                        code="missing_continuation_document",
                        message="章节的 EPUB 后续物理分片不存在。",
                        node_id=continuation_owner,
                        source_path=path,
                        severity="error",
                    )
                )
                continue
            continuation = _render_complete_document(
                book=book,
                item=item,
                assets=assets,
                asset_items=asset_items,
                style_sources=style_sources_by_path.get(
                    path,
                    _DocumentStyleSources(),
                ),
            )
            if continuation:
                owner = nodes[continuation_owner]
                owner.content_markdown = (
                    f"{owner.content_markdown}\n\n{continuation}".strip()
                )
            source_order.append(continuation_owner)
            continue
        if path not in node_ids_by_path:
            continue
        processed_paths.add(path)
        path_node_ids = node_ids_by_path[path]
        item = item_by_path.get(path)
        if item is None:
            diagnostics.append(
                Diagnostic(
                    code="missing_content_document",
                    message="目录指向的 EPUB 正文文件不存在。",
                    source_path=path,
                    severity="error",
                )
            )
            continue
        source_order.extend(_render_and_split_document(
            book=book,
            item=item,
            node_ids=path_node_ids,
            nodes=nodes,
            assets=assets,
            asset_items=asset_items,
            style_sources=style_sources_by_path.get(
                path,
                _DocumentStyleSources(),
            ),
            diagnostics=diagnostics,
        ))

    for node in nodes.values():
        node.content_markdown = _normalize_relative_headings(node.content_markdown)

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
    )


def asset_token(asset_id: str) -> str:
    return f"{_ASSET_TOKEN_PREFIX}{asset_id}{_ASSET_TOKEN_SUFFIX}"


def _walk_node_ids(nodes: dict[str, StructureNode], root_id: str) -> list[str]:
    ordered = [root_id]
    for child_id in nodes[root_id].children_ids:
        ordered.extend(_walk_node_ids(nodes, child_id))
    return ordered
