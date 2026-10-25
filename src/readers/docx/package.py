"""OOXML source evidence that the HTML extractor does not retain itself.

This prepares one in-memory input for Mammoth; it never writes final views.
Source text checks also detect unreported losses in unsupported document parts.
"""

from __future__ import annotations

import io
import posixpath
import shutil
import unicodedata
import uuid
from collections import defaultdict
from dataclasses import dataclass
from urllib.parse import unquote, urlsplit
from xml.etree import ElementTree as ET
from zipfile import ZIP_STORED, ZipFile

from mammoth.docx.dingbats import dingbats

from ...domain.book import Diagnostic

W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
MC = '{http://schemas.openxmlformats.org/markup-compatibility/2006}'
MATH = '{http://schemas.openxmlformats.org/officeDocument/2006/math}'
A = '{http://schemas.openxmlformats.org/drawingml/2006/main}'
REL = '{http://schemas.openxmlformats.org/package/2006/relationships}'
SUPPORTED_CHOICES = {W[1:-1], 'http://schemas.openxmlformats.org/drawingml/2006/main', 'http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing', 'http://schemas.openxmlformats.org/drawingml/2006/picture'}
STRICT_NAMESPACES = {
    'http://purl.oclc.org/ooxml/wordprocessingml/main': W[1:-1],
    'http://purl.oclc.org/ooxml/officeDocument/relationships': 'http://schemas.openxmlformats.org/officeDocument/2006/relationships',
    **{f'http://purl.oclc.org/ooxml/drawingml/{name}': f'http://schemas.openxmlformats.org/drawingml/2006/{name}' for name in ('main', 'wordprocessingDrawing', 'picture')},
}


def _canonical_name(name):
    if name.startswith('{'):
        namespace, local = name[1:].split('}', 1)
        return '{'+STRICT_NAMESPACES.get(namespace, namespace)+'}'+local
    return name


def normalized_text(value: str) -> str:
    return ''.join(unicodedata.normalize('NFKC', value).split())


def _parse(content: bytes):
    bindings, pending, stack = {}, {}, [{}]
    normalized = False
    parser = ET.iterparse(io.BytesIO(content), events=('start-ns', 'start', 'end'))
    for event, value in parser:
        if event == 'start-ns':
            pending[value[0]] = STRICT_NAMESPACES.get(value[1], value[1])
        elif event == 'start':
            tag = _canonical_name(value.tag)
            attributes = {_canonical_name(key): item for key, item in value.attrib.items()}
            normalized |= tag != value.tag or attributes != value.attrib
            value.tag = tag
            value.attrib = attributes
            current = {**stack[-1], **pending}
            bindings[id(value)] = current
            stack.append(current)
            pending.clear()
        else:
            stack.pop()
    return parser.root, bindings, normalized


def _select_alternatives(root, bindings, diagnostics, path):
    changed = False
    def visit(parent):
        nonlocal changed
        for child in list(parent):
            if child.tag == MC+'AlternateContent':
                chosen = next((choice for choice in child.findall(MC+'Choice') if choice.get('Requires') and all(bindings[id(choice)].get(prefix) in SUPPORTED_CHOICES for prefix in choice.get('Requires').split())), None)
                if chosen is None:
                    chosen = child.find(MC+'Fallback')
                index = list(parent).index(child)
                parent.remove(child)
                changed = True
                if chosen is None:
                    diagnostics.append(Diagnostic('docx_alternate_content', '可选内容没有可理解的分支或回退内容，未保留。', severity='error', source_path=path))
                    continue
                visit(chosen)
                for offset, element in enumerate(list(chosen)):
                    parent.insert(index+offset, element)
            else:
                visit(child)
    visit(root)
    return changed


def _source_text(element):
    if element.tag in {W+'del', W+'moveFrom'}:
        return ''
    if element.tag in {W+'t', MATH+'t', A+'t'}:
        return element.text or ''
    if element.tag == W+'noBreakHyphen':
        return '\u2011'
    if element.tag == W+'softHyphen':
        return '\u00ad'
    if element.tag == W+'sym':
        # Share the extractor's font mapping data, not its parsed output.
        # Unknown symbols remain evidence of loss even if it emits no warning.
        value = int(element.get(W+'char'), 16)
        key = value & 0xff if 0xf000 <= value <= 0xf0ff else value
        codepoint = dingbats.get((element.get(W+'font'), key))
        return chr(codepoint) if codepoint is not None else '\ufffd'
    return ''.join(_source_text(child) for child in element)


