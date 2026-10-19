"""Strict source-wide EPUB text/order, navigation and image audit.

This checker never imports a production reader/writer. Its expected text and
navigation come from the original ZIP/XML. Only whitespace/Unicode NFKC and
source-evidenced printed code line numbers are allowed text transformations.
Reports stay local because they describe a potentially private source book.
"""

from __future__ import annotations

if __name__ == "__main__" and not __package__:
    import runpy
    from pathlib import Path

    runpy.run_path(str(Path(__file__).resolve().with_name("bootstrap.py")))

import argparse
import hashlib
import json
import posixpath
import re
import unicodedata
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import unquote, urlsplit
from xml.etree import ElementTree as ET
from zipfile import ZipFile

import markdown
import tinycss2
from bs4 import BeautifulSoup, Comment, ProcessingInstruction, Tag


def normalize(value: str) -> str:
    return ''.join(unicodedata.normalize('NFKC', value).split())


def _digest(value: str | bytes) -> str:
    return hashlib.sha256(value.encode() if isinstance(value, str) else value).hexdigest()


def _relative(base: str, value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme or parsed.netloc:
        raise ValueError('Source audit expects offline package references')
    return posixpath.normpath(posixpath.join(posixpath.dirname(base), unquote(parsed.path))) if parsed.path else base


@dataclass
class SourceUnit:
    document: str
    text: str
    tag: str
    number: int | None = None
    payload: str | None = None
    monospace: bool = False
    allow_number_cleanup: bool = False


@dataclass
class SourceBook:
    title: str
    package: str
    documents: list[str] = field(default_factory=list)
    units: list[SourceUnit] = field(default_factory=list)
    labels: set[str] = field(default_factory=set)
    navigation: list[dict] = field(default_factory=list)
    images: list[str] = field(default_factory=list)
    authors: list[str] = field(default_factory=list)
    language: str | None = None
    document_labels: dict[str, set[str]] = field(default_factory=dict)


_CODE_FONTS = {'monospace', 'ui-monospace', 'source code pro', 'courier', 'courier new', 'consolas', 'menlo', 'monaco', 'dejavu sans mono', 'liberation mono', 'fira code', 'fira mono', 'cascadia code', 'cascadia mono', 'noto sans mono'}
_TEXT_FONTS = {'', 'serif', 'sans-serif', 'cursive', 'fantasy', 'system-ui', 'ui-serif', 'ui-sans-serif', 'emoji', 'math', 'fangsong', 'arial', 'helvetica', 'times', 'times new roman', 'dejavu sans', 'pingfang sc', 'pingfang tc', 'monotype corsiva'}
_BLOCKS = ['p', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'pre', 'li', 'td', 'th', 'dt', 'dd', 'div', 'body', 'aside']


def _source_font(value: str | None) -> bool | None:
    # The independent checker accepts only unambiguous, single named families.
    # It never guesses which fallback or custom font will actually be used.
    if value is None:
        return None
    name = ' '.join(value.strip().strip('\"\'').casefold().split())
    if name in _CODE_FONTS:
        return True
    return False if name in _TEXT_FONTS else None


def _font_evidence(soup: BeautifulSoup, document: str, archive: ZipFile) -> dict[int, str | None]:
    """Author font declarations, independently parsed from linked/inline CSS.

    Conflicting matched declarations do not authorize cleanup, even if a CSS
    engine could resolve the conflict. This deliberately differs from the
    production cascade. Conditional/imported font selection is not guessed.
    """
    styles = []
    for element in soup.find_all(['style', 'link']):
        if element.name == 'style':
            css = element.get_text()
        elif element.name == 'link' and 'stylesheet' in element.get('rel', []):
            css = archive.read(_relative(document, element['href'])).decode('utf-8')
        else:
            continue
        if element.get('media') and element['media'].lower() != 'all' and re.search(r'\b(?:font|font-family|all)\s*:', css):
            return {id(tag): None for tag in soup.find_all(True)}
        styles.append(css)
    fonts = {}
    def record(element, value):
        identity = id(element)
        if identity in fonts and fonts[identity] != value:
            fonts[identity] = None
        else:
            fonts[identity] = value
    for rule in tinycss2.parse_stylesheet('\n'.join(styles), skip_comments=True, skip_whitespace=True):
        if rule.type == 'at-rule' and (rule.lower_at_keyword == 'import' or (rule.content and re.search(r'\b(?:font|font-family|all)\s*:', tinycss2.serialize(rule.content)))):
            return {id(element): None for element in soup.find_all(True)}
        if rule.type != 'qualified-rule':
            continue
        if any(token.type == '{} block' and re.search(r'\b(?:font|font-family|all)\s*:', tinycss2.serialize(token.content)) for token in rule.content):
            return {id(element): None for element in soup.find_all(True)}
        declarations = tinycss2.parse_declaration_list(rule.content, skip_comments=True, skip_whitespace=True)
        for declaration in declarations:
            if declaration.type == 'declaration' and declaration.lower_name in {'font-family', 'font', 'all'}:
                for element in soup.select(tinycss2.serialize(rule.prelude)):
                    record(element, tinycss2.serialize(declaration.value).strip().lower() if declaration.lower_name == 'font-family' else None)
    for element in soup.find_all(style=True):
        for declaration in tinycss2.parse_declaration_list(element['style']):
            if declaration.type == 'declaration' and declaration.lower_name in {'font-family', 'font', 'all'}:
                record(element, tinycss2.serialize(declaration.value).strip().lower() if declaration.lower_name == 'font-family' else None)
    return fonts


def _is_code_font(element: Tag, fonts: dict[int, str | None]) -> bool:
    fallback = []
    mono_text = []
    for string in element.find_all(string=True):
        if isinstance(string, (Comment, ProcessingInstruction)):
            continue
        family = next((fonts[id(parent)] for parent in string.parents if id(parent) in fonts), '')
        state = _source_font(family)
        if state is None and str(string).strip():
            return False
        (mono_text if state is True else fallback).append(str(string))
    mono, other = ''.join(mono_text), ''.join(fallback)
    if not mono.strip():
        return False
    if not any(char.isalnum() for char in other):
        return True
    if not re.search(r'[(){}\[\];=/*#$%<>:"\'\\]', mono):
        return False
    raw = ''.join(str(string) for string in element.find_all(string=True) if not isinstance(string, (Comment, ProcessingInstruction)))
    annotated = set()
    for literal in re.finditer(r'/\*.*?\*/|//[^\n]*|(?<!\S)#[^\n]*|"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'', raw, re.S):
        annotated.update(range(literal.start(), literal.end()))
    position = 0
    for string in element.find_all(string=True):
        if isinstance(string, (Comment, ProcessingInstruction)):
            continue
        family = next((fonts[id(parent)] for parent in string.parents if id(parent) in fonts), '')
        if _source_font(family) is not True and any(character.isalnum() and position+index not in annotated for index, character in enumerate(str(string))):
            return False
        position += len(str(string))
    return True


def read_source(path: Path) -> SourceBook:
    with ZipFile(path) as archive:
        rootfiles = ET.fromstring(archive.read('META-INF/container.xml')).findall('.//{*}rootfile')
        if len(rootfiles) != 1:
            raise ValueError('Full audit requires one unambiguous OPF rootfile')
        package_path = rootfiles[0].attrib['full-path']
        package = ET.fromstring(archive.read(package_path))
        metadata_title = package.find('./{*}metadata/{*}title')
        book = SourceBook(''.join(metadata_title.itertext()).strip() if metadata_title is not None else '', package_path)
        book.authors = [''.join(author.itertext()).strip() for author in package.findall('./{*}metadata/{*}creator')]
        language = package.find('./{*}metadata/{*}language')
        book.language = ''.join(language.itertext()).strip() if language is not None else None
        manifest = {item.attrib['id']: item.attrib for item in package.findall('./{*}manifest/{*}item')}
        spine = package.find('./{*}spine')
        if spine is None:
            raise ValueError('Missing source spine')
        if spine.get('toc'):
            ncx_path = _relative(package_path, manifest[spine.attrib['toc']]['href'])
            ncx = ET.fromstring(archive.read(ncx_path))
            def visit(parent, parent_index=None):
                for point in parent.findall('./{*}navPoint'):
                    label = ''.join(point.find('./{*}navLabel').itertext()).strip()
                    content = point.find('./{*}content')
                    href = content.attrib['src'] if content is not None else ''
                    index = len(book.navigation)
                    book.navigation.append({'label': normalize(label), 'path': _relative(ncx_path, href) if href else None, 'fragment': unquote(urlsplit(href).fragment), 'parent': parent_index})
                    visit(point, index)
            navmap = ncx.find('./{*}navMap')
            if navmap is not None:
                visit(navmap)
        else:
            nav_items = [item for item in manifest.values() if 'nav' in item.get('properties', '').split()]
            if len(nav_items) > 1:
                raise ValueError('Ambiguous source navigation documents')
            if nav_items:
                nav_path = _relative(package_path, nav_items[0]['href'])
                tree = ET.fromstring(archive.read(nav_path))
                navs = [element for element in tree.findall('.//{*}nav') if 'toc' in element.get('{http://www.idpf.org/2007/ops}type', '').split() or element.get('role') == 'doc-toc']
                if len(navs) != 1:
                    raise ValueError('Missing or ambiguous source TOC')
                def visit_list(parent, parent_index=None):
                    for entry in parent.findall('./{*}li'):
                        label = entry.find('./{*}a')
                        if label is None:
                            label = entry.find('./{*}span')
                        if label is None:
                            raise ValueError('Navigation item has no source label')
                        href = label.get('href', '')
                        index = len(book.navigation)
                        book.navigation.append({'label': normalize(''.join(label.itertext())), 'path': _relative(nav_path, href) if href else None, 'fragment': unquote(urlsplit(href).fragment), 'parent': parent_index})
                        for children in entry.findall('./{*}ol') + entry.findall('./{*}ul'):
                            visit_list(children, index)
                for listing in navs[0].findall('./{*}ol') + navs[0].findall('./{*}ul'):
                    visit_list(listing)
        book.labels = {normalize(book.title), *(item['label'] for item in book.navigation)}
        for position, ref in enumerate(spine.findall('./{*}itemref'), 1):
            item = manifest[ref.attrib['idref']]
            if 'nav' in item.get('properties', '').split():
                continue
            document = _relative(package_path, item['href'])
            book.documents.append(document)
            soup = BeautifulSoup(archive.read(document), 'html.parser')
            fonts = _font_evidence(soup, document, archive)
            book.document_labels[document] = {normalize(f'正文 {position}')}
            if soup.title:
                book.labels.add(normalize(soup.title.get_text()))
                book.document_labels[document].add(normalize(soup.title.get_text()))
            book.labels.add(normalize(f'正文 {position}'))
            body = soup.body or soup
            for line_break in body.find_all('br'):
                line_break.replace_with('\n')
            for image in body.find_all(['img', 'image']):
                target = image.get('src') if image.name == 'img' else image.get('xlink:href') or image.get('href')
                if target:
                    book.images.append(_digest(archive.read(_relative(document, target))))
            for hidden in soup.find_all(['script', 'style', 'iframe', 'object', 'embed']):
                hidden.decompose()
            groups = []
            for string in body.find_all(string=True):
                if isinstance(string, (Comment, ProcessingInstruction)):
                    continue
                owner = string.find_parent(_BLOCKS) or body
                if groups and groups[-1][0] is owner:
                    groups[-1][1].append(str(string))
                else:
                    groups.append((owner, [str(string)]))
            for element, strings in groups:
                raw = ''.join(strings)
                text = normalize(raw)
                if not text:
                    continue
                if re.fullmatch(r'h[1-6]', element.name):
                    book.labels.add(text)
                    book.document_labels[document].add(text)
                match = re.fullmatch(r'\s*(\d+)(?:[ \t\u00a0]+(.*))?', raw, re.S)
                unit = SourceUnit(document, text, element.name)
                if match:
                    unit.number, unit.payload = int(match[1]), normalize(match[2] or '')
                    unit.monospace = _is_code_font(element, fonts)
                book.units.append(unit)
        for index, unit in enumerate(book.units):
            unit.allow_number_cleanup = unit.monospace and any(
                0 <= neighbour < len(book.units)
                and book.units[neighbour].document == unit.document
                and book.units[neighbour].monospace
                and book.units[neighbour].number == unit.number + offset
                for neighbour, offset in ((index - 1, -1), (index + 1, 1))
            )
        return book


def align(units: list[SourceUnit], text: str, heading_ranges: list[tuple[int, int]]) -> dict:
    """Consume every source unit in order and reject non-heading output gaps."""
    cursor = 0
    missing, gaps, cleaned = [], [], []
    for index, unit in enumerate(units):
        candidates = [(text.find(unit.text, cursor), False, unit.text)]
        if unit.allow_number_cleanup:
            candidates.append((text.find(unit.payload, cursor) if unit.payload else cursor, True, unit.payload))
        candidates = [candidate for candidate in candidates if candidate[0] >= 0]
        if not candidates:
            missing.append({'unit': index, 'source_hash': _digest(unit.text), 'document': unit.document})
            continue
        position, is_cleaned, literal = min(candidates, key=lambda candidate: (candidate[0], candidate[1]))
        if position > cursor:
            gaps.append((cursor, position))
        if is_cleaned:
            cleaned.append(index)
        cursor = position + len(literal)
    if cursor < len(text):
        gaps.append((cursor, len(text)))
    def heading_only(start, end):
        for left, right in heading_ranges:
            if left <= start < right:
                start = min(end, right)
            if start == end:
                return True
        return False
    unexpected = [{'position': left, 'characters': right-left, 'sha256': _digest(text[left:right])} for left, right in gaps if not heading_only(left, right)]
    return {'passed': not missing and not unexpected, 'source_units': len(units), 'matched_units': len(units)-len(missing), 'cleaned_line_numbers': len(cleaned), 'cleaned_units': cleaned, 'missing': missing, 'unexpected_output': unexpected, 'generated_heading_gap_characters': sum(right-left for left, right in gaps if heading_only(left, right))}


def _front_matter(book: SourceBook, snapshot: dict) -> str:
    lines = ['---', 'title: ' + json.dumps(book.title, ensure_ascii=False)]
    if book.authors:
        lines += ['authors:', *['  - ' + json.dumps(author, ensure_ascii=False) for author in book.authors]]
    if book.language:
        lines.append('language: ' + json.dumps(book.language, ensure_ascii=False))
    for name in ('book_id', 'edition_id', 'revision_id'):
        try:
            identity = str(uuid.UUID(snapshot[name]))
        except (KeyError, TypeError, ValueError, AttributeError) as error:
            raise ValueError(f'Missing or invalid required snapshot identity: {name}') from error
        lines.append(name + ': ' + identity)
    return '\n'.join([*lines, '---'])


def _chapter_anchor(path: Path) -> str:
    soup = BeautifulSoup(markdown.markdown(path.read_text(encoding='utf-8'), extensions=['tables', 'fenced_code']), 'html.parser')
    anchor = soup.find('a', id=re.compile('^sec-'))
    if anchor is None:
        raise ValueError('Navigation target has no chapter anchor')
    return anchor['id']


def _plain_navigation(element: Tag, expected: dict[str, str], *, path: Path, prelude=False) -> None:
    if element.find(['img', 'svg', 'math', 'video', 'audio', 'iframe', 'object', 'embed', 'table', 'pre', 'script', 'style']):
        raise ValueError('Unexpected non-navigation content')
    anchors = element.find_all('a', recursive=False)
    residual = ''.join(child.get_text() if isinstance(child, Tag) else str(child) for child in element.children if child not in anchors)
    if normalize(residual) != ('·' if len(anchors) == 2 else ''):
        raise ValueError('Unexpected prose in navigation')
    for anchor in anchors:
        destination = urlsplit(anchor.get('href', ''))
        target = (path.parent/unquote(destination.path)).resolve()
        if destination.scheme or destination.netloc or not target.is_relative_to(path.parent.parent.resolve()) or not target.is_file():
            raise ValueError('Missing or invalid navigation target file')
        if destination.fragment:
            target_soup = BeautifulSoup(markdown.markdown(target.read_text(encoding='utf-8'), extensions=['tables', 'fenced_code']), 'html.parser')
            if target_soup.find(id=destination.fragment) is None:
                raise ValueError('Navigation anchor is absent from its target file')
    if prelude:
        if len(anchors) != 2 or anchors[0].get('href') != '../TOC.md' or normalize(anchors[0].get_text()) != '总目录' or normalize(anchors[1].get_text()) != '整体版对应位置':
            raise ValueError('Unexpected split-view prelude')
        destination = urlsplit(anchors[1].get('href', ''))
        if destination.path != '../book.md' or destination.fragment not in expected or destination.fragment != _chapter_anchor(path):
            raise ValueError('Invalid whole-book navigation target')
        return
    if not 1 <= len(anchors) <= 2:
        raise ValueError('Invalid adjacent-chapter navigation')
    siblings = sorted(path.parent.glob('*.md'))
    index = siblings.index(path)
    neighbours = []
    for sibling_index, previous in ((index-1, True), (index+1, False)):
        if 0 <= sibling_index < len(siblings):
            sibling = siblings[sibling_index]
            identity = _chapter_anchor(sibling)
            if identity not in expected:
                raise ValueError('Unknown adjacent-chapter identity')
            label = ('←'+expected[identity]) if previous else (expected[identity]+'→')
            neighbours.append((sibling.name, identity, label))
    actual = [(unquote(urlsplit(anchor.get('href', '')).path), urlsplit(anchor.get('href', '')).fragment, normalize(anchor.get_text())) for anchor in anchors]
    if actual != neighbours:
        raise ValueError('Adjacent navigation differs from actual chapter order')


def _prelude(elements, expected: dict[str, str], title: str, split: bool, parents: dict[str, str | None], path: Path) -> None:
    if not elements:
        raise ValueError('Missing required Markdown title/TOC or split-view navigation')
    if any(not isinstance(element, Tag) for element in elements):
        raise ValueError('Unexpected prose before content')
    if any(element.find(['img', 'svg', 'math', 'video', 'audio', 'iframe', 'object', 'embed', 'table', 'pre', 'script', 'style']) for element in elements):
        raise ValueError('Unexpected non-navigation content before body')
    if split:
        if len(elements) != 1 or elements[0].name != 'p':
            raise ValueError('Unexpected split-view prelude')
        _plain_navigation(elements[0], expected, path=path, prelude=True)
        return
    if len(elements) != 3 or [element.name for element in elements] != ['h1', 'h2', 'ul'] or normalize(elements[0].get_text()) != normalize(title) or normalize(elements[1].get_text()) != '目录':
        raise ValueError('Unexpected prose or structure before whole-book content')
    seen = []
    for listing in [elements[2], *elements[2].find_all('ul')]:
        if any((isinstance(child, Tag) and child.name != 'li') or (not isinstance(child, Tag) and normalize(str(child))) for child in listing.children):
            raise ValueError('Unexpected prose in generated TOC')
    for entry in elements[2].find_all('li'):
        anchors = entry.find_all('a', recursive=False)
        if len(anchors) != 1:
            raise ValueError('Invalid generated TOC entry')
        anchor = anchors[0]
        identity = anchor.get('href', '').removeprefix('#')
        label = normalize(''.join(child.get_text() if isinstance(child, Tag) else str(child) for child in entry.children if child is not anchor and not (isinstance(child, Tag) and child.name == 'ul')))
        if identity not in expected or label != expected[identity]+'—' or normalize(anchor.get_text()) != '定位':
            raise ValueError('Generated TOC contains non-source prose')
        parent = entry.find_parent('li')
        parent_id = parent.find('a', recursive=False).get('href', '').removeprefix('#') if parent else None
        if identity in parents and parent_id != parents[identity]:
            raise ValueError('Generated TOC hierarchy differs from source-backed nodes')
        seen.append(identity)
    if sorted(seen) != sorted(expected):
        raise ValueError('Generated TOC has omitted/duplicate entries')


def _view(path: Path, expected: dict[str, str], *, book_title='', front_matter='', split=False, parents=None) -> tuple[str, list[tuple[int, int]], list[str], list[str]]:
    is_markdown = path.suffix == '.md'
    raw = path.read_text(encoding='utf-8')
    if is_markdown:
        if not split:
            if not front_matter or not raw.startswith(front_matter+'\n'):
                raise ValueError('Markdown metadata differs from the original source or contains added prose')
            raw = raw[len(front_matter):].lstrip('\n')
        soup = BeautifulSoup(markdown.markdown(raw, extensions=['tables', 'fenced_code']), 'html.parser')
    else:
        soup = BeautifulSoup(raw, 'html.parser')
    container = soup.body or soup
    children = [child for child in container.children if not isinstance(child, Comment) and (isinstance(child, Tag) or normalize(str(child)))]
    if is_markdown:
        start = next((index for index, child in enumerate(children) if isinstance(child, Tag) and (child.find('a', id=re.compile('^sec-')) or child.name == 'a' and str(child.get('id', '')).startswith('sec-'))), None)
        if start is None:
            raise ValueError('Markdown view has no explicit content start')
        _prelude(children[:start], expected, book_title, split, parents or {}, path)
        children = children[start:]
        if split:
            has_footer = len(children) >= 2 and isinstance(children[-2], Tag) and children[-2].name == 'hr' and isinstance(children[-1], Tag) and children[-1].name == 'p'
            if has_footer:
                _plain_navigation(children[-1], expected, path=path)
                children = children[:-2]
            elif len(list(path.parent.glob('*.md'))) > 1:
                raise ValueError('Missing adjacent-chapter navigation')
    text, ranges, images, seen = '', [], [], []
    pending_heading = None
    for element in children:
        if not isinstance(element, Tag):
            text += normalize(str(element))
            continue
        anchors = ([element] if element.name == 'a' and str(element.get('id', '')).startswith('sec-') else []) + element.find_all('a', id=re.compile('^sec-'))
        if is_markdown and anchors:
            if len(anchors) != 1 or pending_heading is not None or anchors[0]['id'] not in expected:
                raise ValueError('Unknown/duplicate node anchor')
            pending_heading = anchors[0]['id']
        heading = bool(re.fullmatch('h[1-6]', element.name))
        identity = pending_heading if is_markdown and heading else element.get('id', '') if heading else ''
        is_node = heading and (bool(pending_heading) if is_markdown else str(identity).startswith('sec-'))
        value = normalize(element.get_text())
        if is_node:
            if identity not in expected or value != expected[identity] or identity in seen:
                raise ValueError('Generated heading identity/title is not a validated source node')
            seen.append(identity)
            ranges.append((len(text), len(text)+len(value)))
            pending_heading = None
        elif not is_markdown and heading and element.name == 'h1' and not text:
            if value != normalize(book_title):
                raise ValueError('Unexpected generated book title')
            ranges.append((len(text), len(text)+len(value)))
        text += value
        for image in ([element] if element.name in {'img', 'image'} else []) + element.find_all(['img', 'image']):
            target = image.get('src') if image.name == 'img' else image.get('xlink:href') or image.get('href')
            if target:
                resource = (path.parent / unquote(urlsplit(target).path)).resolve()
                if not resource.is_relative_to((path.parent.parent if split else path.parent).resolve()):
                    raise ValueError('Output image escaped the export directory')
                images.append(_digest(resource.read_bytes()))
    if pending_heading is not None:
        raise ValueError('Node anchor has no heading')
    return text, ranges, images, seen


def inspect_book(source: Path, export: Path, *, pdf: bool = False) -> dict:
    book = read_source(source)
    snapshot = json.loads((export / 'book.json').read_text())
    source_paths = [_relative(book.package, fragment['source']['path']) for fragment in snapshot['content']]
    compact_paths = [path for index, path in enumerate(source_paths) if index == 0 or source_paths[index-1] != path]
    nodes = snapshot['nodes']
    matched = {}
    navigation_errors = []
    for index, item in enumerate(book.navigation):
        parent = matched.get(item['parent'])
        candidates = [node for node in nodes if node['node_id'] not in matched.values() and normalize(node['title_original']) == item['label'] and (_relative(book.package, node['source']['path']) if node['source']['path'] else None) == item['path'] and (node['source']['fragment'] or '') == item['fragment'] and node['parent_id'] == parent]
        if len(candidates) != 1:
            navigation_errors.append(index)
        else:
            matched[index] = candidates[0]['node_id']
    invalid_nodes = []
    referenced = {item['path'] for item in book.navigation}
    unlisted_paths = set()
    for node in nodes:
        if normalize(node.get('title_display', node['title_original'])) != normalize(node['title_original']):
            invalid_nodes.append(node['node_id'])
        if node['node_id'] in matched.values():
            continue
        path = _relative(book.package, node['source']['path']) if node['source']['path'] else None
        owns_content = any(fragment.get('node_id') == node['node_id'] and _relative(book.package, fragment['source']['path']) == path for fragment in snapshot['content'])
        if path not in book.documents or path in referenced or path in unlisted_paths or node['parent_id'] is not None or node['source']['fragment'] or normalize(node['title_original']) not in book.document_labels.get(path, set()) or not owns_content:
            invalid_nodes.append(node['node_id'])
        unlisted_paths.add(path)
    expected = {'sec-'+node['node_id'].replace('-', ''): normalize(node.get('title_display', node['title_original'])) for node in nodes}
    parents = {'sec-'+node['node_id'].replace('-', ''): 'sec-'+node['parent_id'].replace('-', '') if node['parent_id'] else None for node in nodes}
    if len(expected) != len(nodes) or any(fragment.get('node_id') not in {node['node_id'] for node in nodes} for fragment in snapshot['content']):
        invalid_nodes.append('duplicate_or_unknown_identity')
    report = {'source_sha256': _digest(source.read_bytes()), 'source_documents': len(book.documents), 'source_units': len(book.units), 'source_normalized_characters': sum(len(unit.text) for unit in book.units), 'source_images': len(book.images), 'source_navigation_entries': len(book.navigation), 'structure': {'spine_order_preserved': compact_paths == book.documents, 'navigation_entries_matched': len(matched), 'navigation_errors': navigation_errors, 'invalid_output_nodes': invalid_nodes, 'output_nodes': len(nodes)}, 'views': {}, 'normalizations': ['Unicode NFKC and whitespace', 'Printed number prefix only with unambiguous source monospace evidence and consecutive source numbering'], 'limitations': ['Text equality does not prove every page layout, hidden-content policy or code indentation', 'Conflicting, conditional or imported font evidence does not authorize number cleanup', 'PDF text extraction is compared with the independently audited HTML view; selected pages need separate visual inspection']}
    html_text = None
    for view, paths in [('html', [export/'book.html']), ('md_single', [export/'book.md']), ('md_chapters', sorted((export/'chapters').glob('*.md')))]:
        texts, ranges, images = [], [], []
        offset, seen = 0, []
        try:
            for path in paths:
                text, headings, resources, identities = _view(path, expected, book_title=book.title, front_matter=_front_matter(book, snapshot), split=view == 'md_chapters', parents=parents)
                texts.append(text)
                ranges.extend((left+offset, right+offset) for left, right in headings)
                images.extend(resources)
                seen.extend(identities)
                offset += len(text)
        except (ValueError, OSError) as error:
            report['views'][view] = {'passed': False, 'error': str(error)}
            continue
        text = ''.join(texts)
        result = align(book.units, text, ranges)
        result['image_bytes_and_order_match'] = images == book.images
        result['heading_identities_once'] = sorted(seen) == sorted(expected)
        result['passed'] &= result['image_bytes_and_order_match'] and result['heading_identities_once']
        report['views'][view] = result
        if view == 'html':
            html_text = text
    if pdf:
        import pymupdf
        with pymupdf.open(export/'book.pdf') as document:
            text = normalize(''.join(page.get_text() for page in document))
            report['views']['pdf'] = {'passed': text == html_text, 'normalized_text_equals_audited_html': text == html_text, 'characters': len(text), 'pages': len(document)}
    report['passed'] = compact_paths == book.documents and not navigation_errors and not invalid_nodes and all(result['passed'] for result in report['views'].values())
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--export', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--pdf', action='store_true')
    args = parser.parse_args()
    report = inspect_book(args.input, args.export, pdf=args.pdf)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps({**{key: value for key, value in report.items() if key != 'views'}, 'views': {name: {key: value for key, value in result.items() if key != 'cleaned_units'} for name, result in report['views'].items()}}, ensure_ascii=False))
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
