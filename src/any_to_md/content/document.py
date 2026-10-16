"""Source-independent HTML anchors, media and scoped author styles.

Readers supply source bytes and resource lookup; format-specific text cleanup
and chapter discovery stay in the corresponding reader.
"""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import unquote, urlsplit, urljoin

from bs4 import BeautifulSoup, ProcessingInstruction, Tag

from .references import register_asset, resolve_path, rewrite_srcset, scope_id, source_anchor
from .styles import MAX_STYLE_DEPTH, StyleLimit, rewrite_declarations, scope_styles
from .media import MediaFreezer, ResourceState, xml_resource_base
from ..domain.book import AssetRef, Diagnostic

SOURCE_ANCHORS_ATTRIBUTE = "data-any-to-md-source-anchors"


@dataclass(frozen=True)
class DocumentStyleSources:
    linked_paths: tuple[str, ...] = ()
    ordered_css: tuple[tuple[str, str, str], ...] = ()
    source_html: str = ""
    inline_css: tuple[str, ...] = ()


def document_base(soup: BeautifulSoup, path: str) -> str:
    base = soup.find('base', href=True)
    return urljoin(path, str(base['href'])) if base else path


def element_resource_base(element: Tag, document: str) -> str:
    return xml_resource_base(document, [ancestor.get('xml:base') for ancestor in [*reversed(list(element.parents)), element]])


def document_styles(soup: BeautifulSoup, path: str, source_html: str = "") -> DocumentStyleSources:
    paths, ordered, inline = [], [], []
    for element in soup.find_all(["link", "style"]):
        media = str(element.get("media") or "").strip()
        if element.name == "style":
            css = element.get_text("", strip=False)
            inline.append(css)
            ordered.append(("inline", css, media))
        elif element.get("href"):
            relations = {str(value).lower() for value in element.get("rel", [])}
            if "stylesheet" in relations and "alternate" not in relations and not element.has_attr("disabled"):
                target = resolve_path(path, str(element["href"]))
                if target:
                    paths.append(target)
                    ordered.append(("path", target, media))
    return DocumentStyleSources(tuple(paths), tuple(ordered), source_html, tuple(inline))


def _unsafe_link(value: str) -> bool:
    return urlsplit(value).scheme.lower() in {'javascript', 'vbscript', 'file'}


def sanitize_html(soup: BeautifulSoup) -> None:
    """Shared active-markup policy, before canonical or interim DOM rendering."""
    for node in list(soup.find_all(string=lambda value: isinstance(value, ProcessingInstruction))):
        node.extract()
    for node in soup.find_all(['script', 'iframe', 'object', 'embed']):
        node.decompose()
    for tag in soup.find_all(True):
        for attribute in list(tag.attrs):
            if attribute.lower().startswith('on'):
                del tag[attribute]
        if tag.get('href') and _unsafe_link(str(tag['href'])):
            del tag['href']


