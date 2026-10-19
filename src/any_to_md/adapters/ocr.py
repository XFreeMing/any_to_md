"""The existing OCR ZIP protocol, with injectable transport and bounded parsing.

The provider returns source content and resources only. It never publishes an
export, imports Web configuration, or assumes page-level service capability.
"""

from dataclasses import dataclass
import io
import math
from pathlib import Path
from zipfile import ZipFile

import httpx

from .archive import validate_archive
from .http import read_response_body
from ..domain.book import Diagnostic

MAX_OCR_RESPONSE_BYTES = 100 * 1024 * 1024


@dataclass(frozen=True)
class OcrConfig:
    api_url: str
    timeout: float

    def __post_init__(self):
        url = httpx.URL(self.api_url)
        if url.scheme not in {'http', 'https'} or not url.host or url.username or url.password:
            raise ValueError('OCR 服务需要无凭据的 HTTP/HTTPS 地址')
        if not math.isfinite(self.timeout) or self.timeout <= 0:
            raise ValueError('OCR 超时必须大于零')


@dataclass(frozen=True)
class OcrContent:
    markdown_path: str
    markdown: str
    resources: dict[str, bytes]
    raw_response: bytes
    diagnostics: tuple[Diagnostic, ...] = (Diagnostic('ocr_markdown_only', 'OCR 上游仅返回 Markdown，原版面及已丢失的表格/样式信息无法恢复。'),)


def parse_ocr_response(content: bytes) -> OcrContent:
    if len(content) > MAX_OCR_RESPONSE_BYTES:
        raise ValueError('OCR 响应超过 100 MiB 限制')
    with ZipFile(io.BytesIO(content)) as archive:
        members = {name: archive.read(item) for name, item in validate_archive(archive)}
    markdown = [name for name in members if Path(name).suffix.lower() == '.md']
    if len(markdown) != 1:
        raise ValueError('OCR 响应必须包含唯一 Markdown 正文')
    name = markdown[0]
    text = members.pop(name).decode('utf-8-sig')
    if not text.strip():
        raise ValueError('OCR 响应正文为空')
    return OcrContent(name, text, members, content)


def recognize(source: Path, config: OcrConfig, *, client: httpx.Client | None = None) -> OcrContent:
    if client is None:
        with httpx.Client(timeout=config.timeout) as session:
            return recognize(source, config, client=session)
    with source.open('rb') as stream:
        with client.stream('POST', config.api_url, files={'file': (source.name, stream)}, timeout=config.timeout,
                           headers={'Accept-Encoding': 'identity'}) as response:
            response.raise_for_status()
            content = read_response_body(response, MAX_OCR_RESPONSE_BYTES)
    return parse_ocr_response(content)
