"""Utilities for converting HTML exports into high-quality PDF files."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Iterable, List, Sequence

from weasyprint import CSS, HTML

try:  # WeasyPrint >= 66 renamed the module location
    from weasyprint.fonts import FontConfiguration  # type: ignore
except ImportError:  # pragma: no cover - compatibility path
    from weasyprint.text.fonts import FontConfiguration

LOGGER = logging.getLogger(__name__)
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_FONT_DIRS: Sequence[Path] = (
    PROJECT_ROOT / "md_to_any" / "fonts",
    PROJECT_ROOT / "md_to_any" / "service" / "fonts",
)

_DEFAULT_STYLES = """
@page {
  size: A4;
  margin: 18mm 15mm 20mm 15mm;
}
body {
  font-size: 12pt;
  line-height: 1.55;
  color: #111;
  font-family: 'AnyToMdSans', 'AnyToMdYaHei', 'PingFang SC', 'Microsoft YaHei',
               'Helvetica Neue', 'Segoe UI Emoji', 'Apple Color Emoji',
               'Noto Color Emoji', sans-serif;
}
h1, h2, h3, h4, h5, h6 {
  color: #0b2545;
  line-height: 1.2;
  page-break-after: avoid;
}
img {
  max-width: 100%;
  height: auto;
  page-break-inside: avoid;
}
table {
  width: 100%;
  border-collapse: collapse;
  margin: 1em 0;
  font-size: 0.95em;
}
th, td {
  border: 1px solid #ccc;
  padding: 6px 8px;
  vertical-align: top;
}
pre, code {
  font-family: 'JetBrains Mono', 'Fira Code', 'SFMono-Regular', Consolas, monospace;
  font-size: 10pt;
  background: #f4f4f4;
  border-radius: 4px;
}
pre {
  padding: 10px;
  overflow-x: auto;
}
blockquote {
  border-left: 4px solid #5089c6;
  margin: 1em 0;
  padding: 0.3em 1em;
  color: #31456a;
}
""".strip()


def _existing_font_files() -> List[Path]:
    fonts: List[Path] = []
    for directory in DEFAULT_FONT_DIRS:
        if directory.is_dir():
            for entry in directory.iterdir():
                if entry.suffix.lower() in {".ttf", ".otf", ".ttc"}:
                    fonts.append(entry)
    return fonts


def _font_face_rules(font_files: Iterable[Path]) -> str:
    rules: List[str] = []
    for font_path in font_files:
        font_name = font_path.stem.replace(" ", "")
        try:
            uri = font_path.resolve().as_uri()
        except ValueError:
            continue
        rules.append(
            "@font-face {\n"
            f"  font-family: '{font_name}';\n"
            f"  src: url('{uri}');\n"
            "  font-weight: normal;\n"
            "  font-style: normal;\n"
            "}"
        )
    return "\n".join(rules)


def _compose_styles(extra_css: str | None = None) -> str:
    font_rules = _font_face_rules(_existing_font_files())
    body_family = (
        "font-family: 'AnyToMdSans', 'AnyToMdYaHei', 'NotoSansCJK', 'PingFang SC', "
        "'Microsoft YaHei', 'Hiragino Sans', 'Helvetica Neue', 'Segoe UI Emoji', "
        "'Apple Color Emoji', 'Noto Color Emoji', sans-serif;"
    )
    body_override = f"body {{{body_family}}}"
    pieces = [font_rules, _DEFAULT_STYLES, body_override]
    if extra_css:
        pieces.append(extra_css)
    return "\n\n".join(part for part in pieces if part)


def convert_html_to_pdf(
    html_path: Path,
    *,
    output_path: Path | None = None,
    extra_css: str | None = None,
) -> Path:
    """Convert an HTML file (plus relative assets) into a PDF document."""

    html_path = html_path.resolve()
    if output_path is None:
        output_path = html_path.parent / f"{html_path.stem}.pdf"
    output_path.parent.mkdir(parents=True, exist_ok=True)

    font_config = FontConfiguration()
    stylesheet = CSS(string=_compose_styles(extra_css), font_config=font_config)
    LOGGER.info("Rendering %s -> %s", html_path.name, output_path)

    document = HTML(filename=str(html_path), base_url=str(html_path.parent))
    document.write_pdf(
        target=str(output_path),
        stylesheets=[stylesheet],
        font_config=font_config,
    )
    return output_path


__all__ = ["convert_html_to_pdf"]