def prepare_html_document(
    soup: BeautifulSoup,
    path: str,
    assets: dict[str, AssetRef],
    load_resource: Callable[[str], tuple[bytes, str | None] | None],
    style_sources: DocumentStyleSources,
    styles: dict[str, str],
    diagnostics: list[Diagnostic],
    *, resource_base: str | None = None, document_paths: set[str] | None = None,
    resource_state: ResourceState | None = None,
) -> BeautifulSoup:
    resource_base = resource_base or path
    resource_state = resource_state if resource_state is not None else ResourceState()
    media = MediaFreezer(load_resource, lambda message: diagnostics.append(Diagnostic('svg_resource', message, severity='error', source_path=path)), resource_state)
    sanitize_html(soup)
    seen_anchors: set[str] = set()
    anchor_occurrences: dict[str, int] = defaultdict(int)
    anchor_map: dict[str, str] = {}
    for tag in list(soup.find_all(True)):
        try:
            encoded_aliases = json.loads(tag.get(SOURCE_ANCHORS_ATTRIBUTE) or "[]")
        except (ValueError, TypeError):
            encoded_aliases = []
        aliases = {value for value in encoded_aliases if isinstance(value, str)} if isinstance(encoded_aliases, list) else set()
        if tag.get("id"):
            tag["data-source-original-id"] = str(tag["id"])
        own = [str(tag[key]) for key in (("id", "name") if tag.name == 'a' else ("id",)) if tag.get(key)]
        for index, original in enumerate(dict.fromkeys(own)):
            anchor_occurrences[original] += 1
            canonical = source_anchor(path, original)
            anchor_map.setdefault(original, canonical)
            if original in seen_anchors:
                diagnostics.append(Diagnostic("duplicate_source_anchor", "同一源文件含重复锚点，引用定位到首次出现处。", source_path=path))
                canonical += "-" + str(anchor_occurrences[original])
            seen_anchors.add(original)
            if index == 0:
                tag["id"] = canonical
                # Link IDs remain unique, while this style identity survives DOM reopening.
                tag["data-source-element-id"] = anchor_map[original]
            else:
                tag.insert_before(soup.new_tag("a", id=canonical))
        tag.attrs.pop("name", None)
        tag.attrs.pop(SOURCE_ANCHORS_ATTRIBUTE, None)
        for original in sorted(aliases - set(own)):
            if original not in seen_anchors:
                canonical = source_anchor(path, original)
                anchor_map[original] = canonical
                tag.insert_before(soup.new_tag("a", id=canonical))
                seen_anchors.add(original)

    def resource(value: str, base: str = resource_base) -> str:
        parsed = urlsplit(value)
        if parsed.scheme.lower() == "data":
            return media.data_svg(value, base)
        if not parsed.scheme and not parsed.netloc and not parsed.path and parsed.fragment:
            return "#" + source_anchor(base, unquote(parsed.fragment))
        target = resolve_path(base, value)
        asset = load_resource(target) if target else None
        if asset is None:
            diagnostics.append(Diagnostic("missing_resource", f"资源无法离线获取：{value}", severity="error", source_path=base))
            return ""
        content = media.svg(asset[0], target) if asset[1] == 'image/svg+xml' else asset[0]
        token = register_asset(assets, target, content, asset[1])
        return token+('#'+parsed.fragment if parsed.fragment else '')

    resource_bases = {
        id(tag): element_resource_base(tag, resource_base)
        for tag in soup.find_all(True)
    }
    for tag in soup.find_all(True):
        if tag.name == "link":
            continue
        base = resource_bases[id(tag)]
        resolve_resource = lambda value: resource(value, base)
        for attribute in ("src", "poster", "xlink:href", *(("href",) if tag.name in {"image", "use"} else ())):
            if tag.get(attribute):
                tag[attribute] = resolve_resource(str(tag[attribute]))
        if tag.get("srcset"):
            tag["srcset"] = rewrite_srcset(str(tag["srcset"]), resolve_resource)
        if tag.get("href") and tag.name not in {"image", "use"}:
            href = str(tag["href"])
            if urlsplit(base).scheme or urlsplit(base).netloc:
                href = urljoin(base, href)
                tag['href'] = href
            parsed = urlsplit(href)
            if _unsafe_link(href):
                del tag["href"]
            elif document_paths is not None and resolve_path(base, href) in document_paths and 'external' not in tag.get('rel', []):
                tag['href'] = '#' + source_anchor(resolve_path(base, href), unquote(parsed.fragment))
            elif not parsed.scheme and not parsed.netloc:
                target = resolve_path(base, href)
                candidate = load_resource(target)
                is_document = target in document_paths if document_paths is not None else candidate is not None and candidate[1] == 'application/xhtml+xml'
                if candidate is not None and not is_document:
                    tag["href"] = resolve_resource(href)
                else:
                    tag["href"] = "#" + source_anchor(target, unquote(parsed.fragment))
        for attribute in ("style", "fill", "stroke", "filter", "clip-path", "mask", "marker-start", "marker-mid", "marker-end"):
            if tag.get(attribute):
                tag[attribute] = rewrite_declarations(str(tag[attribute]), resolve_resource)
        tag.attrs.pop('xml:base', None)

    scope = scope_id(path)
    css_parts: list[str] = []

    def style_diagnostic(message: str) -> None:
        diagnostics.append(Diagnostic("stylesheet_limit", message, source_path=path))

    def add_css(css: str, base: str, active: tuple[str, ...]) -> str:
        def load_import(value: str) -> str:
            parsed = urlsplit(value)
            target = resolve_path(base, value)
            if target in active:
                style_diagnostic(f"跳过循环样式导入：{target}")
                return ""
            stylesheet = load_resource(target)
            if stylesheet is None:
                diagnostics.append(Diagnostic("missing_stylesheet", f"样式文件无法离线获取：{value}", source_path=base))
                return ""
            return add_css(stylesheet[0].decode("utf-8", errors="replace"), target, (*active, target))

        try:
            if len(active) > MAX_STYLE_DEPTH:
                raise StyleLimit('样式导入超过深度限制。')
            return scope_styles(
                css, scope, lambda value: resource(value, base), anchor_map,
                load_import=load_import, report=style_diagnostic, budget=resource_state.styles,
            )
        except StyleLimit as error:
            diagnostics.append(Diagnostic('stylesheet_limit', str(error), severity='error', source_path=base))
            return ''

    for kind, value, media in style_sources.ordered_css:
        if kind == "inline":
            css = add_css(value, resource_base, ())
        else:
            stylesheet = load_resource(value)
            if stylesheet is not None:
                css = add_css(stylesheet[0].decode("utf-8", errors="replace"), value, (value,))
            else:
                diagnostics.append(Diagnostic("missing_stylesheet", f"样式文件不存在：{value}", source_path=path))
                continue
        css_parts.append(f"@media {media}{{{css}}}" if media else css)
    styles[scope] = "\n".join(css_parts)
    for node in soup.find_all(["style", "link"]):
        node.decompose()
    container = soup.body or soup
    if soup.html and soup.html.get("id"):
        container.insert(0, soup.new_tag("a", id=soup.html["id"]))
        del soup.html["id"]
    if container.get("id"):
        container.insert(0, soup.new_tag("a", id=container["id"]))
        del container["id"]
    container.insert(0, soup.new_tag("a", id=source_anchor(path)))
    return soup



def scoped_fragment(soup: BeautifulSoup, html: str, document_path: str) -> str:
    wrapper = BeautifulSoup("", "html.parser")
    container = soup.body or soup
    root_attrs = dict(soup.html.attrs) if soup.html else {}
    root_attrs.pop("id", None)
    root_attrs["data-source-html"] = ""
    scoped = wrapper.new_tag("div", attrs={"data-source-scope": scope_id(document_path)})
    source_html = wrapper.new_tag("any-to-md-html", attrs=root_attrs)
    body_attrs = dict(container.attrs) if isinstance(container, Tag) else {}
    body_attrs.pop("id", None)
    body_attrs["data-source-body"] = ""
    body = wrapper.new_tag("any-to-md-body", attrs=body_attrs)
    body.append(BeautifulSoup(html, "html.parser"))
    source_html.append(body)
    scoped.append(source_html)
    return str(scoped)


