"""File management for uploads and outputs."""

import shutil
import time
from pathlib import Path
from typing import Optional

from ..config import settings


class FileManager:
    """Manages file storage for conversion tasks."""

    def __init__(self, task_id: str):
        self.task_id = task_id
        self.upload_dir = settings.uploads_path / task_id
        self.output_dir = settings.outputs_path / task_id

    def ensure_dirs(self):
        """Create task directories."""
        self.upload_dir.mkdir(parents=True, exist_ok=True)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def save_upload(self, filename: str, content: bytes) -> Path:
        """Save uploaded file."""
        self.ensure_dirs()
        file_path = self.upload_dir / filename
        file_path.write_bytes(content)
        return file_path

    def get_upload_path(self, filename: str) -> Path:
        """Get path to uploaded file."""
        return self.upload_dir / filename

    def get_output_path(self, filename: str) -> Path:
        """Get path for output file."""
        self.output_dir.mkdir(parents=True, exist_ok=True)
        return self.output_dir / filename

    def list_outputs(self) -> list[Path]:
        """List all output files."""
        if not self.output_dir.exists():
            return []
        return list(self.output_dir.iterdir())

    def cleanup(self):
        """Remove all task files."""
        if self.upload_dir.exists():
            shutil.rmtree(self.upload_dir)
        if self.output_dir.exists():
            shutil.rmtree(self.output_dir)


def cleanup_expired_files():
    """Clean up files older than FILE_EXPIRE_SECONDS."""
    now = time.time()
    expire_seconds = settings.FILE_EXPIRE_SECONDS

    for base_dir in [settings.uploads_path, settings.outputs_path]:
        if not base_dir.exists():
            continue

        for task_dir in base_dir.iterdir():
            if not task_dir.is_dir():
                continue

            # Check directory age by modification time
            try:
                mtime = task_dir.stat().st_mtime
                if now - mtime > expire_seconds:
                    shutil.rmtree(task_dir)
            except OSError:
                pass


def get_input_format(filename: str) -> Optional[str]:
    """Detect input format from filename."""
    suffix = Path(filename).suffix.lower()
    format_map = {
        ".epub": "epub",
        ".pdf": "pdf",
        ".docx": "docx",
        ".doc": "docx",
        ".html": "html",
        ".htm": "html",
        ".png": "image",
        ".jpg": "image",
        ".jpeg": "image",
        ".gif": "image",
        ".bmp": "image",
        ".webp": "image",
    }
    return format_map.get(suffix)


def get_available_outputs(input_format: str) -> list[str]:
    """Get available output formats for given input format."""
    output_map = {
        "epub": ["md_single", "md_chapters", "html", "pdf"],
        "pdf": ["md_single", "html", "pdf"],
        "docx": ["md_single", "html", "pdf"],
        "html": ["md_single", "pdf"],
        "image": ["md_single", "html", "pdf"],
        "url": ["md_single", "html", "pdf"],
    }
    return output_map.get(input_format, ["md_single"])