def _has_visible_content(element):
    visible = {W+name for name in ('drawing', 'pict', 'object', 'sym', 'fldSimple', 'fldChar', 'instrText')}
    return bool(normalized_text(_source_text(element))) or any(node.tag in visible for node in element.iter())


def _relationships(archive, path):
    if path not in archive.namelist():
        return []
    return list(ET.fromstring(archive.read(path)).findall(REL+'Relationship'))


def _part(base, target):
    return posixpath.normpath(posixpath.join(posixpath.dirname(base), unquote(target))).lstrip('/')


def _value(element, name, default=None):
    child = element.find(W+name) if element is not None else None
    return child.get(W+'val', default) if child is not None else default


@dataclass
class ListHint:
    value: int
    kind: str
    level: int
    text: str
    instance: str


@dataclass
class PreparedDocx:
    document_path: str
    stream: io.BytesIO | None
    expected_text: str
    list_hints: dict[str, ListHint]


def prepare_docx(archive: ZipFile, edition_id: str, diagnostics: list[Diagnostic]) -> PreparedDocx:
    names = set(archive.namelist())
    roots = _relationships(archive, '_rels/.rels')
    document_path = next((_part('', relation.get('Target', '')) for relation in roots if relation.get('Type', '').endswith('/officeDocument') and _part('', relation.get('Target', '')) in names), 'word/document.xml')
    relations_path = posixpath.join(posixpath.dirname(document_path), '_rels', posixpath.basename(document_path)+'.rels')
    relations = _relationships(archive, relations_path)
    parts = {}
    for relation in relations:
        target = relation.get('Target', '')
        if relation.get('TargetMode') != 'External' and not urlsplit(target).scheme:
            path = _part(document_path, target)
            if path in names:
                parts.setdefault(relation.get('Type', '').rsplit('/', 1)[-1], []).append(path)
    def optional(name):
        return next(iter(parts.get(name, [])), f'word/{name}.xml')
    styles = _parse(archive.read(optional('styles')))[0] if optional('styles') in names else ET.Element('styles')
    numbering = _parse(archive.read(optional('numbering')))[0] if optional('numbering') in names else ET.Element('numbering')
    style_map = {style.get(W+'styleId'): style for style in styles.findall(W+'style')}
    def num_properties(paragraph):
        properties = paragraph.find(W+'pPr')
        result = {}
        seen = set()
        def inherit(style_id):
            if style_id in seen or style_id not in style_map:
                return
            seen.add(style_id)
            style = style_map[style_id]
            inherit(_value(style, 'basedOn'))
            apply(style.find(W+'pPr'))
        def apply(props):
            number = props.find(W+'numPr') if props is not None else None
            for key in ('numId', 'ilvl'):
                value = _value(number, key)
                if value is not None:
                    result[key] = value
        inherit(_value(properties, 'pStyle'))
        apply(properties)
        return result
    abstracts = {item.get(W+'abstractNumId'): item for item in numbering.findall(W+'abstractNum')}
    levels = {}
    for number in numbering.findall(W+'num'):
        abstract = abstracts.get(_value(number, 'abstractNumId'))
        entries = {item.get(W+'ilvl'): item for item in abstract.findall(W+'lvl')} if abstract is not None else {}
        overrides = {item.get(W+'ilvl'): item for item in number.findall(W+'lvlOverride')}
        for level in set(entries)|set(overrides):
            override = overrides.get(level)
            replacement = override.find(W+'lvl') if override is not None else None
            definition = replacement if replacement is not None else entries.get(level)
            start = _value(override, 'startOverride', _value(definition, 'start', '1'))
            levels[(number.get(W+'numId'), level)] = (int(start), _value(definition, 'numFmt', 'decimal'), _value(definition, 'lvlText', f'%{int(level)+1}.'), _value(definition, 'lvlRestart'))
    modified, trees, hints = {}, {}, {}
    # Mammoth otherwise resolves embedded images against a literal "word/".
    # Root-relative package URIs also handle nested/custom part directories.
    for name in names:
        if not name.endswith('.rels') or not (name.startswith('_rels/') or '/_rels/' in name):
            continue
        if name == '_rels/.rels':
            owner = ''
        else:
            directory, filename = name.rsplit('_rels/', 1)
            owner = posixpath.join(directory, filename[:-5])
        relationships = ET.fromstring(archive.read(name))
        changed = False
        for relation in relationships.findall(REL+'Relationship'):
            kind = relation.get('Type', '')
            # Mammoth finds package parts by Transitional relationship types.
            strict = 'http://purl.oclc.org/ooxml/officeDocument/relationships/'
            if kind.startswith(strict) or kind == 'http://purl.oclc.org/ooxml/relationships/officeDocument':
                relation.set('Type', 'http://schemas.openxmlformats.org/officeDocument/2006/relationships/'+kind.rsplit('/', 1)[-1])
                changed = True
            if not relation.get('Type', '').endswith('/image') or relation.get('TargetMode') == 'External':
                continue
            target = relation.get('Target', '')
            parsed = urlsplit(target)
            if parsed.scheme or parsed.netloc:
                continue
            resolved = _part(owner, parsed.path)
            if resolved in names:
                relation.set('Target', '/'+resolved)
                changed = True
                if parsed.fragment:
                    diagnostics.append(Diagnostic('docx_image_fragment', '嵌入图像的 URI 片段尚不能完整保留。', severity='error', source_path=owner))
        if changed:
            modified[name] = ET.tostring(relationships, encoding='utf-8', xml_declaration=True)
    def read_story(path, note_kind=None, active_ids=None):
        if path in trees:
            return trees[path]
        tree, bindings, changed = _parse(archive.read(path))
        changed |= _select_alternatives(tree, bindings, diagnostics, path)
        counts = defaultdict(dict)
        paragraphs = tree.iter(W+'p') if active_ids is None else (paragraph for note in tree.findall(W+note_kind) if note.get(W+'id') in active_ids for paragraph in note.iter(W+'p'))
        for index, paragraph in enumerate(paragraphs):
            props = num_properties(paragraph)
            num_id, level = props.get('numId'), props.get('ilvl', '0')
            if not num_id or num_id == '0' or (num_id, level) not in levels:
                continue
            start, kind, text, restart = levels[(num_id, level)]
            number = counts[num_id].get(level, start-1)+1
            counts[num_id][level] = number
            for nested in list(counts[num_id]):
                if int(nested) > int(level):
                    nested_restart = levels.get((num_id, nested), (1, '', '', None))[3]
                    if nested_restart != '0':
                        del counts[num_id][nested]
            if restart not in {None, '0', level}:
                diagnostics.append(Diagnostic('docx_numbering', '非默认列表重启级别尚不能完整保留。', severity='error', source_path=path))
            anchor = 'any-to-md-list-'+uuid.uuid5(uuid.UUID(edition_id), f'{path}:{index}').hex
            marker = ET.Element(W+'bookmarkStart', {W+'id': str(1000000+index), W+'name': anchor})
            paragraph.insert(1 if paragraph.find(W+'pPr') is not None else 0, marker)
            hints[anchor] = ListHint(number, kind, int(level), text, num_id)
            changed = True
        if changed:
            modified[path] = ET.tostring(tree, encoding='utf-8', xml_declaration=True)
        trees[path] = tree
        return tree
    main = read_story(document_path)
    expected = _source_text(main)
    note_texts = {}
    for kind in ('footnote', 'endnote', 'comment'):
        ids = list(dict.fromkeys(element.get(W+'id') for element in main.iter(W+kind+'Reference')))
        path = optional(kind+'s')
        if ids and path in names:
            tree = read_story(path, kind, set(ids))
            notes = {element.get(W+'id'): element for element in tree.findall(W+kind)}
            note_texts.update({(kind, identifier): _source_text(notes[identifier]) for identifier in ids if identifier in notes})
    note_order = list(dict.fromkeys((element.tag[len(W):-len('Reference')], element.get(W+'id')) for element in main.iter() if element.tag in {W+'footnoteReference', W+'endnoteReference'}))
    comment_order = list(dict.fromkeys(('comment', element.get(W+'id')) for element in main.iter(W+'commentReference')))
    expected += ''.join(note_texts.get(key, '') for key in [*note_order, *comment_order])
    for kind in ('header', 'footer'):
        for path in parts.get(kind, []):
            if _has_visible_content(read_story(path)):
                diagnostics.append(Diagnostic('docx_unsupported_part', '页眉/页脚中的文字、图形或字段尚不能完整保留到重排版输出。', severity='error', source_path=path))
    stream = None
    if modified:
        stream = io.BytesIO()
        with ZipFile(stream, 'w', compression=ZIP_STORED) as target:
            for item in archive.infolist():
                if item.filename in modified:
                    target.writestr(item.filename, modified[item.filename])
                else:
                    with archive.open(item) as source, target.open(item.filename, 'w') as output:
                        shutil.copyfileobj(source, output)
        stream.seek(0)
    return PreparedDocx(document_path, stream, normalized_text(expected), hints)
