"""Pending PDF/image cutover: source materialization around the sole provider."""

import asyncio
from pathlib import Path

from src.adapters.archive import write_members
from src.adapters.ocr import OcrConfig, recognize


def _config(api_url: str | None, timeout: float | None) -> OcrConfig:
    # The remaining old interface owns settings injection; the provider has no
    # knowledge of Web configuration. This interface exits with Reader cutover.
    from web.config import settings
    return OcrConfig(api_url if api_url is not None else settings.OCR_API_URL,
                     timeout if timeout is not None else settings.OCR_TIMEOUT)


async def convert_with_ocr(file_path: Path | str, output_dir: Path | str,
                           api_url: str | None = None, timeout: float | None = None) -> tuple[Path, Path | None]:
    result = await asyncio.to_thread(recognize, Path(file_path), _config(api_url, timeout))
    if any(name.casefold() == '.ocr-response.zip' for name in result.resources):
        raise ValueError('OCR 资源与保留响应名称冲突')
    root = Path(output_dir)
    await asyncio.to_thread(write_members, root, {
        **result.resources, result.markdown_path: result.markdown.encode('utf-8'),
        '.ocr-response.zip': result.raw_response,
    })
    markdown = root/result.markdown_path
    images = markdown.parent/'images'
    return markdown, images if images.is_dir() else None
