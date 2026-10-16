"""Freeze references inside SVG and data SVG into self-contained resources."""

from __future__ import annotations

import base64
from collections.abc import Callable
from dataclasses import dataclass, field
from urllib.parse import unquote_to_bytes, urljoin, urlsplit
from xml.etree import ElementTree as ET

from .references import resolve_path
from .styles import StyleBudget, rewrite_declarations, rewrite_stylesheet_resources

MAX_SVG_BYTES = 16 * 1024 * 1024
MAX_EXPANDED_BYTES = 64 * 1024 * 1024
EMPTY_SVG = b'<svg xmlns="http://www.w3.org/2000/svg"/>'


@dataclass
class ResourceState:
    """One revision's media/style budgets and immutable resource cache."""
    expanded_bytes: int = 0
    cache: dict[str, tuple[bytes, str | None]] = field(default_factory=dict)
    styles: StyleBudget = field(default_factory=StyleBudget)


def xml_resource_base(path: str, bases) -> str:
    for base in bases:
        if base:
            path = urljoin(path, str(base))
    return path


def decode_svg_data(value: str) -> bytes | None:
    """Decode SVG data once for both source capture and resource freezing."""
    source = value.partition('#')[0]
    header, separator, payload = source.partition(',')
    if not separator or header.split(';', 1)[0].lower() != 'data:image/svg+xml':
        return None
    encoded = unquote_to_bytes(payload)
    return base64.b64decode(encoded, validate=True) if header.lower().endswith(';base64') else encoded


class MediaFreezer:
    def __init__(self, load: Callable[[str], tuple[bytes, str | None] | None], report: Callable[[str], None], state: ResourceState | None = None):
        self.load = load
        self.report = report
        self.state = state if state is not None else ResourceState()
        self.cache = self.state.cache

    @property
    def expanded_bytes(self) -> int:
        return self.state.expanded_bytes

    def _reserve(self, size: int) -> bool:
        if size > MAX_SVG_BYTES or self.expanded_bytes+size > MAX_EXPANDED_BYTES:
            self.report('SVG 展开超过单资源 16 MiB 或累计 64 MiB 大小限制。')
            return False
        self.state.expanded_bytes += size
        return True

    def svg(self, content: bytes, path: str, active: tuple[str, ...] = ()) -> bytes:
        if len(content) > MAX_SVG_BYTES:
            self.report(f'SVG 原资源超过 16 MiB 大小限制：{path}')
            return EMPTY_SVG
        try:
            root = ET.fromstring(content)
        except ET.ParseError:
            self.report(f'SVG 不是有效 XML：{path}')
            return EMPTY_SVG
        def prepare(parent, base):
            base = xml_resource_base(base, [parent.attrib.get('{http://www.w3.org/XML/1998/namespace}base')])
            for child in list(parent):
                if child.tag.split('}')[-1].lower() in {'script', 'foreignobject'}:
                    parent.remove(child)
                    if child.tag.split('}')[-1].lower() == 'foreignobject':
                        self.report(f'SVG foreignObject 内容不能安全跨格式保留，已移除：{path}')
            for attribute in list(parent.attrib):
                local = attribute.split('}')[-1].lower()
                value = parent.attrib[attribute]
                if local.startswith('on'):
                    del parent.attrib[attribute]
                elif local in {'href', 'src'} and not value.startswith('#'):
                    resolved = self.embed(value, base, (*active, path))
                    if resolved:
                        parent.attrib[attribute] = resolved
                    else:
                        del parent.attrib[attribute]
                elif local == 'base':
                    del parent.attrib[attribute]
                elif local in {'style', 'fill', 'stroke', 'filter', 'clip-path', 'mask', 'marker-start', 'marker-mid', 'marker-end'}:
                    parent.attrib[attribute] = rewrite_declarations(value, lambda value: value if value.startswith('#') else self.embed(value, base, (*active, path)))
            if parent.tag.split('}')[-1].lower() == 'style' and parent.text:
                parent.text = rewrite_stylesheet_resources(parent.text, lambda value: value if value.startswith('#') else self.embed(value, base, (*active, path)))
            for child in list(parent):
                prepare(child, base)
                if child.tag.split('}')[-1].lower() in {'image', 'feimage', 'use'} and not any(
                    attribute.split('}')[-1].lower() in {'href', 'src'} and value
                    for attribute, value in child.attrib.items()
                ):
                    parent.remove(child)
                    self.report(f'SVG 图像或引用缺少有效资源，已移除：{path}')
        prepare(root, path)
        # Every embedded data string was reserved before allocation. Bound the
        # final XML as well, including repeated cached references and escaping.
        estimated = sum(len(str(element.tag))+sum(len(key)+len(value) for key, value in element.attrib.items())+len(element.text or '')+len(element.tail or '')+32 for element in root.iter())
        if not self._reserve(estimated):
            return EMPTY_SVG
        result = ET.tostring(root, encoding='utf-8')
        if len(result) > MAX_SVG_BYTES:
            self.report(f'SVG 序列化超过 16 MiB 大小限制：{path}')
            return EMPTY_SVG
        return result

    def data_svg(self, value: str, base: str, active: tuple[str, ...] = ()) -> str:
        if len(active) > 32:
            self.report('SVG 内嵌资源超过 32 层，未继续展开。')
            return ''
        try:
            content = decode_svg_data(value)
        except ValueError:
            self.report('SVG data URL 编码无效。')
            return ''
        if content is None:
            return value
        _, marker, fragment = value.partition('#')
        return self._data(self.svg(content, base, (*active, 'data:')), 'image/svg+xml')+('#'+fragment if marker else '')

    def _data(self, content: bytes, media_type: str | None) -> str:
        header = 'data:'+(media_type or 'application/octet-stream')+';base64,'
        if not self._reserve(len(header)+4*((len(content)+2)//3)):
            return ''
        return header+base64.b64encode(content).decode('ascii')

    def embed(self, value: str, base: str, active: tuple[str, ...] = ()) -> str:
        parsed = urlsplit(value)
        if not parsed.scheme and not parsed.netloc and not parsed.path and parsed.fragment:
            return value
        if parsed.scheme.lower() == 'data':
            return self.data_svg(value, base, active)
        if (parsed.scheme or parsed.netloc) and urlsplit(base).scheme not in {'http', 'https'}:
            self.report(f'SVG 资源不能离线读取：{value}')
            return ''
        target = resolve_path(base, value)
        if target in active or len(active) > 32:
            self.report(f'SVG/CSS 资源存在循环或超过 32 层：{target}')
            return ''
        if target not in self.cache:
            resource = self.load(target)
            if resource is None:
                self.report(f'SVG 资源缺失：{target}')
                return ''
            content, media_type = resource
            if media_type == 'image/svg+xml':
                content = self.svg(content, target, active)
            elif media_type == 'text/css':
                css = rewrite_stylesheet_resources(content.decode('utf-8', errors='replace'), lambda value: self.embed(value, target, (*active, target)))
                content = css.encode('utf-8')
            self.cache[target] = (content, media_type)
        return self._data(*self.cache[target])+('#'+parsed.fragment if parsed.fragment else '')
