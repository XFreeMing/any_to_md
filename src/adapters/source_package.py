"""Deterministic immutable source packages for entrypoint-plus-resources inputs.

The archive keeps the original bytes of the entrypoint and of every captured
local resource. It is one packaging mechanism shared by HTML and Markdown; it
does not interpret either format. Paths inside the package are POSIX relative
names validated by :func:`archive.member_name`, and the archive is written with
fixed timestamps so the same input set always produces the same bytes.
"""

from __future__ import annotations

import io
import json
from pathlib import Path
from zipfile import ZIP_STORED, ZipFile, ZipInfo

from .archive import member_name, validate_archive

SOURCE_MANIFEST = 'any-to-md-source.json'
MAX_SOURCE_BYTES = 256 * 1024 * 1024
MAX_SOURCE_FILES = 10000
PACKAGE_FORMATS = ('html-source-v1', 'markdown-source-v1')
ENTRYPOINT_SUFFIXES = {
    'html-source-v1': {'.html', '.htm'},
    'markdown-source-v1': {'.md', '.markdown'},
}


def pack_source(format_name: str, filename: str, content: bytes, resources: dict[str, bytes] | None = None) -> bytes:
    """Build one deterministic package; identical inputs produce identical bytes."""
    if format_name not in PACKAGE_FORMATS:
        raise ValueError(f'未知的来源包格式：{format_name}')
    name = member_name(Path(filename).name)
    members: dict[str, bytes] = {}
    for path, value in (resources or {}).items():
        members[member_name(path)] = value
    if name in members and members[name] != content:
        raise ValueError(f'来源资源与入口文件路径冲突：{name}')
    members[name] = content
    if SOURCE_MANIFEST in members:
        raise ValueError('来源资源与保留清单名称冲突')
    members[SOURCE_MANIFEST] = json.dumps(
        {'format': format_name, 'entrypoint': name}, ensure_ascii=False, sort_keys=True
    ).encode()
    if len(members) > MAX_SOURCE_FILES or sum(map(len, members.values())) > MAX_SOURCE_BYTES:
        raise ValueError('来源包超出 10000 个文件或 256 MiB 大小限制')
    stream = io.BytesIO()
    with ZipFile(stream, 'w', compression=ZIP_STORED) as archive:
        for member, value in sorted(members.items()):
            archive.writestr(ZipInfo(member_name(member), date_time=(2020, 1, 1, 0, 0, 0)), value)
    return stream.getvalue()


def read_package(path: Path) -> tuple[str, dict[str, bytes]]:
    """Return ``(format, members)`` after validating names, size and uniqueness."""
    with ZipFile(path) as archive:
        members = {
            name: archive.read(item)
            for name, item in validate_archive(
                archive, max_files=MAX_SOURCE_FILES, max_bytes=MAX_SOURCE_BYTES
            )
        }
    if SOURCE_MANIFEST not in members:
        raise ValueError('来源包缺少清单 any-to-md-source.json')
    manifest = json.loads(members[SOURCE_MANIFEST])
    format_name = manifest.get('format') if isinstance(manifest, dict) else None
    if format_name not in PACKAGE_FORMATS:
        raise ValueError(f'未知的来源包格式：{format_name}')
    entrypoint = member_name(str(manifest.get('entrypoint') or ''))
    if entrypoint not in members:
        raise ValueError('来源包清单没有有效的入口文件')
    if Path(entrypoint).suffix.lower() not in ENTRYPOINT_SUFFIXES[format_name]:
        raise ValueError('来源包入口文件类型与清单格式不一致')
    return format_name, members


def resources_fingerprint(resources: dict[str, bytes]) -> str | None:
    """Snapshot identity of normalized relative paths and file digests."""
    import hashlib

    if not resources:
        return None
    digest = hashlib.sha256()
    for path, value in sorted(resources.items()):
        digest.update(member_name(path).encode('utf-8'))
        digest.update(b'\0')
        digest.update(hashlib.sha256(value).hexdigest().encode('ascii'))
        digest.update(b'\0')
    return digest.hexdigest()