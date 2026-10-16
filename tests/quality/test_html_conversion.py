"""HTML gates use source-authored body, resource, CSS and navigation expectations."""

import io
import base64
import json
import re
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile
from zipfile import ZipInfo

from bs4 import BeautifulSoup
import markdown
from fastapi.testclient import TestClient

import any_to_md
from any_to_md.application.convert import ConversionRequest, convert
from any_to_md.application.jobs import JobRuntime
from any_to_md.application.jobs import ConversionFailure
from tests.quality.fixtures import _archive, build_fixtures, load_cases
from tests.quality.inspection import inspect_output, inspect_pdf
from web.app import app
from web.config import settings


class HtmlConversionTests(unittest.TestCase):
    def test_section_preludes_and_empty_sections_keep_source_heading_order(self):
        sources = [
            ('<section><p>SECTION_INTRO_940</p><h1>Chapter Alpha</h1><p>SECTION_BODY_941</p></section>', ['Chapter Alpha']),
            ('<section data-title="Empty Alpha"></section><section><h1>Chapter Beta</h1><p>SECTION_BODY_941</p></section>', ['Empty Alpha', 'Chapter Beta']),
            ('<section><h1>Empty Alpha</h1></section><section><h1>Chapter Beta</h1><p>SECTION_BODY_941</p></section>', ['Empty Alpha', 'Chapter Beta']),
        ]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for body, titles in sources:
                with self.subTest(body=body):
                    source = root/'sections.html';source.write_text('<title>Sections</title>'+body)
                    result = convert(ConversionRequest(source, root/'data', ('markdown', 'html')))
                    self.assertEqual(result.status, 'completed')
                    for paths in ([result.output_dir/'book.html'], [result.output_dir/'book.md'], sorted((result.output_dir/'chapters').glob('*.md'))):
                        rendered = '\n'.join(path.read_text() if path.suffix == '.html' else markdown.markdown(path.read_text(), extensions=['tables', 'fenced_code']) for path in paths)
                        soup = BeautifulSoup(rendered, 'html.parser')
                        actual = [heading.get_text() for heading in soup.find_all(re.compile('^h[1-6]$')) if heading.get_text() in titles]
                        self.assertEqual(actual, titles)
                        if 'SECTION_INTRO_940' in body:
                            prefix = soup.find(string=lambda text: 'SECTION_INTRO_940' in text)
                            heading = soup.find(['h1', 'h2'], string=titles[0])
                            positions = {id(element): index for index, element in enumerate(soup.descendants)}
                            self.assertLess(positions[id(prefix)], positions[id(heading)])

    def test_headerless_and_row_header_tables_keep_source_rows_and_cell_roles(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for roles in (('td', 'td', 'td', 'td'), ('th', 'td', 'th', 'td'), ('th', 'th', 'td', 'td')):
                with self.subTest(roles=roles):
                    values = ('TABLE_TOP_LEFT_936', 'TABLE_TOP_RIGHT_937', 'TABLE_BOTTOM_LEFT_938', 'TABLE_BOTTOM_RIGHT_939')
                    cells = [f'<{role}>{text}</{role}>' for role, text in zip(roles, values)]
                    source = root/'table.html'
                    source.write_text('<title>表格角色</title><table><tr>'+''.join(cells[:2])+'</tr><tr>'+''.join(cells[2:])+'</tr></table>')
                    result = convert(ConversionRequest(source, root/'data', ('markdown', 'html')))
                    self.assertEqual(result.status, 'completed')
                    case = {'markers': values, 'required_cells': [{'marker': text, 'row': index//2, 'column': index%2, 'rowspan': 1, 'colspan': 1} for index, text in enumerate(values)]}
                    for paths in ([result.output_dir/'book.html'], [result.output_dir/'book.md'], sorted((result.output_dir/'chapters').glob('*.md'))):
                        checked = inspect_output(paths, case, result.output_dir)
                        self.assertTrue(checked['passed'], checked['failures'])
                        rendered = '\n'.join(path.read_text() if path.suffix == '.html' else markdown.markdown(path.read_text(), extensions=['tables', 'fenced_code']) for path in paths)
                        soup = BeautifulSoup(rendered, 'html.parser')
                        self.assertEqual([cell.name for cell in soup.table.find_all(['th', 'td'])], list(roles))

    def test_repeated_stylesheet_imports_keep_source_cascade_order(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root/'index.html'
            source.write_text('<title>样式顺序</title><style>@import "shared.css"; @import "override.css"; @import "shared.css";</style><p class="tone">CSS_CASCADE_928</p>')
            (root/'shared.css').write_text('.tone {color:#c12345}')
            (root/'override.css').write_text('.tone {color:#154c79}')
            result = convert(ConversionRequest(source, root/'data', ('html',)))
            self.assertEqual(result.status, 'completed')
            css = BeautifulSoup((result.output_dir/'book.html').read_text(), 'html.parser').style.get_text()
            self.assertEqual(css.count('#c12345'), 2)
            self.assertLess(css.index('#c12345'), css.index('#154c79'))
            self.assertGreater(css.rindex('#c12345'), css.index('#154c79'))

    def test_stylesheet_import_expansion_and_work_are_bounded(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root/'index.html'
            source.write_text('<title>样式预算</title><link rel="stylesheet" href="0.css"><p>CSS_BUDGET_929</p>')
            for index in range(20):
                (root/f'{index}.css').write_text(f'@import "{index+1}.css"; @import "{index+1}.css";')
            (root/'20.css').write_text('.tone {color:#c12345}')
            for leaf in ('.tone {color:#c12345}', ''):
                (root/'20.css').write_text(leaf)
                with patch('any_to_md.content.styles.MAX_STYLE_BYTES', 4096), patch('any_to_md.content.styles.MAX_STYLE_RULES', 128):
                    result = convert(ConversionRequest(source, root/'data', ('html',)))
                self.assertEqual(result.status, 'partial')
                snapshot = json.loads((result.output_dir/'book.json').read_text())
                self.assertLess(sum(len(css.encode()) for css in snapshot['styles'].values()), 4096)
                self.assertIn('CSS_BUDGET_929', (result.output_dir/'book.html').read_text())
                self.assertTrue(any(item['code'] == 'stylesheet_limit' and item['severity'] == 'error' for item in snapshot['diagnostics']))

    def test_stylesheet_budget_is_shared_across_reachable_html_pages(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for index in range(20):
                following = f'<a href="{index+1}.html">Next</a>' if index < 19 else ''
                (root/f'{index}.html').write_text(f'<title>Page {index}</title><style>.tone {{color:#c12345}}</style><p>CSS_PAGE_{index:02d}</p>{following}')
            with patch('any_to_md.content.styles.MAX_STYLE_BYTES', 512):
                result = convert(ConversionRequest(root/'0.html', root/'data', ('html',)))
            self.assertEqual(result.status, 'partial')
            snapshot = json.loads((result.output_dir/'book.json').read_text())
            self.assertLessEqual(sum(len(css.encode()) for css in snapshot['styles'].values()), 512)
            html = BeautifulSoup((result.output_dir/'book.html').read_text(), 'html.parser')
            for index in range(20):
                self.assertEqual(html.get_text().count(f'CSS_PAGE_{index:02d}'), 1)

    def test_xml_base_links_import_captured_local_body_and_keep_target(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/'pages').mkdir()
            source = root/'index.html'
            source.write_text('<title>XML 导航</title><p>XML_MAIN_926</p><svg xml:base="pages/"><a href="details.html#note"><text>Details</text></a></svg>')
            (root/'pages'/'details.html').write_text('<title>Details</title><p id="note">XML_BASE_DETAIL_927</p>')
            result = convert(ConversionRequest(source, root/'data', ('html', 'markdown')))
            self.assertEqual(result.status, 'completed')
            case = {'markers': ['XML_MAIN_926', 'XML_BASE_DETAIL_927'], 'required_links': [{'label': 'Details', 'target_marker': 'XML_BASE_DETAIL_927'}]}
            check = inspect_output([result.output_dir/'book.html'], case, result.output_dir)
            self.assertTrue(check['passed'], check['failures'])

    def test_svg_expansion_budget_is_shared_by_all_reachable_html_pages(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            svg = b'<svg xmlns="http://www.w3.org/2000/svg"><rect width="10" height="10"/></svg>'
            data = 'data:image/svg+xml;base64,'+base64.b64encode(svg).decode()
            for index in range(20):
                following = f'<a href="{index+1}.html">Next</a>' if index < 19 else ''
                (root/f'{index}.html').write_text(f'<title>Page {index}</title><p>PAGE_BODY_{index:02d}</p><img src="{data}">{following}')
            with patch('any_to_md.content.media.MAX_SVG_BYTES', 1024), patch('any_to_md.content.media.MAX_EXPANDED_BYTES', 2048):
                result = convert(ConversionRequest(root/'0.html', root/'data', ('html',)))
            self.assertEqual(result.status, 'partial')
            html = BeautifulSoup((result.output_dir/'book.html').read_text(), 'html.parser')
            for index in range(20):
                self.assertEqual(html.get_text().count(f'PAGE_BODY_{index:02d}'), 1)
            report = json.loads(result.report_path.read_text())
            self.assertTrue(any('大小限制' in item['message'] for item in report['diagnostics']))
            self.assertLess(sum(len(image.get('src', '')) for image in html.find_all('img')), 2048)

    def test_unidentified_html_sections_have_distinct_persistent_identities(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for body in ('<h1>First</h1><p>FIRST_BODY_918</p><h1>Second</h1><p>SECOND_BODY_919</p>', '<section><h1>First</h1><p>FIRST_BODY_918</p></section><section><h1>Second</h1><p>SECOND_BODY_919</p></section>'):
                source = root/'index.html'
                source.write_text('<title>无 ID 章节</title>'+body)
                first = convert(ConversionRequest(source, root/'data', ('markdown', 'html')))
                again = convert(ConversionRequest(source, root/'data', ('markdown', 'html')))
                self.assertEqual(first.revision_id, again.revision_id)
                snapshot = json.loads((first.output_dir/'book.json').read_text())
                self.assertEqual(len({node['source_key'] for node in snapshot['nodes']}), 2)
                for paths in ([first.output_dir/'book.html'], [first.output_dir/'book.md'], sorted((first.output_dir/'chapters').glob('*.md'))):
                    check = inspect_output(paths, {'markers': ['FIRST_BODY_918', 'SECOND_BODY_919']}, first.output_dir)
                    self.assertTrue(check['passed'], check['failures'])

    def test_media_only_sections_keep_players_and_portable_sources(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root/'index.html'
            source.write_text('<title>媒体文档</title><section><h1>正文</h1><p>MEDIA_BODY_920</p></section><section data-title="媒体"><video controls src="clip.mp4"></video><audio controls src="sound.mp3"></audio></section>')
            (root/'clip.mp4').write_bytes(b'video-resource-bytes')
            (root/'sound.mp3').write_bytes(b'audio-resource-bytes')
            result = convert(ConversionRequest(source, root/'data', ('html', 'markdown')))
            self.assertEqual(result.status, 'completed')
            for paths in ([result.output_dir/'book.html'], [result.output_dir/'book.md'], sorted((result.output_dir/'chapters').glob('*.md'))):
                for tag in ('video', 'audio'):
                    players = [(path, player) for path in paths for player in BeautifulSoup(path.read_text(), 'html.parser').find_all(tag)]
                    self.assertEqual(len(players), 1)
                    path, player = players[0]
                    self.assertEqual((path.parent/player['src']).read_bytes(), b'video-resource-bytes' if tag == 'video' else b'audio-resource-bytes')

    def test_svg_unsupported_foreign_object_reports_partial(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root/'index.html'
            source.write_text('<title>图示限制</title><p>SVG_LABEL_BODY_921</p><img src="diagram.svg">')
            (root/'diagram.svg').write_text('<svg xmlns="http://www.w3.org/2000/svg"><foreignObject width="100" height="30"><div xmlns="http://www.w3.org/1999/xhtml">SOURCE_DIAGRAM_LABEL_719</div></foreignObject></svg>')
            result = convert(ConversionRequest(source, root/'data', ('html',)))
            self.assertEqual(result.status, 'partial')
            report = json.loads(result.report_path.read_text())
            self.assertTrue(any(item['severity'] == 'error' and 'foreignObject' in item['message'] for item in report['diagnostics']))

    def test_svg_expansion_is_bounded_before_data_url_allocation(self):
        from any_to_md.content.media import MediaFreezer

        members = {f'{index}.svg': f'<svg xmlns="http://www.w3.org/2000/svg"><image href="{index+1}.svg"/><image href="{index+1}.svg"/></svg>'.encode() for index in range(20)}
        members['20.svg'] = b'<svg xmlns="http://www.w3.org/2000/svg"><rect width="10" height="10"/></svg>'
        diagnostics = []
        freezer = MediaFreezer(lambda path: (members[path], 'image/svg+xml'), diagnostics.append)
        with patch('any_to_md.content.media.MAX_SVG_BYTES', 4096), patch('any_to_md.content.media.MAX_EXPANDED_BYTES', 8192):
            result = freezer.svg(members['0.svg'], '0.svg')
        self.assertLessEqual(freezer.expanded_bytes, 8192)
        self.assertLessEqual(len(result), 4096)
        self.assertTrue(any('大小限制' in item for item in diagnostics), diagnostics)

    def test_reachable_local_html_pages_keep_links_and_each_body_once(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root/'index.html'
            source.write_text('<html><head><title>主页</title></head><body><p>HTML_MAIN_911 <a href="details.html#note">Details</a></p></body></html>')
            (root/'details.html').write_text('<html><head><title>说明</title></head><body><p id="note">HTML_DETAIL_912 <a href="index.html">返回主页</a></p></body></html>')
            (root/'unreferenced.html').write_text('<p>UNREFERENCED_NOT_IMPORTED</p>')
            result = convert(ConversionRequest(source, root/'data', ('markdown', 'html')))
            self.assertEqual(result.status, 'completed')
            case = {'markers': ['HTML_MAIN_911', 'HTML_DETAIL_912'], 'required_links': [{'label': 'Details', 'target_marker': 'HTML_DETAIL_912'}, {'label': '返回主页', 'target_marker': 'HTML_MAIN_911'}]}
            for paths in ([result.output_dir/'book.html'], [result.output_dir/'book.md'], sorted((result.output_dir/'chapters').glob('*.md'))):
                check = inspect_output(paths, case, result.output_dir)
                self.assertTrue(check['passed'], check['failures'])
                self.assertTrue(all('UNREFERENCED_NOT_IMPORTED' not in path.read_text() for path in paths))

    def test_base_uri_and_data_srcset_keep_local_resource_context(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = build_fixtures(root/'inputs')['html-rich']
            media = root/'media'
            media.mkdir()
            red = (fixture.parent/'assets'/'red.png').read_bytes()
            blue = (fixture.parent/'assets'/'blue.png').read_bytes()
            (media/'blue.png').write_bytes(blue)
            (media/'main.css').write_text('body {border-top:7px solid #654321}')
            data = 'data:image/png;base64,'+base64.b64encode(red).decode()
            source = root/'index.html'
            source.write_text(f'<html><head><title>资源上下文</title><base href="media/"><link rel="stylesheet" href="main.css"></head><body><p id="note">BASE_BODY_913</p><img src="blue.png" srcset="{data} 1x, blue.png 2x"><a href="../index.html#note">注释</a></body></html>')
            result = convert(ConversionRequest(source, root/'data', ('html', 'markdown')))
            self.assertEqual(result.status, 'completed')
            html = BeautifulSoup((result.output_dir/'book.html').read_text(), 'html.parser')
            self.assertIn(data+' 1x, ', html.img['srcset'])
            self.assertTrue(html.img['srcset'].endswith(html.img['src']+' 2x'))
            self.assertEqual((result.output_dir/html.img['src']).read_bytes(), blue)
            self.assertIn('#654321', html.style.get_text())
            self.assertIsNotNone(html.find(id=html.find('a', string='注释')['href'][1:]))

    def test_invalid_archive_paths_are_persisted_as_failure_without_extracting(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ('../outside.png', '/absolute.png', 'folder\\outside.png'):
                with self.subTest(name=name):
                    bundle = root/'unsafe.zip'
                    _archive(bundle, {'index.html': '<p>BODY_SAFE</p>', name: b'no extraction'})
                    with self.assertRaises(ConversionFailure) as caught:
                        convert(ConversionRequest(bundle, root/'data'))
                    job = JobRuntime(root/'data').get(caught.exception.job_id)
                    self.assertEqual(job.status.value, 'failed')
                    self.assertFalse((root/'outside.png').exists())
                    with self.assertRaises(ValueError):
                        JobRuntime(root/'data').download(job.job_id)

    def test_archive_symlinks_duplicate_members_and_expansion_quotas_fail(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle = root/'source.zip'
            for kind in ('symlink', 'duplicate', 'count', 'bytes'):
                with self.subTest(kind=kind):
                    with ZipFile(bundle, 'w') as archive:
                        archive.writestr('index.html', '<p>ARCHIVE_BODY_930</p>')
                        if kind == 'symlink':
                            link = ZipInfo('assets/link.png')
                            link.create_system = 3
                            link.external_attr = 0o120777 << 16
                            archive.writestr(link, '../../outside.png')
                        elif kind == 'duplicate':
                            archive.writestr('./index.html', '<p>Duplicate body</p>')
                        else:
                            archive.writestr('assets/resource.png', b'123456789')
                    with patch('any_to_md.adapters.html_source.MAX_SOURCE_FILES', 1 if kind == 'count' else 10000), patch('any_to_md.adapters.html_source.MAX_SOURCE_BYTES', 8 if kind == 'bytes' else 256*1024*1024):
                        with self.assertRaises(ConversionFailure) as caught:
                            convert(ConversionRequest(bundle, root/'data'))
                    self.assertEqual(JobRuntime(root/'data').get(caught.exception.job_id).status.value, 'failed')
                    self.assertFalse((root/'assets').exists())

    @unittest.skipUnless(os.environ.get('ANY_TO_MD_TEST_PDF') == '1', '需要显式启用原生 PDF 渲染环境')
    def test_svg_file_and_data_urls_cannot_read_outside_source_context(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_dir = root/'inputs'
            source_dir.mkdir()
            fixture = build_fixtures(root/'fixtures')['html-rich']
            outside = root/'outside-private.png'
            outside.write_bytes((fixture.parent/'assets'/'red.png').read_bytes())
            svg = f'<svg xmlns="http://www.w3.org/2000/svg" width="64" height="32"><image width="64" height="32" href="{outside.as_uri()}"/></svg>'
            (source_dir/'picture.svg').write_text(svg)
            data = 'data:image/svg+xml;base64,'+base64.b64encode(svg.encode()).decode()
            source = source_dir/'index.html'
            source.write_text(f'<html><head><title>SVG隔离</title></head><body><p>SVG_BODY_914</p><img src="picture.svg"><img src="{data}"></body></html>')
            result = convert(ConversionRequest(source, root/'data', ('html', 'pdf')))
            self.assertEqual(result.status, 'partial')
            snapshot = json.loads((result.output_dir/'book.json').read_text())
            self.assertTrue(any(item['code'] == 'svg_resource' for item in snapshot['diagnostics']))
            for path in (result.output_dir/'assets').glob('*.svg'):
                self.assertNotIn(outside.as_uri(), path.read_text())
            html = BeautifulSoup((result.output_dir/'book.html').read_text(), 'html.parser')
            sanitized = base64.b64decode(html.find_all('img')[1]['src'].split(',', 1)[1])
            self.assertNotIn(outside.as_uri().encode(), sanitized)
            import pymupdf
            with pymupdf.open(result.output_dir/'book.pdf') as pdf:
                self.assertFalse(any(page.get_images() for page in pdf))

    @unittest.skipUnless(os.environ.get('ANY_TO_MD_TEST_PDF') == '1', '需要显式启用原生 PDF 渲染环境')
    def test_pdf_fetcher_rejects_nested_file_and_network_even_without_reader(self):
        from any_to_md.writers.pdf import render_pdf

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            export = root/'export'
            export.mkdir()
            fixture = build_fixtures(root/'fixtures')['html-rich']
            outside = fixture.parent/'assets'/'red.png'
            for reference in (outside.as_uri(), 'https://example.invalid/image.png'):
                with self.subTest(reference=reference):
                    svg = f'<svg xmlns="http://www.w3.org/2000/svg" width="64" height="32"><image width="64" height="32" href="{reference}"/></svg>'
                    source = export/'book.html'
                    source.write_text('<img src="data:image/svg+xml;base64,'+base64.b64encode(svg.encode()).decode()+'">')
                    with self.assertRaisesRegex(ValueError, 'PDF 拒绝读取'):
                        render_pdf(source)
                    self.assertFalse(source.with_suffix('.pdf').exists())

    @unittest.skipUnless(os.environ.get('ANY_TO_MD_TEST_PDF') == '1', '需要显式启用原生 PDF 渲染环境')
    def test_local_nested_svg_dependencies_keep_source_pixels_after_relocation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = build_fixtures(root/'fixtures')['html-rich']
            source_dir = root/'source'
            (source_dir/'media').mkdir(parents=True)
            shutil.copytree(fixture.parent/'assets', source_dir/'assets')
            (source_dir/'media'/'inner.svg').write_text('<svg xmlns="http://www.w3.org/2000/svg" width="64" height="32"><image width="64" height="32" href="../assets/red.png"/></svg>')
            (source_dir/'media'/'outer.svg').write_text('<svg xmlns="http://www.w3.org/2000/svg" width="64" height="32"><image width="64" height="32" href="inner.svg"/></svg>')
            svg = '<svg xmlns="http://www.w3.org/2000/svg" width="64" height="32"><image width="64" height="32" href="assets/blue.png"/></svg>'
            data = 'data:image/svg+xml;base64,'+base64.b64encode(svg.encode()).decode()
            source = source_dir/'index.html'
            source.write_text(f'<html><head><title>嵌套图像</title></head><body><p>SVG_LOCAL_915</p><img src="media/outer.svg"><img src="{data}"></body></html>')
            runtime = JobRuntime(root/'data')
            job = runtime.file_job(source, ('html', 'pdf'))
            shutil.rmtree(source_dir)
            result = convert(ConversionRequest(data_dir=root/'data', job_id=job.job_id))
            self.assertEqual(result.status, 'completed')
            relocated = root/'relocated'
            shutil.copytree(result.output_dir, relocated)
            case = next(case for case in load_cases() if case['id'] == 'html-rich')
            case = {'markers': ['SVG_LOCAL_915'], 'minimum_images': 2, 'image_pixels': case['image_pixels']}
            observation = inspect_pdf(relocated/'book.pdf', case)
            self.assertTrue(observation['passed'], observation['failures'])

    @unittest.skipUnless(os.environ.get('ANY_TO_MD_TEST_PDF') == '1', '需要显式启用原生 PDF 渲染环境')
    def test_svg_inherited_xml_base_keeps_captured_local_pixels(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = build_fixtures(root/'fixtures')['html-rich']
            source_dir = root/'source'
            shutil.copytree(fixture.parent/'assets', source_dir/'media')
            source = source_dir/'index.html'
            source.write_text('<title>XML 基准</title><p>XML_BASE_BODY_922</p><img src="diagram.svg"><svg xml:base="media/" width="64" height="32"><image width="64" height="32" href="blue.png"/></svg>')
            (source_dir/'diagram.svg').write_text('<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64" xml:base="media/"><g xml:base="./"><image width="64" height="32" href="red.png"/><image y="32" width="64" height="32" href="blue.png"/></g></svg>')
            runtime = JobRuntime(root/'data')
            job = runtime.file_job(source, ('html', 'pdf'))
            shutil.rmtree(source_dir)
            result = convert(ConversionRequest(data_dir=root/'data', job_id=job.job_id))
            self.assertEqual(result.status, 'completed')
            html = BeautifulSoup((result.output_dir/'book.html').read_text(), 'html.parser')
            self.assertFalse(html.svg.has_attr('xml:base'))
            self.assertEqual((result.output_dir/html.svg.image['href']).read_bytes(), (fixture.parent/'assets'/'blue.png').read_bytes())
            case = next(case for case in load_cases() if case['id'] == 'html-rich')
            check = inspect_pdf(result.output_dir/'book.pdf', {'markers': ['XML_BASE_BODY_922'], 'minimum_images': 2, 'image_pixels': case['image_pixels']})
            self.assertTrue(check['passed'], check['failures'])

    @unittest.skipUnless(os.environ.get('ANY_TO_MD_TEST_PDF') == '1', '需要显式启用原生 PDF 渲染环境')
    def test_pdf_with_unreferenced_svg_image_keeps_available_body(self):
        from any_to_md.writers.pdf import render_pdf

        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory)/'book.html'
            svg = '<svg xmlns="http://www.w3.org/2000/svg" width="64" height="32"><image width="64" height="32"/></svg>'
            source.write_text('<p>EMPTY_SVG_BODY_916</p><img src="data:image/svg+xml;base64,'+base64.b64encode(svg.encode()).decode()+'">')
            result = render_pdf(source)
            observation = inspect_pdf(result, {'markers': ['EMPTY_SVG_BODY_916']})
            self.assertTrue(observation['passed'], observation['failures'])

    def test_rich_html_preserves_source_content_resources_and_all_views(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = build_fixtures(root/'inputs')['html-rich']
            case = next(case for case in load_cases() if case['id'] == 'html-rich')
            source.write_text(source.read_text().replace('<body>', '<body><p>HTML_PREFIX_901</p>').replace('</body>', '<p>HTML_TAIL_902</p></body>'))
            case = dict(case, markers=['HTML_PREFIX_901', *case['markers'], 'HTML_TAIL_902'])
            result = convert(ConversionRequest(source, root/'data', ('markdown', 'html')))
            self.assertEqual(result.status, 'completed')
            for view in ('md_single', 'md_chapters', 'html'):
                paths = sorted((result.output_dir/'chapters').glob('*.md')) if view == 'md_chapters' else [result.output_dir/('book.html' if view == 'html' else 'book.md')]
                observation = inspect_output(paths, case, result.output_dir)
                self.assertTrue(observation['passed'], (view, observation['failures']))
                headings = []
                for path in paths:
                    rendered = path.read_text() if path.suffix == '.html' else markdown.markdown(path.read_text(), extensions=['tables', 'fenced_code'])
                    headings.extend(tag.get_text(strip=True) for tag in BeautifulSoup(rendered, 'html.parser').find_all(re.compile('^h[1-6]$')))
                self.assertEqual(headings.count('第一章：图表与代码'), 1, (view, headings))
                rendered = '\n'.join(path.read_text() if path.suffix == '.html' else markdown.markdown(path.read_text(), extensions=['tables', 'fenced_code']) for path in paths)
                soup = BeautifulSoup(rendered, 'html.parser')
                prefix = soup.find(string=lambda text: 'HTML_PREFIX_901' in text)
                heading = soup.find(['h1', 'h2'], string='第一章：图表与代码')
                positions = {id(element): index for index, element in enumerate(soup.descendants)}
                self.assertLess(positions[id(prefix)], positions[id(heading)], 'Source prefix must stay before its first chapter heading')
            snapshot = json.loads((result.output_dir/'book.json').read_text())
            self.assertEqual(snapshot['source_format'], 'html')
            self.assertEqual(len(snapshot['roots']), 2)
            self.assertEqual(len(list((result.output_dir/'chapters').glob('*.md'))), 2)
            self.assertIn('colspan="2"', (result.output_dir/'book.html').read_text())

    def test_source_snapshot_keeps_css_images_and_replays_after_originals_are_removed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = build_fixtures(root/'inputs')['html-rich']
            css = source.parent/'css'
            css.mkdir()
            (css/'main.css').write_text('@import "nested.css" screen; .external {background-image:url(../assets/blue.png);border-left:7px solid #c12345}')
            (css/'nested.css').write_text('body#source-body {color:#154c79} #first {padding-left:13px}')
            source.write_text(source.read_text().replace('</head>', '<link rel="stylesheet" href="css/main.css"></head>').replace('<body>', '<body id="source-body" class="external">'))
            runtime = JobRuntime(root/'data')
            job = runtime.file_job(source, ('markdown', 'html'))
            stored = runtime.source_path(job)
            self.assertTrue(stored.is_file())
            shutil.rmtree(source.parent)
            result = convert(ConversionRequest(data_dir=root/'data', job_id=job.job_id))
            self.assertEqual(result.status, 'completed')
            html = (result.output_dir/'book.html').read_text()
            self.assertIn('@media screen', html)
            for marker in ('#c12345', '#154c79', '13px'):
                self.assertIn(marker, html)
            restarted = JobRuntime(root/'data')
            retry = restarted.queue(job.job_id, 'html')
            again = convert(ConversionRequest(data_dir=root/'data', job_id=retry.job_id))
            self.assertEqual(result.revision_id, again.revision_id)
            self.assertEqual((result.output_dir/'book.html').read_bytes(), (again.output_dir/'book.html').read_bytes())

    def test_resource_changes_change_source_and_revision_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = build_fixtures(root/'inputs')['html-rich']
            first = convert(ConversionRequest(source, root/'data', ('html',)))
            (source.parent/'assets'/'red.png').write_bytes((source.parent/'assets'/'blue.png').read_bytes())
            second = convert(ConversionRequest(source, root/'data', ('html',)))
            self.assertNotEqual(first.revision_id, second.revision_id)
            old = json.loads((first.output_dir/'book.json').read_text())
            new = json.loads((second.output_dir/'book.json').read_text())
            self.assertNotEqual(old['source_sha256'], new['source_sha256'])

    def test_plain_html_is_one_document_and_does_not_apply_epub_number_heuristics(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root/'plain.html'
            source.write_text('<html lang="zh"><head><title>普通文档</title><style>p{font-family:monospace}</style></head><body><p>1 正文第一项</p><p>2 正文第二项</p><pre><code>  # literal_code\n</code></pre></body></html>')
            result = convert(ConversionRequest(source, root/'data', ('markdown', 'html')))
            snapshot = json.loads((result.output_dir/'book.json').read_text())
            self.assertEqual(len(snapshot['nodes']), 1)
            html = BeautifulSoup((result.output_dir/'book.html').read_text(), 'html.parser')
            self.assertIn('1 正文第一项', html.get_text())
            self.assertIn('2 正文第二项', html.get_text())
            self.assertEqual(html.pre.get_text(), '  # literal_code\n')

    def test_web_bundle_and_cli_share_the_same_conversion_and_portable_download(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = build_fixtures(root/'inputs')['html-rich']
            case = next(case for case in load_cases() if case['id'] == 'html-rich')
            bundle = root/'article.zip'
            _archive(bundle, {'rich.html': source.read_bytes(), 'assets/red.png': (source.parent/'assets'/'red.png').read_bytes(), 'assets/blue.png': (source.parent/'assets'/'blue.png').read_bytes()})
            with patch.object(settings, 'DATA_DIR', str(root/'data')), TestClient(app) as client:
                revisions = set()
                views = ('md_single', 'md_chapters', 'html', *(('pdf',) if os.environ.get('ANY_TO_MD_TEST_PDF') == '1' else ()))
                for view in views:
                    uploaded = client.post('/api/upload', files={'file': (bundle.name, bundle.read_bytes(), 'application/zip')})
                    self.assertEqual(uploaded.status_code, 200, uploaded.text)
                    self.assertEqual(uploaded.json()['input_format'], 'html')
                    self.assertIn(view, uploaded.json()['available_outputs'])
                    task = uploaded.json()['task_id']
                    response = client.post('/api/convert', json={'task_id': task, 'output_format': view})
                    self.assertEqual(response.status_code, 200, response.text)
                    state = client.get(f'/api/status/{task}').json()
                    self.assertEqual(state['status'], 'completed', state)
                    download = client.get(state['download_url'])
                    self.assertEqual(download.status_code, 200)
                    target = root/'downloaded'/view
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
                    job = JobRuntime(root/'data').get(task)
                    record = json.loads((root/'data'/job.storage_key/'book.json').read_text())
                    revisions.add(record['revision_id'])
                self.assertEqual(len(revisions), 1)
            completed = self._cli(root, ['--input', str(bundle)], root/'cli-data')
            self.assertEqual(len(completed), 1)
            snapshot = json.loads((completed[0]/'book.json').read_text())
            self.assertEqual({snapshot['revision_id']}, revisions)
            for paths in ([completed[0]/'book.html'], [completed[0]/'book.md'], sorted((completed[0]/'chapters').glob('*.md'))):
                check = inspect_output(paths, case, completed[0])
                self.assertTrue(check['passed'], check['failures'])

    def _cli(self, cwd, arguments, data_dir):
        installed = Path(any_to_md.__file__).is_relative_to(Path(sys.prefix))
        command = [sys.executable, *(['-I'] if installed else []), '-m', 'any_to_md.interfaces.cli.convert_book']
        environment = dict(os.environ)
        if not installed:
            environment['PYTHONPATH'] = str(Path(__file__).resolve().parents[2]/'src')
        result = subprocess.run([*command, *arguments, '--data-dir', str(data_dir), '--target', 'markdown', 'html'], cwd=cwd, env=environment, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
        return list((data_dir/'exports').iterdir())

    def test_html_single_multiple_and_directory_cli_preserve_source_revision(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = build_fixtures(root/'fixtures')['html-rich']
            batch = root/'batch'
            batch.mkdir()
            source = batch/'rich.html'
            source.write_bytes(fixture.read_bytes())
            shutil.copytree(fixture.parent/'assets', batch/'assets')
            plain = batch/'plain.htm'
            plain.write_text('<title>普通输入</title><p>CLI_PLAIN_917</p>')
            for arguments in (['--input', str(source)], ['--input', str(source), str(plain)], ['--input-dir', str(batch)]):
                completed = self._cli(root, arguments, root/'data')
            self.assertEqual(len(completed), 5)
            snapshots = [(path, json.loads((path/'book.json').read_text())) for path in completed]
            rich = [(path, record) for path, record in snapshots if record['metadata']['title'] != '普通输入']
            self.assertEqual(len(rich), 3)
            self.assertEqual(len({record['revision_id'] for _, record in rich}), 1)
            case = next(case for case in load_cases() if case['id'] == 'html-rich')
            for path, _ in rich:
                for paths in ([path/'book.html'], [path/'book.md'], sorted((path/'chapters').glob('*.md'))):
                    check = inspect_output(paths, case, path)
                    self.assertTrue(check['passed'], check['failures'])

    @unittest.skipUnless(os.environ.get('ANY_TO_MD_TEST_PDF') == '1', '需要显式启用原生 PDF 渲染环境')
    def test_html_pdf_uses_shared_html_and_preserves_text_and_images(self):
        import pymupdf

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = build_fixtures(root/'inputs')['html-rich']
            case = next(case for case in load_cases() if case['id'] == 'html-rich')
            source.write_text(source.read_text().replace('<body>', '<body><p>HTML_PREFIX_901</p>'))
            case = dict(case, markers=['HTML_PREFIX_901', *case['markers']])
            result = convert(ConversionRequest(source, root/'data', ('markdown', 'html', 'pdf')))
            observation = inspect_pdf(result.output_dir/'book.pdf', case)
            self.assertTrue(observation['passed'], observation['failures'])
            with pymupdf.open(result.output_dir/'book.pdf') as document:
                text = ''.join(''.join(page.get_text() for page in document).split())
            self.assertLess(text.index('HTML_PREFIX_901'), text.index('第一章：图表与代码'))
            self.assertLess(text.index('第一章：图表与代码'), text.index('BODY_FIRST_101'))


if __name__ == '__main__':
    unittest.main()
