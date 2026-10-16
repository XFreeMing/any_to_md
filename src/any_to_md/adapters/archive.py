"""Validate bounded source archives without extracting files."""

from __future__ import annotations

import stat
from pathlib import Path, PurePosixPath
from zipfile import ZipFile, ZipInfo

MAX_ARCHIVE_BYTES = 256 * 1024 * 1024
MAX_ARCHIVE_FILES = 10000


def member_name(name: str) -> str:
    path = PurePosixPath(name)
    if not name or not path.parts or '\\' in name or path.is_absolute() or '..' in path.parts or ':' in path.parts[0]:
        raise ValueError(f'来源资源包包含无效路径：{name}')
    return str(path)


def validate_archive(archive: ZipFile, *, max_files: int = MAX_ARCHIVE_FILES, max_bytes: int = MAX_ARCHIVE_BYTES) -> list[tuple[str, ZipInfo]]:
    files = [item for item in archive.infolist() if not item.is_dir()]
    if len(files) > max_files or sum(item.file_size for item in files) > max_bytes:
        raise ValueError(f'来源资源包超出文件数或展开大小限制：{max_files} 个文件 / {max_bytes} 字节')
    result, seen = [], set()
    for item in files:
        name = member_name(item.filename)
        if name in seen or stat.S_ISLNK(item.external_attr >> 16):
            raise ValueError(f'来源资源包包含重复路径或符号链接：{name}')
        seen.add(name)
        result.append((name, item))
    return result


def write_members(root: Path, members: dict[str, bytes]) -> None:
    """Materialize validated members without allowing preexisting symlink escape."""
    root.mkdir(parents=True, exist_ok=True)
    base = root.resolve()
    paths = [(root/member_name(name), content) for name, content in members.items()]
    names = [member_name(name).casefold() for name in members]
    if len(names) != len(set(names)):
        raise ValueError('来源资源文件名在不区分大小写的文件系统中冲突')
    if any(not path.resolve().is_relative_to(base) for path, _ in paths):
        raise ValueError('来源资源输出路径越界')
    for path, content in paths:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
