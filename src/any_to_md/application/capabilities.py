"""One input/output capability table; it does not select competing engines."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from importlib import import_module


@dataclass(frozen=True)
class Capability:
    suffixes: tuple[str, ...]
    outputs: tuple[str, ...]
    reader: str | None = None

    def load_reader(self):
        if self.reader is None:
            raise ValueError('此格式尚未接入共同 Reader')
        module_name, function = self.reader.split(':')
        module = import_module(module_name)
        return getattr(module, function), module.PIPELINE_VERSION


CAPABILITIES = {
    "epub": Capability((".epub",), ("md_single", "md_chapters", "html", "pdf"), 'any_to_md.readers.epub:read_epub_book'),
    "html": Capability((".html", ".htm", ".zip"), ("md_single", "md_chapters", "html", "pdf"), 'any_to_md.readers.html:read_html_document'),
    "docx": Capability((".docx",), ("md_single", "md_chapters", "html", "pdf"), 'any_to_md.readers.docx:read_docx_document'),
    "pdf": Capability((".pdf",), ("md_single", "html", "pdf")),
    "image": Capability((".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"), ("md_single", "html", "pdf")),
    "url": Capability((".url",), ("md_single", "html", "pdf"), 'any_to_md.readers.url:read_url_document'),
}
OUTPUT_TARGET = {"md_single": "markdown", "md_chapters": "markdown", "html": "html", "pdf": "pdf"}


def input_format(filename: str) -> str | None:
    suffix = Path(filename).suffix.lower()
    return next((name for name, capability in CAPABILITIES.items() if suffix in capability.suffixes), None)


def validate_outputs(format_name: str, targets: tuple[str, ...], output_format: str | None = None) -> None:
    capability = CAPABILITIES.get(format_name)
    if capability is None:
        raise ValueError(f"不支持的输入格式：{format_name}")
    available = {OUTPUT_TARGET[value] for value in capability.outputs}
    if not targets or len(targets) != len(set(targets)) or set(targets) - available:
        raise ValueError(f"{format_name} 不支持请求的输出：{targets}")
    if output_format is not None and (output_format not in capability.outputs or OUTPUT_TARGET[output_format] not in targets):
        raise ValueError(f"{format_name} 不支持请求的下载视图：{output_format}")
