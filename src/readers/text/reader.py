"""Read a UTF-8 plain-text original into the shared content revision.

Line order and line breaks are preserved by placing the escaped text in one
``pre`` fragment. Decoding is strict: invalid UTF-8 is an extraction failure
after the original has already been saved, never a silently lossy body.
"""

from __future__ import annotations

from html import escape
from pathlib import Path

from bs4 import BeautifulSoup

from ...content.revision import populate_html_revision
from ...domain.book import BookMetadata, BookRevision

PIPELINE_VERSION = 'text-plain-v1'


def read_text_document(
    source_path, *, fallback_title: str, source_sha256: str,
    source_id: str, book_id: str, edition_id: str, revision_id: str,
    original_filename: str,
) -> BookRevision:
    text = source_path.read_bytes().decode('utf-8')
    soup = BeautifulSoup(f'<pre>{escape(text, quote=False)}</pre>', 'html.parser')
    metadata = BookMetadata(fallback_title)
    book = BookRevision(
        book_id, edition_id, revision_id, source_id, source_path, source_sha256,
        metadata, [], {}, [], {}, [], PIPELINE_VERSION, source_format='text',
    )
    return populate_html_revision(book, {Path(original_filename).name: soup}, lambda name: None)