"""Independent sources and observations for UC-08; no production-derived oracle."""

import hashlib
import json
import re
import sqlite3
from contextlib import contextmanager, closing, ExitStack
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile

from bs4 import BeautifulSoup, NavigableString
from tests.common.fixtures import _archive, _html, _image, _docx
from tests.common.docx_fixtures import paragraph, W
from tests.common.inspection import _soup, image_identity

TITLES = [('Arrival',), ('Arrival', 'Details'), ('Departure',)]
TEXT = [('Arrival', 'Opening paragraph.'), ('Details', 'Detail paragraph.'),
        ('Details', 'List paragraph.'), ('Departure', 'Closing paragraph.'),
        ('Departure', 'Cell paragraph.')]
BODY = '''<h1 id="arrival">Arrival</h1><p>Opening paragraph.</p>
<figure><img src="red.png" alt="red"/><figcaption>Red caption.</figcaption></figure>
<h2 id="details">Details</h2><p>Detail paragraph.</p>
<ul><li>List paragraph.<img src="red.png" alt="red"/></li></ul>
<h1 id="departure">Departure</h1><p>Closing paragraph.</p>
<table><tr><td>Cell paragraph.<img src="blue.png" alt="blue"/></td></tr></table>'''


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def expected_locations(reference, kind):
    document = reference['source_documents'][kind]
    suffixes = reference['docx_image_positions'] if kind == 'docx' else reference['dom_image_positions']
    return [document+'#'+position for position in suffixes]


def expected_flow(reference, kind):
    red, blue = (reference['pixel_sha256'][c] for c in ('red','blue'))
    return [('text','Arrival','Opening paragraph.'),
            ('image','Arrival','p' if kind=='docx' else 'figure',red),
            ('caption','Arrival','Red caption.'),
            ('text','Details','Detail paragraph.'), ('text','Details','List paragraph.'),
            ('image','Details','li',red), ('text','Departure','Closing paragraph.'),
            ('text','Departure','Cell paragraph.'), ('image','Departure','td',blue)]


