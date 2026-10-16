"""Portable download selection and packaging before immutable publication."""

from __future__ import annotations

import json
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from .files import artifact_role


def _selection(paths: set[str], output_format: str, partial: bool, views: dict[str, str]) -> tuple[str, set[str]]:
    primary = views.get(output_format)
    if primary is None or primary not in paths:
        raise ValueError(f"此导出不包含请求的视图：{output_format}")
    assets = {path for path in paths if artifact_role(path) == "asset"}
    if output_format != "md_chapters" and not partial and (output_format == "pdf" or not assets):
        return primary, set()
    if output_format == "md_chapters":
        selected = {path for path in paths if path.endswith(".md")} | assets
        selected |= {"locations.json"} & paths
    else:
        selected = {primary} | (assets if output_format != "pdf" else set())
    selected |= {"report.json"} & paths
    return f"{output_format}.zip", selected


def write_downloads(root: Path, partial: bool, views: dict[str, str]) -> None:
    paths = {path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file()}
    for output_format in views:
        relative, selected = _selection(paths, output_format, partial, views)
        if not selected:
            continue
        with ZipFile(root / relative, "w", compression=ZIP_DEFLATED) as archive:
            for name in sorted(selected):
                path = (root / name).resolve()
                if not path.is_relative_to(root.resolve()) or not path.is_file():
                    raise ValueError(f"无效产物路径：{name}")
                archive.write(path, name)


def prepare_download(root: Path, output_format: str) -> Path:
    """Resolve an already published artifact; never mutate the export directory."""
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    report = json.loads((root / "report.json").read_text(encoding="utf-8"))
    paths = {item["path"] for item in manifest["files"]}
    views = manifest.get('views')
    if views is None:
        raise ValueError('历史导出没有下载视图，请从保留的原件创建新导出')
    relative, _ = _selection(paths, output_format, report["status"] == "partial", views)
    if relative not in paths or not (root / relative).is_file():
        raise ValueError(f"下载产物未发布：{relative}")
    return root / relative
