"""Immutable HTML inputs with their referenced local resource context.

The archive retains original bytes; it is not a second conversion engine.
No archive is extracted into the filesystem, and replay never reads beside the
hashed stored input or fetches a network resource.
"""

from __future__ import annotations

import io
import json
import mimetypes
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit
from zipfile import ZIP_STORED, ZipFile, ZipInfo, is_zipfile

from bs4 import BeautifulSoup

from ..content.references import resolve_path, rewrite_srcset
from ..content.document import document_base, element_resource_base
from ..content.media import decode_svg_data
from ..content.styles import style_references
from .archive import member_name as _member_name, validate_archive

SOURCE_MANIFEST = 'any-to-md-source.json'
MAX_SOURCE_BYTES = 256 * 1024 * 1024
MAX_SOURCE_FILES = 10000


@dataclass(frozen=True)
class HtmlSource:
    entrypoint: str
    members: dict[str, bytes]

    def resource(self, name: str) -> tuple[bytes, str | None] | None:
        if name not in self.members:
            return None
        return self.members[name], mimetypes.guess_type(name)[0]


def read_html_source(path: Path, original_filename: str) -> HtmlSource:
    if not is_zipfile(path):
        # Previously stored single-file uploads remain readable by this reader.
        name = Path(original_filename).name
        return HtmlSource(name, {name: path.read_bytes()})
    with ZipFile(path) as archive:
        members = {name: archive.read(item) for name, item in validate_archive(archive, max_files=MAX_SOURCE_FILES, max_bytes=MAX_SOURCE_BYTES)}
    if SOURCE_MANIFEST in members:
        manifest = json.loads(members[SOURCE_MANIFEST])
        if manifest.get('format') != 'html-source-v1':
            raise ValueError('未知的 HTML 来源清单版本')
        entrypoint = _member_name(manifest['entrypoint'])
    else:
        html = [name for name in members if PurePosixPath(name).suffix.lower() in {'.html', '.htm'}]
        if len(html) == 1:
            entrypoint = html[0]
        elif 'index.html' in html:
            entrypoint = 'index.html'
        else:
            raise ValueError('HTML 资源包需要唯一 HTML 文件或根目录 index.html')
    if entrypoint not in members or PurePosixPath(entrypoint).suffix.lower() not in {'.html', '.htm'}:
        raise ValueError('HTML 来源清单没有有效的入口文件')
    return HtmlSource(entrypoint, members)


def _html_references(content: bytes, path: str, depth: int = 0) -> list[tuple[str, str]]:
    soup = BeautifulSoup(content, 'html.parser')
    references = []
    document = document_base(soup, path)
    for element in soup.find_all(True):
        if element.name in {'script', 'iframe', 'object', 'embed'}:
            continue
        base = element_resource_base(element, document)
        def collect(value):
            references.append((value, base))
            return value
        for attribute in ('src', 'poster', 'xlink:href', 'href'):
            if element.get(attribute) and element.name != 'base':
                collect(str(element[attribute]))
        if element.get('srcset'):
            rewrite_srcset(str(element['srcset']), collect)
        for attribute in ('style', 'fill', 'stroke', 'filter', 'clip-path', 'mask', 'marker-start', 'marker-mid', 'marker-end'):
            if element.get(attribute):
                references.extend((value, base) for value in style_references(str(element[attribute])))
        if element.name == 'style':
            references.extend((value, base) for value in style_references(element.get_text()))
    if depth < 32:
        for value, base in list(references):
            try:
                svg = decode_svg_data(value)
            except ValueError:
                continue  # Reader reports invalid SVG data with source context.
            if svg is not None:
                references.extend(_html_references(svg, base, depth+1))
    return references


def pack_html_source(filename: str, content: bytes, resources: dict[str, bytes] | None = None) -> bytes:
    name = _member_name(Path(filename).name)
    members = {**(resources or {}), name: content}
    if SOURCE_MANIFEST in members:
        raise ValueError('HTML 来源资源与保留清单名称冲突')
    members[SOURCE_MANIFEST] = json.dumps({'format': 'html-source-v1', 'entrypoint': name}, ensure_ascii=False, sort_keys=True).encode()
    if len(members) > MAX_SOURCE_FILES or sum(map(len, members.values())) > MAX_SOURCE_BYTES:
        raise ValueError('HTML 来源超出 10000 个文件或 256 MiB 大小限制')
    stream = io.BytesIO()
    with ZipFile(stream, 'w', compression=ZIP_STORED) as archive:
        for member, value in sorted(members.items()):
            archive.writestr(ZipInfo(_member_name(member), date_time=(2020, 1, 1, 0, 0, 0)), value)
    return stream.getvalue()


def capture_html_source(path: Path) -> bytes:
    """Freeze only referenced files within the document's local directory."""
    path = path.resolve()
    content = path.read_bytes()
    members = {path.name: content}
    pending = [path.name]
    while pending:
        name = pending.pop()
        suffix = PurePosixPath(name).suffix.lower()
        if suffix in {'.html', '.htm', '.svg'}:
            references = _html_references(members[name], name)
        elif suffix == '.css':
            references = [(value, name) for value in style_references(members[name].decode('utf-8', errors='replace'))]
        else:
            continue
        for value, base in references:
            parsed = urlsplit(value)
            if parsed.scheme or parsed.netloc or not parsed.path:
                continue
            target = resolve_path(base, value)
            if urlsplit(target).scheme or urlsplit(target).netloc:
                continue
            if target in members:
                continue
            candidate = (path.parent/target).resolve()
            if not candidate.is_relative_to(path.parent) or not candidate.is_file():
                continue  # Missing dependencies become explicit conversion diagnostics.
            if len(members) >= MAX_SOURCE_FILES or candidate.stat().st_size+sum(map(len, members.values())) > MAX_SOURCE_BYTES:
                raise ValueError('HTML 来源超出 10000 个文件或 256 MiB 大小限制')
            members[target] = candidate.read_bytes()
            pending.append(target)
    return pack_html_source(path.name, content, members)
