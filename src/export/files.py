"""Shared artifact roles, JSON output and file checksums."""

import json
import mimetypes
import os
import uuid
from pathlib import Path

from ..adapters.storage import sha256_file
from ..domain.artifacts import ArtifactRecord


def write_json(path: Path, value) -> None:
    temporary = path.with_name(f'.{path.name}.{uuid.uuid4().hex}.tmp')
    try:
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def artifact_role(path: str) -> str:
    if path == "book.md":
        return "markdown_single"
    if path == "book.html":
        return "html"
    if path == "book.pdf":
        return "pdf"
    if path.endswith(".zip"):
        return "download_bundle"
    if path == "TOC.md":
        return "human_toc"
    if path.startswith("chapters/"):
        return "markdown_chapter"
    if path.startswith(("assets/", "images/")) or Path(path).suffix.lower() in {".css", ".ttf", ".otf", ".ttc", ".woff", ".woff2"}:
        return "asset"
    return {"report.json": "report", "manifest.json": "manifest"}.get(path, "metadata")


def file_record(path: Path, root: Path) -> ArtifactRecord:
    relative = path.relative_to(root).as_posix()
    media_type = {".md": "text/markdown", ".json": "application/json"}.get(path.suffix, mimetypes.guess_type(path.name)[0] or "application/octet-stream")
    return ArtifactRecord(artifact_role(relative), relative, sha256_file(path), path.stat().st_size, media_type)


