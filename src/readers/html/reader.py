"""Read HTML and reachable local pages into one canonical content revision."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlsplit

from bs4 import BeautifulSoup

from ...adapters.html_source import read_html_source
from ...content.document import document_base, element_resource_base
from ...content.revision import populate_html_revision
from ...content.references import resolve_path
from ...domain.book import BookMetadata, BookRevision

PIPELINE_VERSION = 'html-dom-v1'


def read_html_document(
    source_path: Path, *, fallback_title: str, source_sha256: str,
    source_id: str, book_id: str, edition_id: str, revision_id: str,
    original_filename: str,
) -> BookRevision:
    source = read_html_source(source_path, original_filename)
    documents = {source.entrypoint: BeautifulSoup(source.members[source.entrypoint], 'html.parser')}
    pending = [source.entrypoint]
    # Follow only captured local HTML links. Unreferenced pages in a ZIP are not
    # silently appended, and a cyclic link never duplicates the source body.
    while pending:
        path = pending.pop(0)
        soup = documents[path]
        base = document_base(soup, path)
        for link in soup.find_all('a', href=True):
            href = str(link['href'])
            if urlsplit(href).scheme or urlsplit(href).netloc:
                continue
            target = resolve_path(element_resource_base(link, base), href)
            if target in documents or target not in source.members or Path(target).suffix.lower() not in {'.html', '.htm'}:
                continue
            documents[target] = BeautifulSoup(source.members[target], 'html.parser')
            pending.append(target)
    first = documents[source.entrypoint]
    title = first.title.get_text(' ', strip=True) if first.title else fallback_title
    metadata = BookMetadata(title or fallback_title, tuple(str(author['content']) for author in first.find_all('meta', attrs={'name': 'author', 'content': True})), first.html.get('lang') if first.html else None)
    book = BookRevision(book_id, edition_id, revision_id, source_id, source_path, source_sha256, metadata, [], {}, [], {}, [], PIPELINE_VERSION, source_format='html')
    return populate_html_revision(book, documents, source.resource)
