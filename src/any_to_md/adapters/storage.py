"""Local immutable source and export storage."""

from __future__ import annotations

import hashlib
import errno
import os
import re
import shutil
import uuid
from pathlib import Path
from collections.abc import Callable

from ..export.naming import title_slug


def sha256_file(path: Path) -> str:
    """Hash a stored file without loading large artifacts into memory."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


class LocalStorage:
    def __init__(self, data_dir: Path):
        self.root = data_dir.resolve()
        self.sources_dir = self.root / "sources"
        self.revisions_dir = self.root / "revisions"
        self.staging_dir = self.root / "staging"
        self.exports_dir = self.root / "exports"
        for directory in (
            self.root,
            self.sources_dir,
            self.revisions_dir,
            self.staging_dir,
            self.exports_dir,
        ):
            directory.mkdir(parents=True, exist_ok=True)

    def store_source(self, source_path: Path, sha256: str) -> tuple[Path, str]:
        return self._store(source_path.suffix, sha256, lambda temporary: shutil.copyfile(source_path, temporary))

    def store_bytes(self, content: bytes, suffix: str) -> tuple[Path, str, str]:
        digest = hashlib.sha256(content).hexdigest()
        path, key = self._store(suffix, digest, lambda temporary: temporary.write_bytes(content))
        return path, key, digest

    def _store(self, suffix: str, sha256: str, write: Callable[[Path], object]) -> tuple[Path, str]:
        if not re.fullmatch(r'[0-9a-f]{64}', sha256):
            raise ValueError('无效的来源哈希')
        suffix = suffix.lower() or '.bin'
        if not re.fullmatch(r'\.[a-z0-9]{1,12}', suffix):
            raise ValueError('无效的来源扩展名')
        destination = self.sources_dir / f'{sha256}{suffix}'
        if not destination.exists():
            temporary = destination.with_name(f'.{destination.name}.{uuid.uuid4().hex}.tmp')
            try:
                write(temporary)
                if sha256_file(temporary) != sha256:
                    raise ValueError('读取时来源内容发生变化')
                try:
                    os.link(temporary, destination)
                except FileExistsError:
                    pass  # Another writer stored the same content; verify below.
            finally:
                temporary.unlink(missing_ok=True)
        if destination.is_symlink() or sha256_file(destination) != sha256:
            raise ValueError('已保存原件的校验和不一致')
        return destination, destination.relative_to(self.root).as_posix()

    def resolve(self, key: str) -> Path:
        relative = Path(key)
        if relative.is_absolute() or '..' in relative.parts:
            raise ValueError('存储键必须是根目录内的相对路径')
        path = (self.root / relative).resolve()
        if not path.is_relative_to(self.root):
            raise ValueError('存储键越界')
        return path

    def diagnostic_path(self, job_id: str) -> Path:
        path = self.root / 'jobs' / str(uuid.UUID(job_id)) / 'report.json'
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def output_path(self, root: Path, filename: str) -> Path:
        suffix = Path(filename).suffix.lower()
        if not re.fullmatch(r'\.[a-z0-9]{1,12}', suffix):
            raise ValueError('无效的输出扩展名')
        stem = filename[:-len(suffix)]
        path = root / (title_slug(stem) + suffix)
        if not path.resolve().is_relative_to(self.root):
            raise ValueError('输出路径越界')
        return path

    def create_staging(self, export_id: str) -> Path:
        destination = self.staging_dir / export_id
        destination.mkdir(parents=True, exist_ok=False)
        return destination

    def publish(self, export_id: str) -> Path:
        source = self.staging_dir / export_id
        destination = self.exports_dir / export_id
        if destination.exists():
            raise FileExistsError(f"导出目录已经存在：{destination}")
        os.replace(source, destination)
        return destination

    def rollback_publish(self, export_id: str) -> Path:
        source = self.exports_dir / export_id
        destination = self.staging_dir / export_id
        if destination.exists():
            raise FileExistsError(f"无法隔离未完成导出，暂存目录已存在：{destination}")
        os.replace(source, destination)
        return destination

    def preserve_revision(self, revision_id: str, published_dir: Path) -> str:
        destination_dir = self.revisions_dir / revision_id
        destination = destination_dir / "book.json"
        source = published_dir / "book.json"
        source_assets = published_dir / "assets"

        def verify_existing() -> None:
            if not destination.is_file() or sha256_file(destination) != sha256_file(source):
                raise ValueError(f"同一 revision 的快照内容不一致：{revision_id}")
            for asset in source_assets.iterdir() if source_assets.exists() else ():
                existing = destination_dir / "assets" / asset.name
                if not existing.is_file() or sha256_file(existing) != sha256_file(asset):
                    raise ValueError(f"同一 revision 的资源内容不一致：{asset.name}")

        if destination_dir.exists():
            verify_existing()
            return destination.relative_to(self.root).as_posix()

        temporary_dir = self.revisions_dir / f".{revision_id}.{uuid.uuid4().hex}.tmp"
        temporary_dir.mkdir(parents=True, exist_ok=False)
        try:
            shutil.copyfile(source, temporary_dir / "book.json")
            if source_assets.exists():
                shutil.copytree(source_assets, temporary_dir / "assets")
            try:
                os.rename(temporary_dir, destination_dir)
            except OSError as error:
                if error.errno not in {errno.EEXIST, errno.ENOTEMPTY}:
                    raise
                verify_existing()
        finally:
            if temporary_dir.exists():
                shutil.rmtree(temporary_dir)
        return destination.relative_to(self.root).as_posix()
