"""Read DOCX semantic HTML, embedded resources and diagnostics once."""

from __future__ import annotations

import hashlib
import mimetypes
import posixpath
from pathlib import Path
from xml.etree import ElementTree as ET
from zipfile import ZipFile

import mammoth
from bs4 import BeautifulSoup

from ...adapters.archive import validate_archive
from ...content.revision import populate_html_revision
from ...domain.book import BookMetadata, BookRevision, Diagnostic
from .package import normalized_text, prepare_docx

PIPELINE_VERSION = 'docx-dom-v1'
STYLE_MAP = "\n".join([
    *(f"p[style-name='{name}'] => pre:separator('\\n')" for name in ('Code', 'Code Block', 'Source Code', '代码', '代码块')),
    'comment-reference => sup',
    'u => u',
])


def _normalize_notes(soup):
    """One note body per source identity, with return links to every reference."""
    for kind, tag in (('footnote', 'li'), ('endnote', 'li'), ('comment', 'dt')):
        seen = set()
        for note in list(soup.find_all(tag, id=lambda value: value and value.startswith(kind+'-'))):
            identifier = note['id']
            if identifier in seen:
                if tag == 'dt':
                    body = note.find_next_sibling('dd')
                    if body is not None:
                        body.decompose()
                note.decompose()
                continue
            seen.add(identifier)
            references = soup.find_all('a', href='#'+identifier)
            body = note.find_next_sibling('dd') if tag == 'dt' else note
            label = references[0].get_text() if references else ''
            for index, reference in enumerate(references, 1):
                original = reference.get('id')
                if index > 1 and original:
                    reference['id'] = original+f'-occurrence-{index}'
                    reference.string = label
                    back = soup.new_tag('a', href='#'+reference['id'])
                    back.string = f' ↑{index}'
                    if body is not None:
                        body.append(back)


def _apply_numbering(soup, hints, diagnostics, path):
    types = {'decimal': None, 'decimalZero': None, 'lowerLetter': 'a', 'upperLetter': 'A', 'lowerRoman': 'i', 'upperRoman': 'I'}
    listings = {}
    for identifier, hint in hints.items():
        anchor = soup.find('a', id=identifier)
        item = anchor.find_parent('li') if anchor is not None else None
        if anchor is not None:
            anchor.decompose()
        if item is None:
            diagnostics.append(Diagnostic('docx_numbering', '源编号段落未映射为列表，编号语义尚不能完整保留。', severity='error', source_path=path))
            continue
        if hint.kind != 'bullet' and (hint.kind not in types or hint.text != f'%{hint.level+1}.'):
            diagnostics.append(Diagnostic('docx_numbering', f'尚不能完整保留列表编号格式：{hint.kind} / {hint.text}', severity='error', source_path=path))
        listing = item.find_parent(['ol', 'ul'])
        listings.setdefault(id(listing), (listing, {}))[1][id(item)] = hint
        if hint.kind != 'bullet':
            item['value'] = str(hint.value)
    for listing, items in listings.values():
        # The extractor can merge adjacent instances/formats into one list.
        # Split only at source boundaries, keeping nested lists on their item.
        current, previous = listing, None
        for child in list(listing.contents):
            hint = items.get(id(child))
            if hint is not None:
                key = hint.instance, hint.kind
                if previous is not None and key != previous:
                    following = soup.new_tag(listing.name)
                    current.insert_after(following)
                    current = following
                if key != previous:
                    current.name = 'ul' if hint.kind == 'bullet' else 'ol'
                    if hint.kind != 'bullet':
                        current['start'] = str(hint.value)
                    if types.get(hint.kind):
                        current['type'] = types[hint.kind]
                    if hint.kind == 'decimalZero':
                        current['style'] = 'list-style-type: decimal-leading-zero'
                previous = key
            if current is not listing:
                current.append(child.extract())


def _check_source_text(soup, expected, diagnostics, path):
    clone = BeautifulSoup(str(soup), 'html.parser')
    for element in list(clone.find_all(['a', 'dt'])):
        identifier = element.get('id', '')
        href = element.get('href', '')
        if (element.name == 'dt' and identifier.startswith('comment-')) or (element.name == 'a' and (
            any(identifier.startswith(kind+'-ref-') for kind in ('footnote', 'endnote', 'comment')) or
            any(href.startswith('#'+kind+'-ref-') for kind in ('footnote', 'endnote', 'comment'))
        )):
            element.decompose()
    if normalized_text(clone.get_text()) != expected:
        diagnostics.append(Diagnostic('docx_source_text', '源 OOXML 正文与解析结果不一致，可能存在未保留、重复或乱序内容。', severity='error', source_path=path))


def _metadata(archive: ZipFile, fallback: str, diagnostics: list[Diagnostic]) -> BookMetadata:
    if 'docProps/core.xml' not in archive.namelist():
        return BookMetadata(fallback)
    try:
        core = ET.fromstring(archive.read('docProps/core.xml'))
    except ET.ParseError:
        diagnostics.append(Diagnostic('docx_metadata', 'DOCX 核心元数据 XML 无效，使用文件名。', source_path='docProps/core.xml'))
        return BookMetadata(fallback)
    namespace = '{http://purl.org/dc/elements/1.1/}'
    title = (core.findtext(namespace+'title') or '').strip() or fallback
    authors = tuple(element.text.strip() for element in core.findall(namespace+'creator') if element.text and element.text.strip())
    return BookMetadata(title, authors, core.findtext(namespace+'language') or None, core.findtext(namespace+'identifier') or None)


def read_docx_document(
    source_path: Path, *, fallback_title: str, source_sha256: str,
    source_id: str, book_id: str, edition_id: str, revision_id: str,
) -> BookRevision:
    diagnostics: list[Diagnostic] = []
    with ZipFile(source_path) as archive:
        validate_archive(archive)
        metadata = _metadata(archive, fallback_title, diagnostics)
        prepared = prepare_docx(archive, edition_id, diagnostics)
    resources: dict[str, tuple[bytes, str | None]] = {}

    def image_source(image):
        with image.open() as stream:
            content = stream.read()
        digest = hashlib.sha256(content).hexdigest()
        suffix = mimetypes.guess_extension(image.content_type or '') or '.bin'
        relative = f'media/{digest}{suffix}'
        resources[posixpath.join(posixpath.dirname(prepared.document_path), relative)] = content, image.content_type
        return {'src': relative}

    input_stream = prepared.stream if prepared.stream is not None else source_path.open('rb')
    with input_stream as stream:
        result = mammoth.convert_to_html(
            stream, style_map=STYLE_MAP, ignore_empty_paragraphs=False,
            include_embedded_style_map=False, external_file_access=False,
            convert_image=mammoth.images.img_element(image_source),
        )
    for message in result.messages:
        text = message.message
        lost = message.type == 'error' or any(token in text.lower() for token in ('could not', 'was ignored', 'unlikely to display'))
        diagnostics.append(Diagnostic('docx_conversion', text, severity='error' if lost else 'warning', source_path=prepared.document_path))
    soup = BeautifulSoup(result.value, 'html.parser')
    _normalize_notes(soup)
    _apply_numbering(soup, prepared.list_hints, diagnostics, prepared.document_path)
    _check_source_text(soup, prepared.expected_text, diagnostics, prepared.document_path)
    book = BookRevision(book_id, edition_id, revision_id, source_id, source_path, source_sha256, metadata, [], {}, [], {}, diagnostics, PIPELINE_VERSION, source_format='docx')
    return populate_html_revision(book, {prepared.document_path: soup}, resources.get)