def sources(root: Path) -> dict:
    """Write reference BEFORE executing any application; source anchors distinguish occurrences."""
    root.mkdir(parents=True)
    red, blue = _image('red'), _image('blue')
    html = _html(BODY, 'Journey')
    (root / 'journey.html').write_text(html, encoding='utf-8')
    (root / 'red.png').write_bytes(red)
    (root / 'blue.png').write_bytes(blue)
    from src.adapters.html_source import pack_html_source
    # Packaging only; expected structure is independently authored above.
    (root / 'journey.zip').write_bytes(pack_html_source('journey.html', html.encode(), {'red.png': red, 'blue.png': blue}))
    _archive(root / 'journey.epub', {
        'mimetype': 'application/epub+zip',
        'META-INF/container.xml': '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile full-path="book.opf" media-type="application/oebps-package+xml"/></rootfiles></container>',
        'book.opf': '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="id"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:identifier id="id">reuse-independent</dc:identifier><dc:title>Journey</dc:title><dc:language>en</dc:language></metadata><manifest><item id="doc" href="journey.xhtml" media-type="application/xhtml+xml"/><item id="nav" href="nav.xhtml" properties="nav" media-type="application/xhtml+xml"/><item id="red" href="red.png" media-type="image/png"/><item id="blue" href="blue.png" media-type="image/png"/></manifest><spine><itemref idref="doc"/></spine></package>',
        'nav.xhtml': _html('<nav xmlns:epub="http://www.idpf.org/2007/ops" epub:type="toc"><ol><li><a href="journey.xhtml#arrival">Arrival</a><ol><li><a href="journey.xhtml#details">Details</a></li></ol></li><li><a href="journey.xhtml#departure">Departure</a></li></ol></nav>'),
        'journey.xhtml': html, 'red.png': red, 'blue.png': blue,
    })
    docx = root / 'journey.docx'
    _docx(docx, red)
    with ZipFile(docx) as archive:
        members = {name: archive.read(name) for name in archive.namelist()}
    original = members['word/document.xml'].decode()
    drawing = '<w:r><w:drawing>' + original.split('<w:drawing>')[1].split('</w:drawing>')[0] + '</w:drawing></w:r>'
    numbered = '<w:p><w:pPr><w:numPr><w:ilvl w:val="0"/><w:numId w:val="1"/></w:numPr></w:pPr><w:r><w:t>List paragraph.</w:t></w:r>' + drawing + '</w:p>'
    body = paragraph('Arrival', 'Heading1') + paragraph('Opening paragraph.') + '<w:p>'+drawing+'</w:p>' + paragraph('Red caption.') + paragraph('Details', 'Heading2') + paragraph('Detail paragraph.') + numbered + paragraph('Departure', 'Heading1') + paragraph('Closing paragraph.')
    body += '<w:tbl><w:tblGrid><w:gridCol w:w="2000"/></w:tblGrid><w:tr><w:tc><w:p><w:r><w:t>Cell paragraph.</w:t></w:r>'+drawing.replace('img1', 'img2')+'</w:p></w:tc></w:tr></w:tbl>'
    members['word/document.xml'] = original.split('<w:body>')[0] + '<w:body>'+body+'<w:sectPr/></w:body></w:document>'
    members['word/styles.xml'] = f'<w:styles xmlns:w="{W}"><w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/></w:style><w:style w:type="paragraph" w:styleId="Heading2"><w:name w:val="heading 2"/></w:style></w:styles>'
    members['word/numbering.xml'] = f'<w:numbering xmlns:w="{W}"><w:abstractNum w:abstractNumId="0"><w:lvl w:ilvl="0"><w:numFmt w:val="bullet"/><w:lvlText w:val="•"/></w:lvl></w:abstractNum><w:num w:numId="1"><w:abstractNumId w:val="0"/></w:num></w:numbering>'
    members['word/media/blue.png'] = blue
    members['word/_rels/document.xml.rels'] = members['word/_rels/document.xml.rels'].decode().replace('</Relationships>', '<Relationship Id="img2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="media/blue.png"/><Relationship Id="styles" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/><Relationship Id="numbering" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/numbering" Target="numbering.xml"/></Relationships>')
    _archive(docx, members)
    reference = {'title_paths': TITLES, 'direct_body_order': TEXT,
                 'image_occurrences': [{'owner': 'Arrival', 'container': 'figure', 'color': 'red', 'caption': 'Red caption.', 'source': 'journey.html#arrival/figure[1]'},
                                       {'owner': 'Details', 'container': 'li', 'color': 'red', 'caption': None, 'source': 'journey.html#details/ul/li[1]'},
                                       {'owner': 'Departure', 'container': 'td', 'color': 'blue', 'caption': None, 'source': 'journey.html#departure/table/tr/td[1]'}],
                 'docx_locations': ['word/document.xml:p[3]', 'word/document.xml:p[7]', 'word/document.xml:tbl[1]/tr[1]/tc[1]/p[1]'],
                 'dom_image_positions': ['body/figure[1]/img[1]', 'body/ul[1]/li[1]/img[1]', 'body/table[1]/tr[1]/td[1]/img[1]'],
                 'docx_image_positions': ['body/p[3]/r[1]/drawing[1]', 'body/p[7]/r[2]/drawing[1]', 'body/tbl[1]/tr[1]/tc[1]/p[1]/r[2]/drawing[1]'],
                 'docx_caption_boundary': 'standalone following paragraph, no fabricated figure wrapper',
                 'pixel_sha256': {'red': image_identity(red), 'blue': image_identity(blue)},
                 'source_documents': {'epub': 'journey.xhtml', 'html': 'journey.html', 'url': 'response.html', 'docx': 'word/document.xml'},
                 'not_covered': 'Source-specific deep structures, cross-file continuation, return-to-parent prose, OCR'}
    save(root / 'independent-reference.json', reference)
    return reference


