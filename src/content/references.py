"""Canonical resource and source-anchor identifiers used by all formats."""

from __future__ import annotations

import hashlib
import posixpath
import re
from pathlib import PurePosixPath
from urllib.parse import unquote, urlsplit, urljoin
from collections.abc import Callable

from ..domain.book import AssetRef


def asset_token(asset_id: str) -> str:
    return f"asset://{asset_id}"


def source_anchor(path: str, fragment: str = "") -> str:
    return "src-" + hashlib.sha256(f"{path}#{fragment}".encode()).hexdigest()[:24]


def scope_id(path: str) -> str:
    return "doc-" + hashlib.sha256(path.encode()).hexdigest()[:16]


def resolve_path(document_path: str, target: str) -> str:
    parsed = urlsplit(target.replace("\\", "/"))
    if urlsplit(document_path).scheme in {'http', 'https'}:
        return urljoin(document_path, target).split('#', 1)[0]
    if parsed.scheme or parsed.netloc:
        return ""
    if urlsplit(document_path).scheme or urlsplit(document_path).netloc:
        return urljoin(document_path, target)
    if not parsed.path:
        return document_path
    path = unquote(parsed.path)
    return posixpath.normpath(posixpath.join(posixpath.dirname(document_path), path)).lstrip('/')


def register_asset(assets: dict[str, AssetRef], path: str, data: bytes, media_type: str | None) -> str:
    digest = hashlib.sha256(data).hexdigest()
    suffix = re.sub(r"[^a-z0-9.]", "", PurePosixPath(urlsplit(path).path).suffix.lower())
    assets.setdefault(digest, AssetRef(digest, path, f"{digest}{suffix}", media_type, data))
    return asset_token(digest)


def rewrite_srcset(value: str, resolver: Callable[[str], str]) -> str:
    """Rewrite URL tokens without splitting data-URL commas or descriptors.

    Token boundaries follow WHATWG's srcset splitting/descriptor tokenizer:
    https://html.spec.whatwg.org/multipage/images.html#parse-a-srcset-attribute
    Descriptors stay unchanged; image selection remains the renderer's job.
    """
    candidates = []
    position = 0
    whitespace = ' \t\n\r\f'
    while position < len(value):
        while position < len(value) and value[position] in whitespace+',':
            position += 1
        start = position
        while position < len(value) and value[position] not in whitespace:
            position += 1
        url = value[start:position]
        if not url:
            break
        descriptor = ''
        if url.endswith(','):
            url = url.rstrip(',')
        else:
            start, parentheses = position, 0
            while position < len(value):
                character = value[position]
                if character == ',' and not parentheses:
                    break
                if character == '(':
                    parentheses += 1
                elif character == ')':
                    parentheses = max(0, parentheses-1)
                position += 1
            descriptor = value[start:position].strip()
            position += 1
        resolved = resolver(url)
        if resolved:
            candidates.append(resolved+(' '+descriptor if descriptor else ''))
    return ', '.join(candidates)
