"""DOCX cutover gates use independently authored OOXML source expectations."""

import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
import markdown
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile

from bs4 import BeautifulSoup
from fastapi.testclient import TestClient

import any_to_md
from any_to_md.application.convert import ConversionRequest, convert
from any_to_md.application.jobs import ConversionFailure, JobRuntime
from tests.quality.docx_fixtures import structured_docx
from tests.quality.fixtures import _archive, _docx, _image, build_fixtures, load_cases
from tests.quality.inspection import inspect_output, inspect_pdf
from web.app import app
from web.config import settings


def rewrite_docx(path, transform):
    with ZipFile(path) as archive:
        members = {name: archive.read(name) for name in archive.namelist()}
    transform(members)
    _archive(path, members)


class DocxConversionTests(unittest.TestCase):
    def test_adjacent_numbering_formats_and_instances_do_not_change_earlier_items(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root/'mixed-numbering.docx'
            case = structured_docx(source)
            def change(members):
                members['word/numbering.xml'] = members['word/numbering.xml'].replace(b'</w:numbering>', b'<w:abstractNum w:abstractNumId="2"><w:lvl w:ilvl="0"><w:start w:val="3"/><w:numFmt w:val="upperRoman"/><w:lvlText w:val="%1."/></w:lvl></w:abstractNum><w:num w:numId="2"><w:abstractNumId w:val="2"/></w:num></w:numbering>')
                original = b'<w:numId w:val="1"/></w:numPr></w:pPr><w:r><w:t>DOCX_LIST_RETURN_505'
                members['word/document.xml'] = members['word/document.xml'].replace(original, original.replace(b'val="1"', b'val="2"'))
                restored = '<w:p><w:pPr><w:numPr><w:ilvl w:val="0"/><w:numId w:val="1"/></w:numPr></w:pPr><w:r><w:t>DOCX_LIST_RESTORED_934</w:t></w:r></w:p>'
                members['word/document.xml'] = members['word/document.xml'].replace(b'DOCX_LIST_RETURN_505</w:t></w:r></w:p>', b'DOCX_LIST_RETURN_505</w:t></w:r></w:p>'+restored.encode())
            rewrite_docx(source, change)
            targets = ('markdown', 'html', 'pdf') if os.environ.get('ANY_TO_MD_TEST_PDF') == '1' else ('markdown', 'html')
            result = convert(ConversionRequest(source, root/'data', targets))
            self.assertEqual(result.status, 'completed')
            case['markers'].insert(case['markers'].index('DOCX_LIST_RETURN_505')+1, 'DOCX_LIST_RESTORED_934')
            self.assert_views(result.output_dir, case)
            for paths in ([result.output_dir/'book.html'], [result.output_dir/'book.md'], sorted((result.output_dir/'chapters').glob('*.md'))):
                soup = BeautifulSoup('\n'.join(path.read_text() if path.suffix == '.html' else markdown.markdown(path.read_text(), extensions=['tables', 'fenced_code']) for path in paths), 'html.parser')
                for marker, value, kind in [('DOCX_LIST_TOP_503', '1', '1'), ('DOCX_LIST_RETURN_505', '3', 'I'), ('DOCX_LIST_RESTORED_934', '2', '1')]:
                    item = next(item for item in soup.find_all('li') if item.find(string=lambda text: marker in text))
                    self.assertEqual(item['value'], value)
                    self.assertEqual(item.parent.get('type', '1'), kind)
            if 'pdf' in targets:
                import pymupdf
                with pymupdf.open(result.output_dir/'book.pdf') as document:
                    words = [word for page in document for word in page.get_text('words')]
                for marker, label in [('DOCX_LIST_TOP_503', '1.'), ('DOCX_LIST_RETURN_505', 'III.'), ('DOCX_LIST_RESTORED_934', '2.')]:
                    location = next(word for word in words if word[4] == marker)
                    labels = [word[4] for word in words if word[2] <= location[0] and abs(word[1]-location[1]) < 2]
                    self.assertIn(label, labels)

    def test_strict_ooxml_and_custom_part_names_keep_structure_notes_and_images(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for relocated in (False, True, 'root'):
                with self.subTest(relocated=relocated):
                    source = root/f'strict-{relocated}.docx'
                    case = structured_docx(source)
                    def change(members):
                        for name, content in list(members.items()):
                            if name.endswith(('.xml', '.rels')):
                                for before, after in [
                                    ('http://schemas.openxmlformats.org/wordprocessingml/2006/main', 'http://purl.oclc.org/ooxml/wordprocessingml/main'),
                                    ('http://schemas.openxmlformats.org/officeDocument/2006/relationships', 'http://purl.oclc.org/ooxml/officeDocument/relationships'),
                                    ('http://schemas.openxmlformats.org/drawingml/2006/', 'http://purl.oclc.org/ooxml/drawingml/'),
                                ]:
                                    content = content.replace(before.encode(), after.encode())
                                if name == '_rels/.rels':
                                    content = content.replace(b'http://purl.oclc.org/ooxml/officeDocument/relationships/officeDocument', b'http://purl.oclc.org/ooxml/relationships/officeDocument')
                                members[name] = content
                        if relocated:
                            prefix = '' if relocated == 'root' else 'custom/'
                            for part in ('document', 'styles', 'numbering', 'footnotes', 'endnotes', 'comments'):
                                members[f'{prefix}{part}.xml'] = members.pop(f'word/{part}.xml')
                            members[f'{prefix}_rels/document.xml.rels'] = members.pop('word/_rels/document.xml.rels')
                            for color in ('red', 'blue'):
                                members[f'{prefix}media/{color}.png'] = members.pop(f'word/media/{color}.png')
                            members['_rels/.rels'] = members['_rels/.rels'].replace(b'word/document.xml', (prefix+'document.xml').encode())
                            members['[Content_Types].xml'] = members['[Content_Types].xml'].replace(b'/word/', ('/'+prefix).encode())
                    rewrite_docx(source, change)
                    result = convert(ConversionRequest(source, root/'data', ('markdown', 'html')))
                    self.assertEqual(result.status, 'completed', result.report_path.read_text())
                    self.assert_views(result.output_dir, case)

    def test_symbol_source_evidence_matches_visible_unicode_and_detects_loss(self):
        import mammoth

        original = mammoth.convert_to_html
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root/'symbols.docx'
            _docx(source, _image('red'))
            def change(members):
                members['word/document.xml'] = members['word/document.xml'].replace(b'<w:sectPr/>', b'<w:p><w:r><w:t>SYMBOL_SOURCE_935</w:t><w:sym w:font="Symbol" w:char="F061"/><w:sym w:font="Symbol" w:char="0057"/><w:sym w:font="Wingdings" w:char="F0FC"/></w:r></w:p><w:sectPr/>')
            rewrite_docx(source, change)
            result = convert(ConversionRequest(source, root/'data', ('markdown', 'html')))
            self.assertEqual(result.status, 'completed', result.report_path.read_text())
            for paths in ([result.output_dir/'book.html'], [result.output_dir/'book.md'], sorted((result.output_dir/'chapters').glob('*.md'))):
                rendered = '\n'.join(path.read_text() if path.suffix == '.html' else markdown.markdown(path.read_text(), extensions=['tables', 'fenced_code']) for path in paths)
                self.assertIn('SYMBOL_SOURCE_935αΩ✓', BeautifulSoup(rendered, 'html.parser').get_text())
            def missing_symbol(*args, **kwargs):
                result = original(*args, **kwargs)
                result.value = result.value.replace('α', '')
                result.messages = []
                return result
            with patch('any_to_md.readers.docx.reader.mammoth.convert_to_html', missing_symbol):
                broken = convert(ConversionRequest(source, root/'data', ('html',)))
            self.assertEqual(broken.status, 'partial')
            self.assertTrue(any(item['code'] == 'docx_source_text' for item in json.loads(broken.report_path.read_text())['diagnostics']))

    def test_image_and_field_only_headers_or_footers_cannot_silently_complete(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for kind, content in [('header', 'image'), ('footer', 'field'), ('header', 'empty')]:
                with self.subTest(kind=kind, content=content):
                    source = root/f'{kind}-{content}.docx'
                    _docx(source, _image('red'))
                    def change(members):
                        document = members['word/document.xml'].decode()
                        opening = document.split('<w:body>', 1)[0].replace('w:document', 'w:hdr' if kind == 'header' else 'w:ftr')
                        drawing = '<w:p><w:r><w:drawing>'+document.split('<w:drawing>', 1)[1].split('</w:drawing>', 1)[0]+'</w:drawing></w:r></w:p>'
                        body = drawing if content == 'image' else ('<w:p><w:fldSimple w:instr="PAGE"/></w:p>' if content == 'field' else '<w:p/>')
                        members[f'word/{kind}1.xml'] = (opening+body+('</w:hdr>' if kind == 'header' else '</w:ftr>')).encode()
                        members[f'word/_rels/{kind}1.xml.rels'] = members['word/_rels/document.xml.rels']
                        relation = f'<Relationship Id="{kind}1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/{kind}" Target="{kind}1.xml"/>'
                        members['word/_rels/document.xml.rels'] = members['word/_rels/document.xml.rels'].replace(b'</Relationships>', relation.encode()+b'</Relationships>')
                        members['word/document.xml'] = members['word/document.xml'].replace(b'<w:sectPr/>', f'<w:sectPr><w:{kind}Reference w:type="default" r:id="{kind}1"/></w:sectPr>'.encode())
                    rewrite_docx(source, change)
                    result = convert(ConversionRequest(source, root/'data', ('html',)))
                    self.assertEqual(result.status, 'completed' if content == 'empty' else 'partial')
                    issues = json.loads(result.report_path.read_text())['diagnostics']
                    if content != 'empty':
                        self.assertTrue(any(item['code'] == 'docx_unsupported_part' and item['severity'] == 'error' for item in issues))

    def test_word_choice_without_fallback_is_selected_and_unknown_choice_is_reported(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for requires in ('w', 'future'):
                source = root/f'choice-{requires}.docx'
                case = structured_docx(source)
                def change(members):
                    choice = f'<mc:AlternateContent xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006" xmlns:future="urn:future-word"><mc:Choice Requires="{requires}"><w:p><w:r><w:t>ALTERNATE_SOURCE_BODY_932</w:t></w:r></w:p></mc:Choice></mc:AlternateContent>'
                    members['word/document.xml'] = members['word/document.xml'].replace(b'<w:sectPr/>', choice.encode()+b'<w:sectPr/>')
                rewrite_docx(source, change)
                result = convert(ConversionRequest(source, root/'data', ('markdown', 'html')))
                if requires == 'w':
                    self.assertEqual(result.status, 'completed')
                    case['markers'].insert(case['markers'].index('DOCX_FOOTNOTE_518'), 'ALTERNATE_SOURCE_BODY_932')
                    self.assert_views(result.output_dir, case)
                else:
                    self.assertEqual(result.status, 'partial')
                    report = json.loads(result.report_path.read_text())
                    self.assertTrue(any(item['code'] == 'docx_alternate_content' and item['severity'] == 'error' for item in report['diagnostics']))

    def test_numbering_start_and_instance_override_keep_visible_values_in_all_views(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for restart in (False, True):
                source = root/f'numbered-{restart}.docx'
                structured_docx(source)
                def change(members):
                    numbers = members['word/numbering.xml'].replace(b'<w:start w:val="1"/>', b'<w:start w:val="5"/>')
                    if restart:
                        numbers = numbers.replace(b'</w:numbering>', b'<w:num w:numId="2"><w:abstractNumId w:val="0"/><w:lvlOverride w:ilvl="0"><w:startOverride w:val="9"/></w:lvlOverride></w:num></w:numbering>')
                        members['word/document.xml'] = members['word/document.xml'].replace(b'<w:numId w:val="1"/></w:numPr></w:pPr><w:r><w:t>DOCX_LIST_RETURN_505', b'<w:numId w:val="2"/></w:numPr></w:pPr><w:r><w:t>DOCX_LIST_RETURN_505')
                    members['word/numbering.xml'] = numbers
                rewrite_docx(source, change)
                result = convert(ConversionRequest(source, root/'data', ('markdown', 'html')))
                self.assertEqual(result.status, 'completed')
                for paths in ([result.output_dir/'book.html'], [result.output_dir/'book.md'], sorted((result.output_dir/'chapters').glob('*.md'))):
                    rendered = '\n'.join(path.read_text() if path.suffix == '.html' else markdown.markdown(path.read_text(), extensions=['tables', 'fenced_code']) for path in paths)
                    soup = BeautifulSoup(rendered, 'html.parser')
                    first = soup.find('li', string=lambda value: value and 'DOCX_LIST_TOP_503' in value)
                    if first is None:
                        first = next(item for item in soup.find_all('li') if 'DOCX_LIST_TOP_503' in item.get_text())
                    last = soup.find('li', string='DOCX_LIST_RETURN_505')
                    self.assertEqual(first['value'], '5')
                    self.assertEqual(first.parent['start'], '5')
                    self.assertEqual(last['value'], '9' if restart else '6')

    def test_repeated_notes_have_one_body_and_distinct_return_targets(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root/'repeated-notes.docx'
            case = structured_docx(source)
            def change(members):
                members['word/document.xml'] = members['word/document.xml'].replace(b'<w:footnoteReference w:id="1"/><w:endnoteReference w:id="2"/>', b'<w:endnoteReference w:id="2"/><w:footnoteReference w:id="1"/><w:footnoteReference w:id="1"/>').replace(b'<w:commentReference w:id="3"/>', b'<w:commentReference w:id="3"/><w:commentReference w:id="3"/>')
            rewrite_docx(source, change)
            result = convert(ConversionRequest(source, root/'data', ('markdown', 'html')))
            self.assertEqual(result.status, 'completed')
            case['markers'][-3:] = ['DOCX_ENDNOTE_519', 'DOCX_FOOTNOTE_518', 'DOCX_COMMENT_520']
            case['required_links'] = [{'label': 'DOCX_TABLE_LINK_509', 'target_marker': 'DOCX_SECOND_513'}, {'label': '[1]', 'target_marker': 'DOCX_ENDNOTE_519'}, {'label': '[2]', 'target_marker': 'DOCX_FOOTNOTE_518'}]
            self.assert_views(result.output_dir, case)
            html = BeautifulSoup((result.output_dir/'book.html').read_text(), 'html.parser')
            for label in ('[2]', '[Q1]'):
                refs = html.find_all('a', string=label)
                self.assertEqual(len(refs), 2)
                self.assertEqual(len({ref['id'] for ref in refs}), 2)
                for ref in refs:
                    self.assertIsNotNone(html.find('a', href='#'+ref['id']))

    def test_source_audit_rejects_silent_omission_and_duplication(self):
        import mammoth

        original = mammoth.convert_to_html
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root/'source.docx'
            structured_docx(source)
            for duplicate in (False, True):
                def corrupted(*args, **kwargs):
                    result = original(*args, **kwargs)
                    paragraph = '<p>DOCX_FIRST_502</p>'
                    result.value = result.value.replace(paragraph, paragraph*2 if duplicate else '')
                    result.messages = []
                    return result
                with patch('any_to_md.readers.docx.reader.mammoth.convert_to_html', corrupted):
                    result = convert(ConversionRequest(source, root/'data', ('html',)))
                self.assertEqual(result.status, 'partial')
                report = json.loads(result.report_path.read_text())
                self.assertTrue(any(item['code'] == 'docx_source_text' and item['severity'] == 'error' for item in report['diagnostics']))

    def test_custom_main_document_part_preserves_source_locator_and_image(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for prefix in ('custom/', ''):
                with self.subTest(prefix=prefix):
                    source = root/'relocated-part.docx'
                    _docx(source, _image('red'))
                    def change(members):
                        members[prefix+'body.xml'] = members.pop('word/document.xml')
                        members[prefix+'_rels/body.xml.rels'] = members.pop('word/_rels/document.xml.rels')
                        members[prefix+'media/red.png'] = members.pop('word/media/red.png')
                        members['_rels/.rels'] = members['_rels/.rels'].replace(b'word/document.xml', (prefix+'body.xml').encode())
                        members['[Content_Types].xml'] = members['[Content_Types].xml'].replace(b'/word/document.xml', ('/'+prefix+'body.xml').encode())
                    rewrite_docx(source, change)
                    result = convert(ConversionRequest(source, root/'data', ('markdown', 'html')))
                    self.assertEqual(result.status, 'completed')
                    self.assert_views(result.output_dir, next(case for case in load_cases() if case['id'] == 'docx-rich'))
                    book = json.loads((result.output_dir/'book.json').read_text())
                    self.assertTrue(all(node['source']['path'] == prefix+'body.xml' for node in book['nodes']))

    def test_header_text_is_explicitly_reported_as_unpreserved(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root/'header.docx'
            _docx(source, _image('red'))
            def change(members):
                members['word/header1.xml'] = b'<w:hdr xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:p><w:r><w:t>HEADER_SOURCE_933</w:t></w:r></w:p></w:hdr>'
                members['word/_rels/document.xml.rels'] = members['word/_rels/document.xml.rels'].replace(b'</Relationships>', b'<Relationship Id="header1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/header" Target="header1.xml"/></Relationships>')
                members['word/document.xml'] = members['word/document.xml'].replace(b'<w:sectPr/>', b'<w:sectPr><w:headerReference w:type="default" r:id="header1"/></w:sectPr>')
            rewrite_docx(source, change)
            result = convert(ConversionRequest(source, root/'data', ('html',)))
            self.assertEqual(result.status, 'partial')
            report = json.loads(result.report_path.read_text())
            self.assertTrue(any(item['code'] == 'docx_unsupported_part' and item['severity'] == 'error' for item in report['diagnostics']))

    def test_docx_code_soft_breaks_keep_blank_lines_and_tabs_in_markdown(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root/'soft-code.docx'
            structured_docx(source)
            def change(members):
                members['word/document.xml'] = members['word/document.xml'].replace(b'# DOCX_CODE_511</w:t>', b'# DOCX_CODE_511</w:t><w:br/><w:br/><w:t xml:space="preserve">  SOFT_BREAK_LINE_931</w:t><w:tab/><w:t>indent</w:t>')
            rewrite_docx(source, change)
            result = convert(ConversionRequest(source, root/'data', ('markdown', 'html')))
            expected = '# DOCX_CODE_511\n\n  SOFT_BREAK_LINE_931\tindent\n  value = "a_b * c"\n  print(value)\n'
            for paths in ([result.output_dir/'book.md'], sorted((result.output_dir/'chapters').glob('*.md'))):
                self.assertIn('```\n'+expected+'```', '\n'.join(path.read_text() for path in paths))
                rendered = '\n'.join(markdown.markdown(path.read_text(), extensions=['tables', 'fenced_code']) for path in paths)
                soup = BeautifulSoup(rendered, 'html.parser')
                self.assertEqual(soup.pre.get_text(), expected.expandtabs(4))
            html = BeautifulSoup((result.output_dir/'book.html').read_text(), 'html.parser')
            self.assertEqual(len(html.pre.find_all('br')), 2)

    def assert_views(self, output, case):
        for view, paths in {'html': [output/'book.html'], 'md_single': [output/'book.md'], 'md_chapters': sorted((output/'chapters').glob('*.md'))}.items():
            check = inspect_output(paths, case, output)
            self.assertTrue(check['passed'], (view, check['failures']))

    def test_existing_docx_source_preserves_merged_table_and_image_in_shared_views(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = build_fixtures(root/'inputs')['docx-rich']
            case = next(case for case in load_cases() if case['id'] == 'docx-rich')
            result = convert(ConversionRequest(source, root/'data', ('markdown', 'html')))
            self.assertEqual(result.status, 'completed')
            self.assert_views(result.output_dir, case)

    def test_structured_docx_keeps_lists_spans_code_notes_and_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root/'structured.docx'
            case = structured_docx(source)
            result = convert(ConversionRequest(source, root/'data', ('markdown', 'html')))
            self.assertEqual(result.status, 'completed')
            self.assert_views(result.output_dir, case)
            book = json.loads((result.output_dir/'book.json').read_text())
            self.assertEqual(book['source_format'], 'docx')
            self.assertEqual(book['metadata']['title'], '结构与资源质量样本')
            self.assertEqual(book['metadata']['authors'], ['质量作者'])
            self.assertEqual(book['metadata']['language'], 'zh-CN')
            self.assertEqual(len(book['roots']), 2)
            self.assertTrue(all(node['source']['format'] == 'docx' and node['source']['path'] == 'word/document.xml' for node in book['nodes']))
            for paths in ([result.output_dir/'book.html'], [result.output_dir/'book.md'], sorted((result.output_dir/'chapters').glob('*.md'))):
                rendered = '\n'.join(path.read_text() if path.suffix == '.html' else markdown.markdown(path.read_text(), extensions=['tables', 'fenced_code']) for path in paths)
                view = BeautifulSoup(rendered, 'html.parser')
                prefix = view.find(string=lambda text: 'DOCX_INTRO_501' in text)
                heading = view.find(['h1', 'h2'], string='第一章：内容')
                positions = {id(element): index for index, element in enumerate(view.descendants)}
                self.assertLess(positions[id(prefix)], positions[id(heading)], 'Source introduction must precede its following chapter heading')
                top = view.find('li', string=lambda value: value and 'DOCX_LIST_RETURN_505' in value)
                self.assertEqual(top.parent.name, 'ol')
                nested = view.find('li', string=lambda value: value and 'DOCX_LIST_NESTED_504' in value)
                self.assertEqual(nested.parent.name, 'ul')
            html = BeautifulSoup((result.output_dir/'book.html').read_text(), 'html.parser')
            self.assertEqual(html.pre.get_text(), '# DOCX_CODE_511\n  value = "a_b * c"\n  print(value)')
            for label, marker in (('[1]', 'DOCX_FOOTNOTE_518'), ('[2]', 'DOCX_ENDNOTE_519'), ('[Q1]', 'DOCX_COMMENT_520')):
                link = html.find('a', string=label)
                self.assertIsNotNone(link, label)
                target = html.find(id=link['href'][1:])
                if target.name == 'dt':
                    target = target.find_next_sibling('dd')
                self.assertIn(marker, target.get_text())
            self.assertEqual(len(html.find_all('h2', string='第一章：内容')), 1)

    @unittest.skipUnless(os.environ.get('ANY_TO_MD_TEST_PDF') == '1', '需要显式启用原生 PDF 渲染环境')
    def test_docx_pdf_uses_canonical_body_order_and_both_source_images(self):
        import pymupdf

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root/'structured.docx'
            case = structured_docx(source)
            result = convert(ConversionRequest(source, root/'data', ('markdown', 'html', 'pdf')))
            check = inspect_pdf(result.output_dir/'book.pdf', case)
            self.assertTrue(check['passed'], check['failures'])
            with pymupdf.open(result.output_dir/'book.pdf') as document:
                text = ''.join(''.join(page.get_text() for page in document).split())
            self.assertLess(text.index('DOCX_INTRO_501'), text.index('第一章：内容'))
            self.assertLess(text.index('第一章：内容'), text.index('DOCX_FIRST_502'))

    def test_docx_source_replays_after_removal_and_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root/'structured.docx'
            case = structured_docx(source)
            first = convert(ConversionRequest(source, root/'data', ('html',)))
            source.unlink()
            runtime = JobRuntime(root/'data')
            queued = runtime.queue(first.job_id, 'md_single')
            again = convert(ConversionRequest(data_dir=root/'data', job_id=queued.job_id))
            self.assertEqual(first.revision_id, again.revision_id)
            check = inspect_output([again.output_dir/'book.md'], case, again.output_dir)
            self.assertTrue(check['passed'], check['failures'])

    def test_all_docx_web_views_and_cli_share_revision_and_portable_artifacts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root/'structured.docx'
            case = structured_docx(source)
            revisions = set()
            with patch.object(settings, 'DATA_DIR', str(root/'web-data')), TestClient(app) as client:
                views = ('md_single', 'md_chapters', 'html', *(('pdf',) if os.environ.get('ANY_TO_MD_TEST_PDF') == '1' else ()))
                for view in views:
                    uploaded = client.post('/api/upload', files={'file': (source.name, source.read_bytes())})
                    self.assertEqual(uploaded.status_code, 200, uploaded.text)
                    self.assertIn(view, uploaded.json()['available_outputs'])
                    task = uploaded.json()['task_id']
                    self.assertEqual(client.post('/api/convert', json={'task_id': task, 'output_format': view}).status_code, 200)
                    state = client.get(f'/api/status/{task}').json()
                    self.assertEqual(state['status'], 'completed', state)
                    download = client.get(state['download_url'])
                    self.assertEqual(download.status_code, 200)
                    target = root/'relocated'/view
                    if view == 'pdf':
                        target.mkdir(parents=True)
                        (target/'book.pdf').write_bytes(download.content)
                        check = inspect_pdf(target/'book.pdf', case)
                    else:
                        with ZipFile(io.BytesIO(download.content)) as archive:
                            archive.extractall(target)
                        paths = sorted((target/'chapters').glob('*.md')) if view == 'md_chapters' else [target/('book.html' if view == 'html' else 'book.md')]
                        check = inspect_output(paths, case, target)
                    self.assertTrue(check['passed'], check['failures'])
                    job = JobRuntime(root/'web-data').get(task)
                    revisions.add(json.loads((root/'web-data'/job.storage_key/'book.json').read_text())['revision_id'])
            outputs = self.cli(root, ['--input', str(source)], root/'cli-data')
            self.assertEqual(len(outputs), 1)
            self.assert_views(outputs[0], case)
            snapshot = json.loads((outputs[0]/'book.json').read_text())
            self.assertEqual({snapshot['revision_id']}, revisions)

    def cli(self, cwd, arguments, data_dir):
        installed = Path(any_to_md.__file__).is_relative_to(Path(sys.prefix))
        command = [sys.executable, *(['-I'] if installed else []), '-m', 'any_to_md.interfaces.cli.convert_book']
        environment = dict(os.environ)
        if not installed:
            environment['PYTHONPATH'] = str(Path(__file__).resolve().parents[2]/'src')
        result = subprocess.run([*command, *arguments, '--data-dir', str(data_dir), '--target', 'markdown', 'html'], cwd=cwd, env=environment, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
        return list((data_dir/'exports').iterdir())

    def test_docx_multiple_and_directory_cli_reuse_same_revision(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            batch = root/'batch'
            source = batch/'structured.docx'
            case = structured_docx(source)
            basic = batch/'basic.docx'
            _docx(basic, _image('red'))
            for arguments in (['--input', str(source), str(basic)], ['--input-dir', str(batch)]):
                outputs = self.cli(root, arguments, root/'data')
            self.assertEqual(len(outputs), 4)
            snapshots = [(path, json.loads((path/'book.json').read_text())) for path in outputs]
            selected = [(path, record) for path, record in snapshots if record['metadata']['title'] == '结构与资源质量样本']
            self.assertEqual(len(selected), 2)
            self.assertEqual(len({record['revision_id'] for _, record in selected}), 1)
            for path, _ in selected:
                self.assert_views(path, case)

    def test_unsafe_or_broken_docx_archive_fails_persistently(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for kind in ('invalid', 'traversal', 'duplicate'):
                source = root/'broken.docx'
                if kind == 'invalid':
                    source.write_bytes(b'not a DOCX')
                else:
                    _archive(source, {'word/document.xml': '<document/>', '../outside.xml' if kind == 'traversal' else './word/document.xml': b'invalid member'})
                with self.assertRaises(ConversionFailure) as caught:
                    convert(ConversionRequest(source, root/'data'))
                runtime = JobRuntime(root/'data')
                self.assertEqual(runtime.get(caught.exception.job_id).status.value, 'failed')
                with self.assertRaises(ValueError):
                    runtime.download(caught.exception.job_id)
                self.assertFalse((root/'outside.xml').exists())

    def test_external_docx_image_is_not_read_and_reports_partial(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            outside = root/'outside.png'
            outside.write_bytes(_image('blue'))
            source = root/'linked.docx'
            _docx(source, _image('red'))
            def change(members):
                members['word/document.xml'] = members['word/document.xml'].replace(b'r:embed="img1"', b'r:link="img1"')
                members['word/_rels/document.xml.rels'] = members['word/_rels/document.xml.rels'].replace(b'Target="media/red.png"', f'Target="{outside.as_uri()}" TargetMode="External"'.encode())
            rewrite_docx(source, change)
            result = convert(ConversionRequest(source, root/'data', ('html',)))
            self.assertEqual(result.status, 'partial')
            report = json.loads(result.report_path.read_text())
            self.assertTrue(any(item['severity'] == 'error' and 'external' in item['message'].lower() for item in report['diagnostics']))
            self.assertFalse(list((result.output_dir/'assets').iterdir()))
            self.assertIn('DOCX_FIRST_401', (result.output_dir/'book.html').read_text())

    def test_embedded_style_map_cannot_silently_remove_source_paragraphs(self):
        import mammoth

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root/'embedded.docx'
            _docx(source, _image('red'))
            with source.open('r+b') as stream:
                mammoth.embed_style_map(stream, 'p => !')
            result = convert(ConversionRequest(source, root/'data', ('html',)))
            self.assertIn('DOCX_FIRST_401', BeautifulSoup((result.output_dir/'book.html').read_text(), 'html.parser').get_text())

    def test_unknown_source_element_reports_content_loss_and_doc_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root/'unknown.docx'
            _docx(source, _image('red'))
            def change(members):
                members['word/document.xml'] = members['word/document.xml'].replace(b'<w:sectPr/>', b'<w:unsupported><w:r><w:t>UNSUPPORTED_BODY_524</w:t></w:r></w:unsupported><w:sectPr/>')
            rewrite_docx(source, change)
            result = convert(ConversionRequest(source, root/'data', ('html',)))
            self.assertEqual(result.status, 'partial')
            report = json.loads(result.report_path.read_text())
            self.assertTrue(any(item['severity'] == 'error' and 'unsupported' in item['message'] for item in report['diagnostics']))
            legacy = root/'legacy.doc'
            legacy.write_bytes(b'legacy Word')
            with self.assertRaises(ConversionFailure):
                convert(ConversionRequest(legacy, root/'data'))


if __name__ == '__main__':
    unittest.main()