def database(data):
    with closing(sqlite3.connect(data / 'catalog.sqlite')) as connection:
        connection.row_factory = sqlite3.Row
        return {table: [dict(row) for row in connection.execute(f'SELECT * FROM {table} ORDER BY rowid')]
                for table in ('jobs', 'sources', 'revisions', 'exports', 'artifacts', 'section_versions', 'revision_migrations')}


@contextmanager
def observe(data: Path, *, forbid=False):
    """Wrap real Reader/OCR/HTTP/read boundaries; never supply a conversion result."""
    from src.application.capabilities import CAPABILITIES
    import src.adapters.ocr as ocr
    import importlib
    import httpx
    events = []
    def wrapper(label, function):
        def call(*args, **kwargs):
            if label == 'http' and type(args[0]._transport).__module__ == 'starlette.testclient':
                return function(*args, **kwargs)  # The user's inbound API request.
            events.append({'kind': label, 'argument': str(args[0]) if args else ''})
            if forbid and (label != 'file' or Path(args[0]).resolve().is_relative_to((data / 'sources').resolve())):
                raise AssertionError('Forbidden re-export read: '+label)
            return function(*args, **kwargs)
        return call
    with ExitStack() as stack:
        for capability in CAPABILITIES.values():
            if capability.reader:
                module, name = capability.reader.split(':')
                module = importlib.import_module(module)
                stack.enter_context(patch.object(module, name, wrapper('reader', getattr(module, name))))
        stack.enter_context(patch.object(httpx.Client, 'send', wrapper('http', httpx.Client.send)))
        # Async OCR boundary is separately observed, including accidental entry.
        stack.enter_context(patch.object(ocr, 'recognize', wrapper('ocr', ocr.recognize)))
        stack.enter_context(patch.object(Path, 'open', wrapper('file', Path.open)))
        yield events


def canonical_observation(export, reference, kind):
    book = json.loads((export / 'book.json').read_text(encoding='utf-8'))
    nodes = {n['node_id']: n for n in book['nodes']}
    def path(identifier):
        node = nodes[identifier]
        return (*path(node['parent_id']), node['title_original']) if node['parent_id'] else (node['title_original'],)
    observed = {'paths': [path(n['node_id']) for n in book['nodes']], 'body': [], 'images': [], 'locators': [], 'flow': [], 'image_locations': []}
    for fragment in book['content']:
        soup = BeautifulSoup(fragment['html'], 'html.parser')
        owner = nodes[fragment['node_id']]['title_original']
        observed['locators'].append(fragment['source'])
        for text in soup.find_all(string=True):
            if str(text).strip() in [value for _, value in TEXT]:
                observed['body'].append((owner, str(text).strip()))
        for image in soup.find_all('img'):
            observed['image_locations'].append(image.get('data-source-location'))
            asset = next(a for a in book['assets'] if image.get('src') == 'asset://'+a['asset_id'])
            container = image.find_parent(['li', 'td', 'figure'])
            observed['images'].append((owner, container.name if container else 'p', image_identity((export/'assets'/asset['file_name']).read_bytes())))
        for item in soup.descendants:
            if isinstance(item,NavigableString):
                text = str(item).strip()
                if text in [value for _,value in TEXT]:
                    observed['flow'].append(('text',owner,text))
                elif text == 'Red caption.':
                    observed['flow'].append(('caption',owner,text))
            elif item.name == 'img':
                asset = next(a for a in book['assets'] if item.get('src')=='asset://'+a['asset_id'])
                container = item.find_parent(['figure','li','td'])
                observed['flow'].append(('image',owner,container.name if container else 'p',image_identity((export/'assets'/asset['file_name']).read_bytes())))
                if container and container.name=='figure':
                    caption = container.find('figcaption')
                    assert caption and caption.get_text(strip=True)=='Red caption.', '图片与图题失配'
    failures = []
    if observed['paths'] != TITLES:
        failures.append({'structure': observed['paths'], 'expected': TITLES})
    if observed['body'] != TEXT:
        failures.append({'direct_body_ownership': observed['body'], 'expected': TEXT})
    if observed['flow'] != expected_flow(reference,kind):
        failures.append({'canonical_interleaved_body_images_caption':observed['flow'],'expected':expected_flow(reference,kind)})
    expected_images = [(r['owner'], 'p' if kind == 'docx' and r['container'] == 'figure' else r['container'], reference['pixel_sha256'][r['color']]) for r in reference['image_occurrences']]
    if observed['images'] != expected_images:
        failures.append({'image_ownership_container_order': observed['images'], 'expected': expected_images})
    if any(locator['format'] != kind or locator['path'] != reference['source_documents'][kind] for locator in observed['locators']):
        failures.append({'source_document_mismatch': observed['locators']})
    if observed['image_locations'] != expected_locations(reference, kind):
        failures.append({'canonical_image_source_occurrences': observed['image_locations'], 'expected': expected_locations(reference, kind)})
    return observed, failures


