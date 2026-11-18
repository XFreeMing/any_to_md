"""Markdown entrypoints with the local images their body actually references.

The package keeps the original Markdown bytes and the referenced local image
bytes; it does not interpret either. Only relative resources inside the entry
file's directory are captured. Absolute paths, traversal, symlinks and URL
references never enter a package, and unreferenced files are ignored.
"""

from __future__ import annotations

import mimetypes
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlsplit
from zipfile import is_zipfile

from ..content.references import resolve_path
from .source_package import (
    MAX_SOURCE_BYTES,
    MAX_SOURCE_FILES,
    pack_source,
    read_package,
)

MARKDOWN_MANIFEST_FORMAT = 'markdown-source-v1'
_IMAGE = re.compile(r"!\[[^\]]*\]\(\s*<?([^)\s>]+)>?(?:\s+['\"][^'\"]*['\"])?\s*\)")
_HTML_IMAGE = re.compile(r"<img\b[^>]*\bsrc\s*=\s*['\"]([^'\"]+)['\"]", re.IGNORECASE)


@dataclass(frozen=True)
class MarkdownSource:
    entrypoint: str
    members: dict[str, bytes]

    def resource(self, name: str) -> tuple[bytes, str | None] | None:
        if name not in self.members:
            return None
        return self.members[name], mimetypes.guess_type(name)[0]


def _invalid(raw: str) -> ValueError:
    return ValueError(f'Markdown 来源包含无效的资源路径：{raw}')


def normalize_resource_path(raw: str) -> str | None:
    """Return the normalized relative path, or None for a non-local reference.

    Raises ``ValueError`` for an absolute path, traversal or another form that
    must never be read from the filesystem; such input is a caller error, not
    a missing image.
    """
    value = raw.strip()
    if not value:
        return None
    parsed = urlsplit(value.replace('\\', '/'))
    if parsed.scheme or parsed.netloc:
        return None  # External and data references are never fetched.
    path = unquote(parsed.path)
    if not path:
        return None
    if path.startswith('/') or PurePosixPath(path).is_absolute() or ':' in PurePosixPath(path).parts[0]:
        raise _invalid(raw)
    if '..' in PurePosixPath(path).parts:
        raise _invalid(raw)
    return resolve_path('entry.md', path)


def referenced_images(content: bytes) -> list[str]:
    text = content.decode('utf-8', errors='replace')
    found: list[str] = []
    for pattern in (_IMAGE, _HTML_IMAGE):
        for match in pattern.finditer(text):
            found.append(match.group(1))
    return found


def capture_resources(entry: Path, content: bytes | None = None) -> dict[str, bytes]:
    """Read only the local images the body references, beside the entry file.

    The entrypoint's own name is reserved; a reference resolving to it is a
    caller error. Missing referenced files are not fatal here: the Reader
    reports them as a partial-extraction diagnostic.
    """
    entry = entry.resolve()
    base = entry.parent
    raw = entry.read_bytes() if content is None else content
    resources: dict[str, bytes] = {}
    for reference in referenced_images(raw):
        target = normalize_resource_path(reference)
        if target is None:
            continue
        if target == entry.name:
            raise _invalid(reference)
        candidate = base / target
        if candidate.is_symlink():
            raise _invalid(reference)
        candidate = candidate.resolve()
        if not candidate.is_relative_to(base):
            raise _invalid(reference)
        if not candidate.is_file():
            continue
        if len(resources) >= MAX_SOURCE_FILES or candidate.stat().st_size + sum(map(len, resources.values())) > MAX_SOURCE_BYTES:
            raise ValueError('Markdown 来源超出 10000 个文件或 256 MiB 大小限制')
        resources[target] = candidate.read_bytes()
    return resources


def pack_markdown_source(filename: str, content: bytes, resources: dict[str, bytes] | None = None) -> bytes:
    return pack_source(MARKDOWN_MANIFEST_FORMAT, filename, content, resources)


def read_markdown_source(path: Path) -> MarkdownSource:
    if not is_zipfile(path):
        # A bare Markdown file remains readable if one was stored directly.
        name = path.name
        if PurePosixPath(name).suffix.lower() not in {'.md', '.markdown'}:
            raise ValueError('Markdown 来源入口类型无效')
        return MarkdownSource(name, {name: path.read_bytes()})
    format_name, members = read_package(path)
    if format_name != MARKDOWN_MANIFEST_FORMAT:
        raise ValueError('来源包不是 Markdown 版本')
    import json

    manifest = json.loads(members['any-to-md-source.json'])
    return MarkdownSource(str(manifest['entrypoint']), members)