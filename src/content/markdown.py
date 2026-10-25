"""Import an upstream Markdown result as source DOM for shared normalization.

This cannot recover layout or table information already lost upstream. Callers
retain that limitation in their source diagnostics. Resources and anchors enter
the same populate_html_revision path as other DOM sources at Reader cutover.
"""

from bs4 import BeautifulSoup
import markdown

from .document import sanitize_html


def import_markdown(content: str) -> BeautifulSoup:
    html = markdown.markdown(content, extensions=['tables', 'fenced_code', 'footnotes'])
    document = BeautifulSoup(html, 'html.parser')
    sanitize_html(document)
    return document
