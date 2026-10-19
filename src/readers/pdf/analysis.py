"""One exhaustive page analysis, independent of OCR transport and Web settings."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
import unicodedata

import pymupdf


class PdfInputError(ValueError):
    """Unreadable, empty, non-PDF or password-protected source."""


@dataclass(frozen=True)
class PageAnalysis:
    page_index: int
    kind: str
    native_characters: int
    unmapped_characters: int
    image_count: int
    vector_count: int

    @property
    def needs_ocr(self) -> bool:
        # A bitmap next to valid native text may be a logo/photo. Mixed is an
        # inspection fact; it does not prove more text needs recognition. Only
        # confirmed unreadable pages/glyphs change the remaining whole-file path
        # until a real page composition strategy is validated.
        return self.kind == 'scanned' or self.unmapped_characters > 0


@dataclass(frozen=True)
class PdfAnalysis:
    pages: tuple[PageAnalysis, ...]
    metadata: dict
    repaired: bool

    @property
    def needs_ocr(self) -> bool:
        return any(page.needs_ocr for page in self.pages)

    def to_dict(self) -> dict:
        return {'page_count': len(self.pages), 'needs_ocr': self.needs_ocr,
                'repaired': self.repaired, 'metadata': self.metadata,
                'pages': [{**asdict(page), 'needs_ocr': page.needs_ocr} for page in self.pages]}


def analyze_pdf(path: Path | str) -> PdfAnalysis:
    """Inspect every page; no file/parse failure is interpreted as a scan.

    Raster content alongside text is mixed: geometry cannot establish whether
    it contains more text, so this alone does not force OCR. Vector paths next to native text
    alone do not force OCR (they commonly form table borders). Classification is
    a routing signal, not recognition quality evidence. White raster pages are blank only
    if their bounded grayscale rendering contains no visible nonwhite pixel.
    """
    try:
        document = pymupdf.open(path)
    except (pymupdf.FileDataError, pymupdf.EmptyFileError, RuntimeError) as error:
        raise PdfInputError(f'PDF 无法解析：{error}') from error
    with document:
        if not document.is_pdf:
            raise PdfInputError('输入不是 PDF')
        if document.needs_pass:
            raise PdfInputError('PDF 已加密，需要解密后的原件')
        if not len(document):
            raise PdfInputError('PDF 没有页面')
        pages = []
        for page in document:
            characters = [value for value in page.get_text() if not value.isspace()]
            unmapped = sum(value == '\ufffd' or unicodedata.category(value) in {'Cc', 'Cs', 'Co', 'Cn'} for value in characters)
            usable = len(characters)-unmapped
            images = [value for value in page.get_image_info() if (pymupdf.Rect(value['bbox'])*page.rotation_matrix).intersects(page.rect)]
            drawings = page.get_drawings()
            if usable:
                kind = 'mixed' if images or unmapped else 'native'
            else:
                # Bound allocation even for unusual physical page dimensions.
                dimension = max(page.rect.width, page.rect.height)
                if dimension <= 0:
                    raise PdfInputError(f'PDF 第 {page.number+1} 页尺寸无效')
                pixmap = page.get_pixmap(matrix=pymupdf.Matrix(min(1, 1024/dimension), min(1, 1024/dimension)), colorspace=pymupdf.csGRAY, alpha=False)
                kind = 'scanned' if any(value < 255 for value in pixmap.samples) else 'blank'
            pages.append(PageAnalysis(page.number, kind, usable, unmapped, len(images), len(drawings)))
        return PdfAnalysis(tuple(pages), dict(document.metadata or {}), document.is_repaired)
