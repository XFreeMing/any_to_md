"""The single HTML projection of canonical content, also consumed by PDF."""

from __future__ import annotations

from html import escape
from pathlib import Path

from ..content.dom import iter_flow
from ..domain.artifacts import ExportPlan
from ..domain.book import BookRevision
from .rendering import RenderContext

BASE_CSS = '''
@page { size: A4; margin: 18mm 15mm 20mm 15mm; }
body { font-family: AnyToMdSans, sans-serif; font-size: 12pt; line-height: 1.55; color: #111; }
[data-source-html], [data-source-body] { display: block; }
h1,h2,h3,h4,h5,h6 { break-after: avoid; }
img,svg { max-width: 100%; height: auto; }
table { border-collapse: collapse; max-width: 100%; }
th,td { border: 1px solid #ccc; padding: 6px 8px; vertical-align: top; }
pre { white-space: pre-wrap; overflow-wrap: anywhere; padding: 10px; background: #f5f5f5; }
pre,code { font-family: monospace; }
@media screen { body { max-width: 960px; margin: 2em auto; padding: 0 1em; } }
'''


def render_html(book: BookRevision, plan: ExportPlan) -> str:
    context = RenderContext(book, plan)
    body = [f"<h1>{escape(book.metadata.title)}</h1>"]
    for kind, item in iter_flow(book):
        if kind == "heading":
            level = min(item.depth + 1, 6)
            body.append(f'<h{level} id="{item.anchor}">{escape(item.title_display)}</h{level}>')
        else:
            body.append(context.fragment(item, "book.html", "single", book.nodes[item.node_id].depth + 1))
    styles = "\n".join(context.resources(css, "book.html") for css in book.styles.values())
    # A style closing sequence is source text, never markup in the generated head.
    styles = styles.replace("</", "<\\/")
    return '<!DOCTYPE html><html><head><meta charset="utf-8"><title>' + escape(book.metadata.title) + '</title><style>' + BASE_CSS + '\n' + styles + '</style></head><body>' + '\n'.join(body) + '</body></html>'


def write_html(book: BookRevision, plan: ExportPlan, root: Path) -> Path:
    target = root / "book.html"
    target.write_text(render_html(book, plan), encoding="utf-8")
    return target
