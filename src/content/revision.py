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
        soup = prepare_html_document(soup, path, assets, load_resource, style_sources, styles, diagnostics, resource_base=base, document_paths=set(documents), resource_state=resource_state, locate_source_images=book.source_format not in {'docx','url'})
        container = soup.body or soup
        sections = [section for section in container.find_all('section') if section.find_parent('section') is None]
        # Explicit sections retain the existing split contract. Heading scopes
        # within each document use all declared levels, including skipped levels.
        headings = [heading for heading in container.find_all(re.compile('^h[1-6]$')) if heading.find_parent(['table', 'pre', 'nav', 'aside']) is None]
        heading_indices = {id(heading): index for index,heading in enumerate(headings)}
        level = min((int(heading.name[1]) for heading in headings), default=1)
        boundaries = sections or headings or [container]
        split_points, document_nodes, preludes = {}, [], {}
        heading_stack = []
        for index, section in enumerate(boundaries, 1):
            heading = section if id(section) in heading_indices else section.find(re.compile('^h[1-6]$')) if section is not container else None
            page_title = soup.title.get_text(' ', strip=True) if soup.title else metadata.title
            label = heading.get_text(' ', strip=True) if heading else (section.get('data-title') or section.get('aria-label') or (page_title if len(boundaries) == 1 else None))
            if not label:
                label = f'第 {index} 节'
                diagnostics.append(Diagnostic('section_title_inferred', '源分节缺少标题，使用阅读序号。', source_path=path))
            original_fragment = section.get('data-source-original-id') or None
            locator = SourceLocator(path, original_fragment, format=book.source_format)
            node_id = str(uuid.uuid5(uuid.UUID(edition_id), f'{book.source_format}:{path}:section:{index}:{original_fragment or ""}'))
            source_level = int(heading.name[1]) if heading is not None else level
            while heading_stack and heading_stack[-1][0] >= source_level:
                heading_stack.pop()
            parent_id = heading_stack[-1][1] if heading_stack and not sections else None
            siblings = nodes[parent_id].children_ids if parent_id else roots
            ordinal = len(siblings)+1
            ordinal_path = (*nodes[parent_id].ordinal_path, ordinal) if parent_id else (ordinal,)
            depth = source_level-level+1 if not sections else 1
            node = StructureNode(node_id, parent_id, [], depth, ordinal, ordinal_path, str(label), str(label), 'chapter' if len(boundaries) > 1 else 'document', f'{locator.key}:section:{index}', locator)
            nodes[node_id] = node
            siblings.append(node_id)
            heading_stack.append((source_level,node_id))
            document_nodes.append(node_id)
            before_heading = 'before:'+node_id
            preludes[before_heading] = node_id
            split_points[id(section)] = before_heading if heading is not None and heading is not section else node_id
            if heading is not None:
                split_points[id(heading)] = node_id
            if sections:
                # Keep explicit section roots, but apply the same heading stack
                # to their declared descendant heading scopes.
                local_stack = [(source_level,node_id)]
                for nested in section.find_all(re.compile('^h[1-6]$')):
                    if nested is heading or id(nested) not in heading_indices:
                        continue
                    nested_level = int(nested.name[1])
                    while len(local_stack)>1 and local_stack[-1][0]>=nested_level:
                        local_stack.pop()
                    parent = nodes[local_stack[-1][1]]
                    child_id = str(uuid.uuid5(uuid.UUID(edition_id),f'{book.source_format}:{path}:heading:{heading_indices[id(nested)]}'))
                    order = len(parent.children_ids)+1
                    title = nested.get_text(' ',strip=True)
                    child_locator = SourceLocator(path,nested.get('data-source-original-id'),format=book.source_format)
                    child = StructureNode(child_id,parent.node_id,[],parent.depth+max(1,nested_level-local_stack[-1][0]),order,(*parent.ordinal_path,order),title,title,'section',child_locator.key,child_locator)
                    nodes[child_id] = child
                    parent.children_ids.append(child_id)
                    split_points[id(nested)] = child_id
                    local_stack.append((nested_level,child_id))
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
    book.reading_order_ids = book.walk_ids()
    return book
