"""Source-authored EPUB gates apply to every canonical output view."""

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from zipfile import ZipFile

from bs4 import BeautifulSoup
import markdown
import tinycss2
from cssselect2 import compile_selector_list

from any_to_md.application.convert import ConversionRequest, convert
from tests.quality.fixtures import _archive, build_fixtures, load_cases
from tests.quality.inspection import inspect_output, inspect_pdf


class CanonicalEpubTests(unittest.TestCase):
    def test_code_number_cleanup_respects_author_font_cascade(self):
        cases = [
            ('#one,#two{font-family:serif!important}.mono{font-family:monospace}', '', False),
            ('#one,#two{font-family:serif}.mono{font-family:monospace}', '', False),
            ('.mono{font-family:serif}.mono{font-family:monospace}', '', True),
            ('#one,#two{font-family:monospace!important}', 'font-family:serif', True),
            ('#one,#two{font-family:monospace!important}', 'font-family:serif!important', False),
            ('body{font-family:monospace!important}.mono{font-family:serif}', '', False),
            ('.mono{font-family:monospace;font:12px serif}', '', False),
            ('.mono{font-family:Arial,monospace}', '', False),
            ('.mono{font-family:monospace}@media screen{.mono{font-family:serif}}', '', False),
            ('.mono{font-family:"Source Code Pro"}', '', True),
            ('.mono{font-family:"source code pro"}', '', True),
            ('.mono{font-family:Monospace}', '', True),
            ('.mono{font-family:"Courier New"}', '', True),
            ('.mono{font-family:"Monotype Corsiva"}', '', False),
            ('.mono{font-family:monotype corsiva}', '', False),
            ('.mono{font-family:UnknownMonoFont}', '', False),
        ]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = build_fixtures(root/'inputs')['epub-no-toc']
            with ZipFile(source) as archive:
                original = {name: archive.read(name) for name in archive.namelist()}
            for css, inline, expected_code in cases:
                with self.subTest(css=css, inline=inline):
                    members = dict(original)
                    members['OEBPS/one.xhtml'] = f'<html><head><title>级联样本</title><style>{css}</style></head><body><p id="one" class="mono" style="{inline}">1 int first();</p><p id="two" class="mono" style="{inline}">2 int second();</p></body></html>'
                    _archive(source, members)
                    result = convert(ConversionRequest(source, root/'data', ('html',)))
                    soup = BeautifulSoup((result.output_dir/'book.html').read_text(), 'html.parser')
                    self.assertEqual(bool(soup.find('pre')), expected_code)
                    if not expected_code:
                        self.assertIn('1 int first();', soup.get_text())
                        self.assertIn('2 int second();', soup.get_text())

    def test_mixed_fonts_preserve_prose_and_unknown_text_but_allow_code_comments(self):
        cases = [
            ('var(--unknown)', 'A', '普通正文', False),
            ('var(--unknown)', 'call(); /* ', '未知注释 */', False),
            ('serif!important', 'A', '普通正文', False),
            ('UnknownFont', 'A', '普通正文', False),
            ('serif', 'call(); ', 'proportional prose', False),
            ('"PingFang SC"', 'call(); /* ', '中文注释 */', True),
        ]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = build_fixtures(root/'inputs')['epub-no-toc']
            with ZipFile(source) as archive:
                original = {name: archive.read(name) for name in archive.namelist()}
            for font, mono, fallback, code in cases:
                with self.subTest(font=font, mono=mono):
                    members = dict(original)
                    paragraphs = ''.join(f'<p class="mono">{number} {mono}<span style=\'font-family:{font}\'>{fallback}</span></p>' for number in (1, 2))
                    members['OEBPS/one.xhtml'] = '<html><head><title>混合字体</title><style>.mono{font-family:monospace}</style></head><body>'+paragraphs+'</body></html>'
                    _archive(source, members)
                    result = convert(ConversionRequest(source, root/'data', ('html', 'markdown')))
                    soup = BeautifulSoup((result.output_dir/'book.html').read_text(), 'html.parser')
                    self.assertEqual(bool(soup.find('pre')), code)
                    if code:
                        self.assertEqual(soup.pre.get_text(), (mono+fallback+'\n')*2)
                    else:
                        for number in (1, 2):
                            self.assertIn(f'{number} {mono}{fallback}', soup.get_text())
                            rendered = markdown.markdown((result.output_dir/'book.md').read_text(), extensions=['fenced_code'])
                            self.assertIn(f'{number} {mono}{fallback}', BeautifulSoup(rendered, 'html.parser').get_text())

    def test_unlisted_pages_use_source_titles_or_readable_labels(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = build_fixtures(root / 'inputs')['epub-no-toc']
            with ZipFile(source) as archive:
                members = {name: archive.read(name) for name in archive.namelist()}
            members['OEBPS/one.xhtml'] = '<html><head><title>阅读说明</title></head><body><p>NO_TOC_FIRST_201</p></body></html>'
            members['OEBPS/two.xhtml'] = '<html><head><title>未知</title></head><body><p>NO_TOC_SECOND_202</p></body></html>'
            _archive(source, members)
            result = convert(ConversionRequest(source, root / 'data', ('markdown', 'html')))
            soup = BeautifulSoup((result.output_dir / 'book.html').read_text(), 'html.parser')
            self.assertEqual([heading.get_text() for heading in soup.find_all('h2', id=True)], ['阅读说明', '正文 2'])
            self.assertIn('NO_TOC_FIRST_201', soup.get_text())
            self.assertIn('NO_TOC_SECOND_202', soup.get_text())
            for path in [result.output_dir / 'book.md', result.output_dir / 'TOC.md']:
                text = path.read_text()
                self.assertIn('阅读说明', text)
                self.assertIn('正文 2', text)
                self.assertNotIn('one.xhtml', text)
                self.assertNotIn('two.xhtml', text)

    def test_numbered_code_crosses_layout_wrappers_without_consuming_prose(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = build_fixtures(root / "inputs")["epub-plain"]
            with ZipFile(source) as archive:
                members = {name: archive.read(name) for name in archive.namelist()}
            extra = '''<div><p><span class="mono">1 #include</span>＜<span class="mono">stdio.h</span>＞</p></div>
<div><h2 class="mono">2</h2></div><div><p class="mono">3 int main() {</p><p class="mono">4   return 0;</p><p class="mono">5 }</p></div>
<p>1 正文列表</p><p>2 正文列表继续</p>
<p class="mono">1 keep_media();<img src="assets/red.png" alt="source symbol"/></p><p class="mono">2 keep_following();</p>
<p class="mono">1 <a href="https://example.org/code">keep_link();</a></p><p class="mono">2 keep_link_following();</p>
<p>代码清单 9-9 <img src="assets/blue.png" alt="caption symbol"/> <a href="https://example.org/caption">图例来源</a></p>
<p class="mono">1 before_barrier();</p><div><span>PROSE_BARRIER</span></div><p class="mono">2 after_barrier();</p>'''
            members["OEBPS/plain.xhtml"] = members["OEBPS/plain.xhtml"].decode().replace('</body>', extra + '</body>').replace('</head>', '<style>.mono {font-family: monospace}</style></head>')
            _archive(source, members)
            result = convert(ConversionRequest(source, root / "data", ("markdown", "html")))
            soup = BeautifulSoup((result.output_dir / 'book.html').read_text(), 'html.parser')
            self.assertEqual(len(soup.find_all('pre')), 1)
            self.assertEqual(soup.pre.code.get_text(), '#include＜stdio.h＞\n\nint main() {\n  return 0;\n}\n')
            self.assertIsNotNone(soup.find('p', string='1 正文列表'))
            self.assertIsNotNone(soup.find('p', string='2 正文列表继续'))
            self.assertIsNotNone(soup.find('p', string='1 before_barrier();'))
            self.assertIsNotNone(soup.find('p', string='2 after_barrier();'))
            self.assertIn('PROSE_BARRIER', soup.get_text())
            self.assertIsNotNone(soup.find('img', alt='source symbol'))
            self.assertIsNotNone(soup.find('a', href='https://example.org/code'))
            self.assertIsNotNone(soup.find('img', alt='caption symbol'))
            self.assertIsNotNone(soup.find('a', href='https://example.org/caption'))
            rendered_md = BeautifulSoup(markdown.markdown((result.output_dir / 'book.md').read_text(), extensions=['fenced_code']), 'html.parser')
            self.assertEqual(rendered_md.pre.code.get_text(), soup.pre.code.get_text())
            self.assertIsNotNone(rendered_md.find('img', alt='source symbol'))
            self.assertIsNotNone(rendered_md.find('a', href='https://example.org/code'))
            self.assertIsNotNone(rendered_md.find('img', alt='caption symbol'))
            self.assertIsNotNone(rendered_md.find('a', href='https://example.org/caption'))

    def test_code_with_separate_monospace_spans_and_cjk_comments_stays_one_block(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = build_fixtures(root/'inputs')['epub-plain']
            with ZipFile(source) as archive:
                members = {name: archive.read(name) for name in archive.namelist()}
            listing = '''<p><span class="mono">1 movw $0x0c,%bx</span> <span class="mono">#</span><span style="font-family:PingFang SC">文字为红色</span></p>
<p><span class="mono">2 movw $12,%cx</span> <span class="mono">#</span><span style="font-family:PingFang SC">字符串长度</span></p>'''
            members['OEBPS/plain.xhtml'] = members['OEBPS/plain.xhtml'].decode().replace('</body>', listing+'</body>').replace('</head>', '<style>.mono{font-family:Source Code Pro}</style></head>')
            _archive(source, members)
            result = convert(ConversionRequest(source, root/'data', ('html', 'markdown')))
            html = BeautifulSoup((result.output_dir/'book.html').read_text(), 'html.parser')
            self.assertIsNotNone(html.pre)
            self.assertEqual(html.pre.get_text(), 'movw $0x0c,%bx #文字为红色\nmovw $12,%cx #字符串长度\n')
            rendered = BeautifulSoup(markdown.markdown((result.output_dir/'book.md').read_text(), extensions=['fenced_code']), 'html.parser')
            self.assertEqual(rendered.pre.get_text(), html.pre.get_text())

    @unittest.skipUnless(os.environ.get("ANY_TO_MD_TEST_PDF") == "1", "需要显式启用原生 PDF 渲染环境")
    def test_pdf_preserves_epub_fixture_body_order_and_images(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inputs = build_fixtures(root / "inputs")
            for case in (case for case in load_cases() if case["kind"] == "epub"):
                with self.subTest(case=case["id"]):
                    result = convert(ConversionRequest(inputs[case["id"]], root / "data", ("pdf",)))
                    observation = inspect_pdf(result.output_dir / "book.pdf", case)
                    self.assertTrue(observation["passed"], observation["failures"])

    def test_title_media_and_ancestor_styles_survive_chapter_partitioning(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sources = build_fixtures(root / "inputs")
            plain = sources["epub-plain"]
            with ZipFile(plain) as archive:
                members = {name: archive.read(name) for name in archive.namelist()}
            red = members["OEBPS/assets/red.png"]
            members["OEBPS/plain.xhtml"] = members["OEBPS/plain.xhtml"].decode().replace('</h1>', '<img src="assets/red.png"/><a href="https://example.org/title">来源</a></h1>')
            _archive(plain, members)
            result = convert(ConversionRequest(plain, root / "data", ("markdown", "html")))
            for content in ((result.output_dir / "book.html").read_text(), markdown.markdown((result.output_dir / "book.md").read_text())):
                soup = BeautifulSoup(content, "html.parser")
                self.assertIsNotNone(soup.find("a", href="https://example.org/title"))
                images = soup.find_all("img")
                self.assertEqual(len(images), 1)
                self.assertEqual((result.output_dir / images[0]["src"]).read_bytes(), red)

            source = sources["epub-anchors"]
            with ZipFile(source) as archive:
                members = {name: archive.read(name) for name in archive.namelist()}
            members["OEBPS/one.xhtml"] = members["OEBPS/one.xhtml"].decode().replace('<div>', '<div id="ancestor">')
            members["OEBPS/style.css"] += b'\n#ancestor p {color:#bada55}'
            _archive(source, members)
            result = convert(ConversionRequest(source, root / "data", ("html",)))
            soup = BeautifulSoup((result.output_dir / "book.html").read_text(), "html.parser")
            rules = tinycss2.parse_stylesheet(soup.style.get_text(), skip_comments=True, skip_whitespace=True)
            selector = next(tinycss2.serialize(rule.prelude) for rule in rules if rule.type == 'qualified-rule' and '#bada55' in tinycss2.serialize(rule.content))
            self.assertEqual([tag.get_text() for tag in soup.select(selector)], ["PREFIX_301", "ANCHOR_FIRST_302", "ANCHOR_SUB_303"])
            ids = [tag['id'] for tag in soup.find_all(id=True)]
            self.assertEqual(len(ids), len(set(ids)))

    def test_root_attributes_cascade_and_svg_references_survive(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = build_fixtures(root / "inputs")["epub-plain"]
            with ZipFile(source) as archive:
                members = {name: archive.read(name) for name in archive.namelist()}
            members["OEBPS/plain.xhtml"] = '''<html xmlns="http://www.w3.org/1999/xhtml" id="root-anchor" class="root"><head>
<style>body.book {background-color: red} :is(#first, #second){color:red} html > body {padding-top:3px}
.book p {margin-left:11px} body#body-anchor {border-top:2px solid blue} html#root-anchor {font-size:14px}
#first {color:#aa0000} .book {color:#0000aa}
:is(html) > :is(body) [id="first"] {padding-right:13px} :root {text-indent:1px}</style>
<link rel="stylesheet" href="z.css"/><style>p{color:green}</style><link rel="stylesheet" href="a.css" media="screen"/>
<style media="print">p {letter-spacing:1px}</style><link rel="alternate stylesheet" title="unused" href="unused.css"/>
</head><body id="body-anchor" class="book" style="padding-left:19px" lang="ar" dir="rtl">
<h1 id="start">普通章节</h1><p id="first" class="book">PLAIN_BODY_001</p><p id="second">second</p><a href="#body-anchor">back</a><a href="#root-anchor">root</a>
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><defs><linearGradient id="gradient"><stop offset="0" stop-color="red"/></linearGradient></defs>
<rect id="rect" width="10" height="10" fill="url(#gradient)"/><use xmlns:xlink="http://www.w3.org/1999/xlink" xlink:href="#rect"/></svg>
</body></html>'''
            members["OEBPS/z.css"] = '@import url(css/print.css) print; @import "css/print.css" screen; @import "css/layer.css" layer(theme) supports(display: block) print; p {color:blue}'
            members["OEBPS/a.css"] = "p {color:purple}"
            members["OEBPS/css/print.css"] = 'p {border-right:8px solid orange} @import "../z.css";'
            members["OEBPS/css/layer.css"] = 'p {font-weight:700}'
            package = members["OEBPS/package.opf"].decode()
            additions = ''.join(f'<item id="css{i}" href="{path}" media-type="text/css"/>' for i, path in enumerate(("z.css", "a.css", "css/print.css", "css/layer.css")))
            members["OEBPS/package.opf"] = package.replace('</manifest>', additions + '</manifest>')
            _archive(source, members)
            result = convert(ConversionRequest(source, root / "data", ("markdown", "html")))
            rendered = (result.output_dir / "book.html").read_text()
            soup = BeautifulSoup(rendered, "html.parser")
            wrapper = soup.find(attrs={"data-source-body": True})
            self.assertEqual(wrapper["class"], ["book"])
            self.assertEqual(wrapper["dir"], "rtl")
            self.assertEqual(wrapper["lang"], "ar")
            self.assertIn("padding-left:19px", wrapper["style"])
            back = soup.find("a", string="back")
            self.assertIsNotNone(soup.find(id=back["href"][1:]))
            self.assertIsNotNone(soup.find(id=soup.find("a", string="root")["href"][1:]))
            css = soup.style.get_text()
            self.assertLess(css.index("color:blue"), css.index("color:green"))
            self.assertLess(css.index("color:green"), css.index("color:purple"))
            gradient = soup.find("lineargradient")
            self.assertEqual(soup.rect["fill"], f'url("#{gradient["id"]}")')
            self.assertEqual(soup.find("use")["xlink:href"], "#" + soup.rect["id"])
            self.assertIn(':is(#src-', css)
            self.assertNotIn(', [data-source-scope', css)
            self.assertNotIn(' > body', css)
            self.assertIn('@media print{', css)
            self.assertIn('@media screen{', css)
            self.assertIn('@layer theme{@supports (display: block){@media print{', css)
            self.assertEqual(css.count('border-right:8px solid orange'), 2)
            self.assertNotIn('@import', css)
            self.assertNotIn('unused.css', css)
            # The body class and ID selectors still select the source body after fragmentation.
            rules = tinycss2.parse_stylesheet(css, skip_comments=True, skip_whitespace=True)
            for declaration, specificity in (('margin-left:11px', (0, 1, 1)), ('border-top:2px solid blue', (1, 0, 1)), ('font-size:14px', (1, 0, 1)), ('padding-top:3px', (0, 0, 2)), ('padding-right:13px', (0, 1, 2)), ('text-indent:1px', (0, 1, 0))):
                selector = next(tinycss2.serialize(rule.prelude) for rule in rules if rule.type == 'qualified-rule' and declaration in tinycss2.serialize(rule.content))
                self.assertTrue(soup.select(selector), (declaration, selector))
                self.assertEqual(compile_selector_list(selector)[0].specificity, specificity, selector)
            first = soup.find('p', string='PLAIN_BODY_001')
            candidates = []
            for index, rule in enumerate(rules):
                if rule.type != 'qualified-rule':
                    continue
                selector = tinycss2.serialize(rule.prelude)
                for declaration in tinycss2.parse_declaration_list(rule.content, skip_comments=True, skip_whitespace=True):
                    if declaration.type == 'declaration' and declaration.lower_name == 'color' and first in soup.select(selector):
                        candidates.append((max(item.specificity for item in compile_selector_list(selector)), index, tinycss2.serialize(declaration.value).strip()))
            self.assertEqual(max(candidates)[2], '#aa0000')
            report = json.loads((result.output_dir / "report.json").read_text())
            self.assertTrue(any('循环样式导入' in diagnostic['message'] for diagnostic in report['diagnostics']))
            self.assertIn('<svg', (result.output_dir / "book.md").read_text())

    def test_all_epub_cases_preserve_source_content_in_both_md_views_and_html(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            inputs = build_fixtures(root / "inputs")
            for case in (case for case in load_cases() if case["kind"] == "epub"):
                with self.subTest(case=case["id"]):
                    result = convert(ConversionRequest(inputs[case["id"]], root / "data", ("markdown", "html")))
                    # Inspect an export moved away from its original source tree.
                    export = root / "relocated" / case["id"]
                    shutil.copytree(result.output_dir, export)
                    for view, paths in {
                        "whole": [export / "book.md"],
                        "split": sorted((export / "chapters").glob("*.md")),
                        "html": [export / "book.html"],
                    }.items():
                        observation = inspect_output(paths, case, export)
                        self.assertTrue(observation["passed"], (case["id"], view, observation["failures"]))
                    snapshot = json.loads((export / "book.json").read_text())
                    self.assertEqual(snapshot["schema_version"], 2)
                    self.assertNotIn("content_markdown", (export / "book.json").read_text())
                    ids = [part["fragment_id"] for part in snapshot["content"]]
                    self.assertEqual(len(ids), len(set(ids)))
                    self.assertEqual(sorted(ids), sorted(part for node in snapshot["nodes"] for part in node["content_ids"]))

    def test_styles_survive_and_are_scoped_to_the_source_document(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = build_fixtures(root / "inputs")["epub-structured"]
            result = convert(ConversionRequest(source, root / "data", ("html",)))
            html = (result.output_dir / "book.html").read_text()
            self.assertIn('border-left: 7px solid #c12345', html)
            self.assertIn('color: #154c79', html)
            self.assertIn('[data-source-scope="doc-', html)
            self.assertNotIn('asset://', html)


if __name__ == "__main__":
    unittest.main()
