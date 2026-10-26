"""Replay captured article DOM through the shared content normalizer."""

from pathlib import Path

from ...adapters.url_source import article_document, read_url_source
from ...content.revision import populate_html_revision
from ...domain.book import BookMetadata, BookRevision, Diagnostic

PIPELINE_VERSION = 'url-dom-v2'


def read_url_document(source_path: Path, *, fallback_title: str, source_sha256: str,
                      source_id: str, book_id: str, edition_id: str, revision_id: str) -> BookRevision:
    source = read_url_source(source_path)
    origin, final = source.manifest['url'], source.manifest['final_url']
    document, marked = article_document(source.members['response.html'], final, source.manifest['headers'].get('content-type', ''))
    metadata = BookMetadata(document.title.get_text(' ', strip=True) or fallback_title,
                            tuple(str(tag['content']) for tag in document.find_all('meta', attrs={'name': 'author', 'content': True})),
                            document.html.get('lang'), origin)
    diagnostics = []
    if not marked:
        diagnostics.append(Diagnostic('unmarked_article', '网页没有 main/article 标记，已保留去除导航后的正文；正文边界需核对。'))
    for failure in source.manifest['failures']:
        diagnostics.append(Diagnostic('url_resource_failed', failure['error'], severity='error', source_path=failure['url']))
    provenance = document.new_tag('p')
    link = document.new_tag('a', href=origin, rel='external')
    link.string = origin
    provenance.append('Source: ')
    provenance.append(link)
    document.body.insert(0, provenance)
    book = BookRevision(book_id, edition_id, revision_id, source_id, source_path, source_sha256,
                        metadata, [], {}, [], {}, diagnostics, PIPELINE_VERSION, source_format='url')
    return populate_html_revision(book, {final: document}, source.resource)
