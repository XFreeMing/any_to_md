"""One PDF rendering implementation; document input comes from HTML Writer."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import unquote, urlsplit

from .html import BASE_CSS


def render_pdf(html_path: Path, *, output_path: Path | None = None, extra_css: str | None = None) -> Path:
    # Lazy loading keeps non-PDF conversion usable without native rendering libs.
    from weasyprint import CSS, HTML, default_url_fetcher
    from weasyprint.text.fonts import FontConfiguration

    target = output_path or html_path.with_suffix(".pdf")
    target.parent.mkdir(parents=True, exist_ok=True)
    fonts = Path(__file__).resolve().parents[1] / "resources" / "fonts"
    allowed_root = html_path.resolve().parent
    allowed_fonts = {path.resolve() for path in fonts.iterdir() if path.is_file() and path.suffix.lower() in {'.ttf', '.ttc', '.otf'}}
    blocked = []

    def fetch(url):
        if not isinstance(url, str) or not url:
            # WeasyPrint can request None for an SVG <image> without href.
            # There is no resource to fetch and no external access attempt.
            raise ValueError('PDF 资源缺少 URL')
        parsed = urlsplit(url)
        if parsed.scheme == 'data':
            return default_url_fetcher(url)
        if parsed.scheme == 'file' and not parsed.netloc:
            path = Path(unquote(parsed.path)).resolve()
            if path.is_file() and (path.is_relative_to(allowed_root) or path in allowed_fonts):
                return default_url_fetcher(url)
        blocked.append(url)
        raise ValueError('PDF 资源不在当前导出或包内字体范围内')
    rules = []
    for filename, family in (("NotoSansCJK.ttc", "AnyToMdSans"), ("NotoEmoji-Regular.ttf", "AnyToMdEmoji")):
        path = fonts / filename
        if path.is_file():
            rules.append(f'@font-face {{ font-family: {family}; src: url("{path.as_uri()}"); }}')
    rules.append("body { font-family: AnyToMdSans, AnyToMdEmoji, sans-serif; }")
    config = FontConfiguration()
    styles = CSS(string=BASE_CSS + "\n" + "\n".join(rules) + "\n" + (extra_css or ""), font_config=config, url_fetcher=fetch)
    HTML(filename=str(html_path.resolve()), url_fetcher=fetch).write_pdf(
        str(target), stylesheets=[styles], font_config=config,
        presentational_hints=True,
    )
    if blocked:
        target.unlink(missing_ok=True)
        raise ValueError('PDF 拒绝读取导出外或网络资源：'+', '.join(dict.fromkeys(blocked)))
    return target
