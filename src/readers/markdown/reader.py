"""Read a Markdown source package into the shared content revision.

The Markdown bytes are imported with the existing ``import_markdown`` helper
and enter the same DOM normalization, resource, anchor and publication chain
as HTML and DOCX. Front matter is source text only: title and every identity
are supplied by the caller, never by the submitted file.
"""

from __future__ import annotations

from pathlib import Path

from ...adapters.markdown_source import read_markdown_source
from ...content.markdown import import_markdown
from ...content.revision import populate_html_revision
from ...domain.book import BookMetadata, BookRevision

PIPELINE_VERSION = 'markdown-dom-v1'


def read_markdown_document(
    source_path: Path, *, fallback_title: str, source_sha256: str,
    source_id: str, book_id: str, edition_id: str, revision_id: str,
    original_filename: str,
) -> BookRevision:
    source = read_markdown_source(source_path)
    text = source.members[source.entrypoint].decode('utf-8')
    document = import_markdown(text)
    metadata = BookMetadata(fallback_title or Path(original_filename).stem)
    book = BookRevision(
        book_id, edition_id, revision_id, source_id, source_path, source_sha256,
        metadata, [], {}, [], {}, [], PIPELINE_VERSION, source_format='markdown',
    )
    return populate_html_revision(book, {source.entrypoint: document}, source.resource)