def view_observation(paths, root, reference, kind):
    result = {'headings': [], 'body': [], 'images': [], 'flow': [], 'image_locations': []}
    captions = 0
    for file in paths:
        soup = _soup(file)
        owner = None
        for element in soup.descendants:
            if isinstance(element,NavigableString):
                text = str(element).strip()
                if text in [value for _,value in TEXT]:
                    result['flow'].append(('text',owner,text))
                elif text=='Red caption.':
                    result['flow'].append(('caption',owner,text))
                continue
            if re.fullmatch(r'h[1-6]', element.name) and element.get_text(' ', strip=True) in {'Arrival', 'Details', 'Departure'}:
                title = element.get_text(' ', strip=True)
                result['headings'].append((int(element.name[1]), title))
                owner = title
            if element.name in {'p', 'li', 'td'}:
                text = element.get_text(' ', strip=True)
                if text in [value for _, value in TEXT] and not any(child.get_text(' ', strip=True) == text for child in element.find_all(['p', 'li', 'td'])):
                    result['body'].append((owner, text))
            if element.name == 'img':
                result['image_locations'].append(element.get('data-source-location'))
                target = (file.parent / element['src']).resolve()
                assert target.is_relative_to(root.resolve()) and target.is_file(), element
                container = element.find_parent(['figure', 'li', 'td'])
                result['images'].append((owner, container.name if container else 'p', image_identity(target.read_bytes())))
                result['flow'].append(('image',*result['images'][-1]))
        if soup.find('figcaption'):
            assert soup.find('figcaption').get_text(strip=True) == 'Red caption.'
        captions += soup.get_text().count('Red caption.')
    failures = []
    if result['image_locations'] != expected_locations(reference, kind):
        failures.append({'view_image_source_occurrences': result['image_locations'], 'expected': expected_locations(reference, kind)})
    if captions != 1:
        failures.append({'caption_occurrences':captions,'expected':1})
    if [title for _, title in result['headings']] != ['Arrival', 'Details', 'Departure']:
        failures.append({'heading_order': result['headings']})
    levels = [level for level, _ in result['headings']]
    if len(levels) == 3 and (levels[1] != levels[0]+1 or levels[2] != levels[0]):
        failures.append({'relative_heading_depth': levels})
    if result['body'] != TEXT:
        failures.append({'view_body_ownership': result['body']})
    if result['flow'] != expected_flow(reference,kind):
        failures.append({'view_interleaved_body_images_caption':result['flow'],'expected':expected_flow(reference,kind)})
    expected = [(r['owner'], 'p' if kind == 'docx' and r['container'] == 'figure' else r['container'], reference['pixel_sha256'][r['color']]) for r in reference['image_occurrences']]
    if result['images'] != expected:
        failures.append({'view_image_relationships': result['images'], 'expected': expected})
    return result, failures
