"""Whole-source gates must reject omissions, repetition and unsafe allowances."""

import tempfile
import json
import re
import unittest
from pathlib import Path
from zipfile import ZipFile

from tests.quality.fixtures import _archive, build_fixtures
from tests.quality.full_book import SourceUnit, _view, align, inspect_book, read_source


class WholeSourceGateTests(unittest.TestCase):
    def test_nested_font_rules_cannot_authorize_number_removal(self):
        from any_to_md.application.convert import ConversionRequest, convert

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = build_fixtures(root/'inputs')['epub-no-toc']
            with ZipFile(source) as archive:
                members = {name: archive.read(name) for name in archive.namelist()}
            members['OEBPS/one.xhtml'] = '''<html><head><title>嵌套字体</title><style>.mono {font-family:monospace; & span {font-family:serif}}</style></head><body>
<p class="mono">1 <span>Ordinary numbered prose A</span></p>
<p class="mono">2 <span>Ordinary numbered prose B</span></p></body></html>'''
            _archive(source, members)
            result = convert(ConversionRequest(source, root/'data', ('markdown', 'html')))
            html = (result.output_dir/'book.html').read_text()
            self.assertNotIn('<pre', html)
            self.assertFalse(any(unit.allow_number_cleanup for unit in read_source(source).units))
            self.assertTrue(inspect_book(source, result.output_dir)['passed'])

    def test_separate_code_spans_with_source_comments_allow_number_cleanup(self):
        with tempfile.TemporaryDirectory() as directory:
            source = build_fixtures(Path(directory))['epub-no-toc']
            with ZipFile(source) as archive:
                members = {name: archive.read(name) for name in archive.namelist()}
            members['OEBPS/one.xhtml'] = '''<html><head><style>.mono{font-family:Source Code Pro}</style></head><body>
<p><span class="mono">1 movw $0x0c,%bx #</span><span style="font-family:PingFang SC">文字为红色</span></p>
<p><span class="mono">2 movw $12,%cx #</span><span style="font-family:PingFang SC">字符串长度</span></p></body></html>'''
            _archive(source, members)
            book = read_source(source)
            code = [unit for unit in book.units if unit.allow_number_cleanup]
            self.assertEqual(len(code), 2)
            self.assertTrue(align(book.units, ''.join(unit.payload if unit.allow_number_cleanup else unit.text for unit in book.units), [])['passed'])

    def test_full_audit_fails_on_body_or_navigation_omission(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = build_fixtures(root/'inputs')['epub-plain']
            export = root/'export'
            (export/'chapters').mkdir(parents=True)
            snapshot = {'content': [{'node_id': 'node', 'source': {'path': 'plain.xhtml'}}], 'nodes': [{'node_id': 'node', 'parent_id': None, 'title_original': '普通章节', 'source': {'path': 'plain.xhtml', 'fragment': 'start'}}]}
            snapshot.update(book_id='00000000-0000-4000-8000-000000000001', edition_id='00000000-0000-4000-8000-000000000002', revision_id='00000000-0000-4000-8000-000000000003')
            (export/'book.json').write_text(json.dumps(snapshot))
            html = '<html><body><h1>质量样本</h1><h2 id="sec-node">普通章节</h2><p>PLAIN_BODY_001</p></body></html>'
            (export/'book.html').write_text(html)
            body = '<a id="sec-node"></a>\n\n## 普通章节\n\nPLAIN_BODY_001\n'
            metadata = '---\ntitle: "质量样本"\nlanguage: "zh"\n'+''.join(f'{key}: {snapshot[key]}\n' for key in ('book_id', 'edition_id', 'revision_id'))+'---\n\n'
            prefix = '# 质量样本\n\n## 目录\n\n- 普通章节 — [定位](#sec-node)\n\n'
            md = metadata+prefix+body
            split_prefix = '[总目录](../TOC.md) · [整体版对应位置](../book.md#sec-node)\n\n'
            (export/'book.md').write_text(md)
            (export/'TOC.md').write_text('# 质量样本\n')
            (export/'chapters'/'one.md').write_text(split_prefix+body)
            self.assertTrue(inspect_book(source, export)['passed'])
            for damaged in (body, metadata+body, prefix+body, md.replace(prefix, ''), md.replace('title: "质量样本"', 'title: "质量样本"\nextra: "PLAIN_BODY_001"')):
                with self.subTest(damaged_prefix=damaged[:80]):
                    (export/'book.md').write_text(damaged)
                    self.assertFalse(inspect_book(source, export)['views']['md_single']['passed'])
            (export/'book.md').write_text(md)
            (export/'chapters'/'one.md').write_text(body)
            self.assertFalse(inspect_book(source, export)['views']['md_chapters']['passed'])
            (export/'chapters'/'one.md').write_text(split_prefix+body)
            (export/'book.md').write_text('PLAIN_BODY_001 DUPLICATED_ADDED_PROSE\n\n'+md)
            self.assertFalse(inspect_book(source, export)['views']['md_single']['passed'])
            (export/'book.md').write_text(md)
            (export/'book.html').write_text(html.replace('</body>', '<h2 id="sec-fabricated">普通章节</h2></body>'))
            self.assertFalse(inspect_book(source, export)['views']['html']['passed'])
            (export/'book.html').write_text(html.replace('PLAIN_BODY_001', ''))
            self.assertFalse(inspect_book(source, export)['views']['html']['passed'])
            (export/'book.html').write_text(html)
            snapshot['nodes'] = []
            (export/'book.json').write_text(json.dumps(snapshot))
            report = inspect_book(source, export)
            self.assertFalse(report['passed'])
            self.assertEqual(report['structure']['navigation_errors'], [0])

    def test_generated_nested_toc_and_split_links_are_audited_against_actual_files(self):
        from any_to_md.application.convert import ConversionRequest, convert

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixtures = build_fixtures(root/'inputs')
            for name in ('epub-structured', 'epub-no-toc'):
                with self.subTest(name=name):
                    result = convert(ConversionRequest(fixtures[name], root/'data', ('markdown', 'html')))
                    report = inspect_book(fixtures[name], result.output_dir)
                    self.assertTrue(report['passed'], report)
                    chapters = sorted((result.output_dir/'chapters').glob('*.md'))
                    path = chapters[0]
                    original = path.read_text()
                    # Leave the valid target identity/label unchanged and corrupt
                    # only the file, which the earlier gate failed to notice.
                    damaged = re.sub(r'(?<=\]\()[^()\n]+\.md(?=#sec-)', 'absent-chapter.md', original.rsplit('\n---\n', 1)[-1])
                    path.write_text(original.rsplit('\n---\n', 1)[0]+'\n---\n'+damaged)
                    self.assertFalse(inspect_book(fixtures[name], result.output_dir)['views']['md_chapters']['passed'])
                    path.write_text(original.rsplit('\n---\n', 1)[0])
                    self.assertFalse(inspect_book(fixtures[name], result.output_dir)['views']['md_chapters']['passed'])
                    path.write_text(original)
                    (result.output_dir/'TOC.md').unlink()
                    self.assertFalse(inspect_book(fixtures[name], result.output_dir)['views']['md_chapters']['passed'])

    def test_conflicting_font_declarations_never_authorize_number_removal(self):
        with tempfile.TemporaryDirectory() as directory:
            source = build_fixtures(Path(directory))['epub-no-toc']
            with ZipFile(source) as archive:
                members = {name: archive.read(name) for name in archive.namelist()}
            members['OEBPS/one.xhtml'] = '''<html><head><title>Prose</title><style>
#one,#two {font-family:serif!important} .mono {font-family:monospace}
</style></head><body><p id="one" class="mono">1 普通编号一</p><p id="two" class="mono">2 普通编号二</p></body></html>'''
            _archive(source, members)
            book = read_source(source)
            self.assertFalse(any(unit.allow_number_cleanup for unit in book.units))
            text = ''.join(unit.text for unit in book.units)
            self.assertFalse(align(book.units, text.replace('1普通编号一', '普通编号一').replace('2普通编号二', '普通编号二'), [])['passed'])

    def test_all_occurrences_and_order_are_required(self):
        units = [SourceUnit('doc', value, 'p') for value in ('FIRST', 'REPEATED', 'MIDDLE', 'REPEATED', 'LAST')]
        self.assertTrue(align(units, 'FIRSTREPEATEDMIDDLEREPEATEDLAST', [])['passed'])
        for damaged in ('FIRSTREPEATEDMIDDLELAST', 'FIRSTREPEATEDMIDDLEREPEATEDLASTLAST', 'MIDDLEFIRSTREPEATEDREPEATEDLAST'):
            with self.subTest(damaged=damaged):
                self.assertFalse(align(units, damaged, [])['passed'])

    def test_misleading_and_unknown_child_fonts_cannot_authorize_cleanup(self):
        cases = [
            ('font-family:Monotype Corsiva', 'A普通正文'),
            ('font-family:monospace', 'A<span style="font-family:var(--unknown)">未知正文</span>'),
            ('font-family:monospace', 'call();<span style="font-family:var(--unknown)">未知正文</span>'),
            ('font-family:monospace', 'A<span style="font-family:serif">普通正文</span>'),
        ]
        with tempfile.TemporaryDirectory() as directory:
            source = build_fixtures(Path(directory))['epub-no-toc']
            with ZipFile(source) as archive:
                original = {name: archive.read(name) for name in archive.namelist()}
            for style, text in cases:
                with self.subTest(style=style, text=text):
                    members = dict(original)
                    members['OEBPS/one.xhtml'] = '<html><body>'+''.join(f'<p style="{style}">{number} {text}</p>' for number in (1, 2))+'</body></html>'
                    _archive(source, members)
                    book = read_source(source)
                    self.assertFalse(any(unit.allow_number_cleanup for unit in book.units))

    def test_number_cleanup_needs_source_font_and_consecutive_number_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            source = build_fixtures(Path(directory))['epub-no-toc']
            with ZipFile(source) as archive:
                members = {name: archive.read(name) for name in archive.namelist()}
            members['OEBPS/one.xhtml'] = '''<html><head><title>Code</title><style>.mono {font-family: monospace}</style></head><body>
<p class="mono">1 int first();</p><p class="mono">2 int second();</p>
<p>1 第一条正文</p><p>2 第二条正文</p><p class="mono">99 isolated();</p>
</body></html>'''
            _archive(source, members)
            book = read_source(source)
            self.assertEqual([unit.text for unit in book.units if unit.allow_number_cleanup], ['1intfirst();', '2intsecond();'])
            text = ''.join(unit.payload if unit.allow_number_cleanup else unit.text for unit in book.units)
            self.assertTrue(align(book.units, text, [])['passed'])
            self.assertFalse(align(book.units, text.replace('1第一条正文', '第一条正文'), [])['passed'])
            self.assertFalse(align(book.units, text.replace('99isolated();', 'isolated();'), [])['passed'])

    def test_added_body_cannot_hide_in_generated_heading_allowances(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'book.html'
            path.write_text('<html><body><h1>Title</h1><h2 id="sec-one">Chapter</h2><p>BODYEXTRA</p></body></html>')
            text, ranges, _, _ = _view(path, {'sec-one': 'Chapter'}, book_title='Title')
            result = align([SourceUnit('doc', 'BODY', 'p')], text, ranges)
            self.assertFalse(result['passed'])
            self.assertEqual(result['unexpected_output'][0]['characters'], len('EXTRA'))
            path.write_text('<html><body><h1>Title</h1><h2 id="sec-invented">Invented prose</h2><p>BODY</p></body></html>')
            with self.assertRaises(ValueError):
                _view(path, {'sec-one': 'Chapter'}, book_title='Title')

    def test_duplicate_titles_with_distinct_ids_are_valid_but_duplicate_ids_fail(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'book.html'
            path.write_text('<h2 id="sec-one">同名</h2><p>FIRST</p><h2 id="sec-two">同名</h2><p>SECOND</p>')
            text, ranges, _, _ = _view(path, {'sec-one': '同名', 'sec-two': '同名'})
            self.assertTrue(align([SourceUnit('doc', 'FIRST', 'p'), SourceUnit('doc', 'SECOND', 'p')], text, ranges)['passed'])
            path.write_text(path.read_text().replace('sec-two', 'sec-one'))
            with self.assertRaises(ValueError):
                _view(path, {'sec-one': '同名', 'sec-two': '同名'})

    def test_epub3_navigation_is_independent_and_preserves_parent_and_targets(self):
        with tempfile.TemporaryDirectory() as directory:
            source = build_fixtures(Path(directory))['epub-structured']
            book = read_source(source)
            self.assertEqual(len(book.navigation), 3)
            self.assertEqual([item['parent'] for item in book.navigation], [None, 0, None])
            self.assertEqual(book.navigation[1]['fragment'], 'sub')
            self.assertEqual(book.navigation[2]['path'], 'OEBPS/second.xhtml')
            self.assertEqual(book.documents, ['OEBPS/chapter_split_000.xhtml', 'OEBPS/chapter_split_001.xhtml', 'OEBPS/second.xhtml'])
            self.assertIn('BODY_CONTINUATION_110', [unit.text for unit in book.units])


if __name__ == '__main__':
    unittest.main()
