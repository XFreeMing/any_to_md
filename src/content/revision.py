"""Build one ordered content revision from already parsed HTML source documents.

Input readers retain source acquisition and format-specific interpretation.
Structure, fragments, resources and references are shared across DOM sources.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Callable

from bs4 import BeautifulSoup

from .document import document_base, document_styles, prepare_html_document, scoped_fragment
from .dom import partition, strip_title
from .media import ResourceState
from ..domain.book import BookRevision, ContentFragment, Diagnostic, SourceLocator, StructureNode


def populate_html_revision(
    book: BookRevision,
    documents: dict[str, BeautifulSoup],
    load_resource: Callable[[str], tuple[bytes, str | None] | None],
) -> BookRevision:
    assets, styles, diagnostics = book.assets, book.styles, book.diagnostics
    nodes, roots, content, anchor_owners = book.nodes, book.roots, book.content, book.anchor_owners
    metadata, edition_id = book.metadata, book.edition_id
    resource_state = ResourceState()
    for path, soup in documents.items():
        base = document_base(soup, path)
        style_sources = document_styles(soup, base)
        soup = prepare_html_document(soup, path, assets, load_resource, style_sources, styles, diagnostics, resource_base=base, document_paths=set(documents), resource_state=resource_state)
        container = soup.body or soup
        sections = [section for section in container.find_all('section') if section.find_parent('section') is None]
        # Explicit sections retain the existing split contract. In their absence,
        # use the highest source heading level; plain prose remains one document.
        headings = [heading for heading in container.find_all(re.compile('^h[12]$')) if heading.find_parent(['table', 'pre', 'nav', 'aside']) is None]
        level = min((int(heading.name[1]) for heading in headings), default=1)
        boundaries = sections or [heading for heading in headings if int(heading.name[1]) == level] or [container]
        split_points, document_nodes, preludes = {}, [], {}
        for index, section in enumerate(boundaries, 1):
            heading = section if section.name in {'h1', 'h2'} else section.find(re.compile('^h[1-6]$')) if section is not container else None
            page_title = soup.title.get_text(' ', strip=True) if soup.title else metadata.title
            label = heading.get_text(' ', strip=True) if heading else (section.get('data-title') or section.get('aria-label') or (page_title if len(boundaries) == 1 else None))
            if not label:
                label = f'第 {index} 节'
                diagnostics.append(Diagnostic('section_title_inferred', '源分节缺少标题，使用阅读序号。', source_path=path))
            original_fragment = section.get('data-source-original-id') or None
            locator = SourceLocator(path, original_fragment, format=book.source_format)
            node_id = str(uuid.uuid5(uuid.UUID(edition_id), f'{book.source_format}:{path}:section:{index}:{original_fragment or ""}'))
            ordinal = len(roots)+1
            node = StructureNode(node_id, None, [], 1, ordinal, (ordinal,), str(label), str(label), 'chapter' if len(boundaries) > 1 else 'document', f'{locator.key}:section:{index}', locator)
            nodes[node_id] = node
            roots.append(node_id)
            document_nodes.append(node_id)
            before_heading = 'before:'+node_id
            preludes[before_heading] = node_id
            split_points[id(section)] = before_heading if heading is not None and heading is not section else node_id
            if heading is not None:
                split_points[id(heading)] = node_id
        titled = set()
        default_owner = document_nodes[0] if len(boundaries) == 1 and boundaries[0] is container else 'before:'+document_nodes[0]
        for index, (owner, html) in enumerate(partition(container, split_points, default_owner, split_boundaries=True, keep_empty_boundaries=True)):
            heading_before = owner not in preludes
            owner = preludes.get(owner, owner)
            if heading_before and owner not in titled:
                headings_before = len(BeautifulSoup(html, 'html.parser').find_all(re.compile('^h[1-6]$')))
                html = strip_title(html, nodes[owner].title_original)
                if len(BeautifulSoup(html, 'html.parser').find_all(re.compile('^h[1-6]$'))) < headings_before:
                    titled.add(owner)
            fragment_id = str(uuid.uuid5(uuid.UUID(edition_id), f'{book.source_format}:{path}:content:{index}'))
            fragment = ContentFragment(fragment_id, owner, SourceLocator(path, format=book.source_format), scoped_fragment(soup, html, path), heading_before=heading_before)
            content.append(fragment)
            nodes[owner].content_ids.append(fragment_id)
            for anchor in BeautifulSoup(fragment.html, 'html.parser').find_all(id=True):
                anchor_owners[str(anchor['id'])] = owner
    for fragment in content:
        for link in BeautifulSoup(fragment.html, 'html.parser').find_all('a', href=True):
            href = str(link['href'])
            if href.startswith('#src-') and href[1:] not in anchor_owners:
                diagnostics.append(Diagnostic('unresolved_internal_reference', f'源引用目标不存在：{href}', node_id=fragment.node_id, source_path=fragment.source.path))
    book.reading_order_ids = roots.copy()
    return book
