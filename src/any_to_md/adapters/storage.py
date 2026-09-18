"""Local immutable source and export storage."""

from __future__ import annotations

import os
import shutil
import uuid
import hashlib
from pathlib import Path


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
        suffix = source_path.suffix.lower() or ".bin"
        destination = self.sources_dir / f"{sha256}{suffix}"
        if not destination.exists():
            temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
            shutil.copyfile(source_path, temporary)
            os.replace(temporary, destination)
        return destination, destination.relative_to(self.root).as_posix()

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

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                digest.update(chunk)
        return digest.hexdigest()

    def preserve_revision(self, revision_id: str, published_dir: Path) -> str:
        destination_dir = self.revisions_dir / revision_id
        destination = destination_dir / "book.json"
        source = published_dir / "book.json"
        source_assets = published_dir / "assets"
        if destination_dir.exists():
            if not destination.is_file() or self._sha256(destination) != self._sha256(source):
                raise ValueError(f"同一 revision 的快照内容不一致：{revision_id}")
            for asset in source_assets.iterdir():
                existing = destination_dir / "assets" / asset.name
                if not existing.is_file() or self._sha256(existing) != self._sha256(asset):
                    raise ValueError(f"同一 revision 的资源内容不一致：{asset.name}")
            return destination.relative_to(self.root).as_posix()

        temporary_dir = self.revisions_dir / f".{revision_id}.{uuid.uuid4().hex}.tmp"
        temporary_dir.mkdir(parents=True, exist_ok=False)
        shutil.copyfile(source, temporary_dir / "book.json")
        if source_assets.exists():
            shutil.copytree(source_assets, temporary_dir / "assets")
        os.replace(temporary_dir, destination_dir)
        return destination.relative_to(self.root).as_posix()
