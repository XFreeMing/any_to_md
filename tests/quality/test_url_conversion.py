"""URL source expectations, redirect bases, replay and shared entry contracts."""

import io
import gzip
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile

import httpx
import markdown
from bs4 import BeautifulSoup
from fastapi.testclient import TestClient
from PIL import Image

import any_to_md
from any_to_md.adapters.url_source import article_document, capture_url_source, read_url_source
from any_to_md.application.convert import ConversionRequest, convert
from any_to_md.application.jobs import ConversionFailure, JobRuntime
from tests.quality.url_fixtures import CODE, HTML, MARKERS, TITLE
from web.app import app
from web.config import settings


def png():
    stream = io.BytesIO()
    Image.new('RGB', (3, 2), (18, 90, 164)).save(stream, format='PNG')
    return stream.getvalue()


def response(request):
    path = request.url.path
    if path == '/start':
        return httpx.Response(302, headers={'location': '/article/index.html'})
    if path == '/article/index.html':
        return httpx.Response(200, text=HTML, headers={'content-type': 'text/html; charset=utf-8'})
    if path == '/article/theme.css':
        return httpx.Response(302, headers={'location': '/styles/site.css'})
    if path == '/styles/site.css':
        return httpx.Response(200, text='@import "extra.css"; article {color:#123456; background-image:url(bg.png); background-repeat:no-repeat; background-position:right bottom}', headers={'content-type': 'text/css'})
    if path == '/styles/extra.css':
        return httpx.Response(200, text='pre {white-space:pre-wrap}', headers={'content-type': 'text/css'})
    if path in {'/article/picture.png', '/article/picture2.png', '/styles/bg.png'}:
        return httpx.Response(200, content=png(), headers={'content-type': 'image/png'})
    return httpx.Response(404)


class UrlConversionTests(unittest.TestCase):
    def test_nested_sidebar_removal_does_not_crash(self):
        document, _ = article_document('<main><p>Body</p><aside><aside role="doc-footnote">Sidebar note</aside></aside></main>', 'https://fixture.test/')
        self.assertIn('Body', document.get_text())
        self.assertNotIn('Sidebar note', document.get_text())

    def test_decorative_permalink_does_not_duplicate_source_heading(self):
        text = '<title>Document title</title><main><h1 id="heading">Article heading<a class="headerlink" href="#heading">¶</a></h1><p>Body</p></main>'
        with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, text=text, headers={'content-type': 'text/html'}))) as client:
            snapshot = capture_url_source('https://fixture.test/', client=client)
        with tempfile.TemporaryDirectory() as directory:
            with patch('any_to_md.application.jobs.capture_url_source', return_value=snapshot):
                result = convert(ConversionRequest('https://fixture.test/', directory, ('html',)))
            html = BeautifulSoup((result.output_dir/'book.html').read_text(), 'html.parser')
            self.assertEqual(html.get_text().count('Article heading'), 1)

    @unittest.skipUnless(os.environ.get('ANY_TO_MD_TEST_PDF') == '1', 'Native PDF environment not enabled')
    def test_article_shell_paginates_without_blank_pages(self):
        body = ''.join(f'<p>FLOW_{index:03d}: '+('This article paragraph must remain readable across printed pages. '*5)+'</p>' for index in range(30))
        text = '<style>.shell {display:flex} .wrapper {float:left;width:100%}</style><div class="shell"><div class="wrapper"><main><h1>Long article</h1>'+body+'</main></div></div>'
        with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, text=text, headers={'content-type': 'text/html'}))) as client:
            snapshot = capture_url_source('https://fixture.test/', client=client)
        with tempfile.TemporaryDirectory() as directory:
            with patch('any_to_md.application.jobs.capture_url_source', return_value=snapshot):
                result = convert(ConversionRequest('https://fixture.test/', directory, ('pdf',)))
            import pymupdf
            with pymupdf.open(result.output_dir/'book.pdf') as pdf:
                texts = [page.get_text() for page in pdf]
            self.assertLess(len(texts), 10)
            self.assertTrue(all(text.strip() for text in texts))
            for index in range(30):
                self.assertEqual(''.join(texts).count(f'FLOW_{index:03d}'), 1)

    def test_context_encoding_external_source_and_unused_links(self):
        content = '''<html><head><title>中文编码</title><link rel="alternate" href="/feed"></head>
        <body class="dark" xml:base="/assets/"><div class="shell"><main><h1>正文</h1>
        <p>编码正文</p><img src="photo.png"></main></div></body></html>'''.encode('gbk')
        seen = []
        def handler(request):
            seen.append(str(request.url))
            return httpx.Response(200, content=content if request.url.path == '/' else png(), headers={'content-type': 'text/html; charset=gbk' if request.url.path == '/' else 'image/png'})
        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            snapshot = capture_url_source('https://fixture.test/', client=client)
        self.assertEqual(seen, ['https://fixture.test/', 'https://fixture.test/assets/photo.png'])
        with tempfile.TemporaryDirectory() as directory:
            with patch('any_to_md.application.jobs.capture_url_source', return_value=snapshot):
                result = convert(ConversionRequest('https://fixture.test/', directory, ('html',)))
            self.assertEqual(result.status, 'completed')
            soup = BeautifulSoup((result.output_dir/'book.html').read_text(), 'html.parser')
            self.assertIn('编码正文', soup.get_text())
            self.assertTrue(soup.select('.dark .shell main'))
            self.assertEqual(soup.find('a', string='https://fixture.test/')['href'], 'https://fixture.test/')

    def test_compressed_response_is_bounded_before_expansion(self):
        data = gzip.compress(b'A' * (1024*1024))
        def handler(request):
            return httpx.Response(200, stream=httpx.ByteStream(data), headers={'content-encoding': 'gzip', 'content-type': 'text/html'})
        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            with patch('any_to_md.adapters.url_source.MAX_RESPONSE_BYTES', 8192):
                with self.assertRaises(ValueError):
                    capture_url_source('https://fixture.test/', client=client)

    def test_invalid_gzip_resource_records_partial_failure(self):
        def handler(request):
            if request.url.path == '/':
                return httpx.Response(200, text='<main><p>正文</p><img src="broken.png"></main>', headers={'content-type': 'text/html'})
            return httpx.Response(200, stream=httpx.ByteStream(b'invalid'), headers={'content-encoding': 'gzip'})
        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            snapshot = capture_url_source('https://fixture.test/', client=client)
        with tempfile.TemporaryDirectory() as directory:
            with patch('any_to_md.application.jobs.capture_url_source', return_value=snapshot):
                result = convert(ConversionRequest('https://fixture.test/', directory))
            self.assertEqual(result.status, 'partial')

    def test_footnotes_and_unmarked_page_boundary(self):
        for marked in (True, False):
            body = '<h1>脚注正文</h1><p>保留正文<a href="#note">注</a></p><aside role="doc-footnote" id="note">保留脚注</aside>'
            text = '<head><meta name="author" content="甲"><meta name="author" content="乙"></head>' + ('<main>'+body+'</main>' if marked else body)
            with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, text=text, headers={'content-type': 'text/html'}))) as client:
                snapshot = capture_url_source('https://fixture.test/', client=client)
            with tempfile.TemporaryDirectory() as directory:
                with patch('any_to_md.application.jobs.capture_url_source', return_value=snapshot):
                    result = convert(ConversionRequest('https://fixture.test/', directory, ('html',)))
                html = BeautifulSoup((result.output_dir/'book.html').read_text(), 'html.parser')
                self.assertIn('保留脚注', html.get_text())
                target = html.find('a', string='注')['href'][1:]
                self.assertTrue(html.find(id=target))
                codes = {item['code'] for item in json.loads(result.report_path.read_text())['diagnostics']}
                self.assertNotIn('duplicate_source_anchor', codes)
                self.assertEqual('unmarked_article' in codes, not marked)

    def test_capture_interruption_is_recoverable_and_retains_url_original(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = JobRuntime(directory)
            job = runtime.file_job('https://fixture.test/', ('html',))
            original = runtime.source_path(job)
            with patch('any_to_md.application.jobs.capture_url_source', side_effect=KeyboardInterrupt):
                with self.assertRaises(KeyboardInterrupt):
                    convert(ConversionRequest(data_dir=directory, job_id=job.job_id))
            self.assertEqual(runtime.get(job.job_id).status.value, 'failed')
            self.assertTrue(original.is_file())

    def capture(self, url='https://fixture.test/start'):
        with httpx.Client(transport=httpx.MockTransport(response), follow_redirects=True) as client:
            return capture_url_source(url, client=client)

    def check_export(self, root):
        book = json.loads((root/'book.json').read_text())
        self.assertEqual(book['source_format'], 'url')
        self.assertEqual(book['metadata']['title'], TITLE)
        self.assertEqual(book['metadata']['identifier'], 'https://fixture.test/start')
        for name in ('book.html', 'book.md'):
            text = (root/name).read_text()
            rendered = markdown.markdown(text, extensions=['tables', 'fenced_code']) if name.endswith('.md') else text
            soup = BeautifulSoup(rendered, 'html.parser')
            content = soup.get_text(' ', strip=True)
            for marker in MARKERS:
                self.assertEqual(content.count(marker), 1, (name, marker))
            self.assertEqual(sorted(MARKERS, key=content.index), list(MARKERS))
            for excluded in ('NAV_EXCLUDED_954', 'AD_EXCLUDED_955', 'FOOTER_EXCLUDED_956'):
                self.assertNotIn(excluded, content)
            self.assertEqual(soup.find('td', rowspan='2').get_text(), 'URL_SPAN_952')
            self.assertEqual(soup.pre.get_text().strip('\n'), CODE)
            for image in soup.find_all('img'):
                with Image.open(root/image['src']) as decoded:
                    self.assertEqual(decoded.size, (3, 2))
                    self.assertEqual(decoded.convert('RGB').getpixel((0, 0)), (18, 90, 164))
            self.assertTrue(soup.find('img')['srcset'])
            start = soup.find('a', string='正文起点')
            self.assertTrue(soup.find(id=start['href'][1:]))
            self.assertEqual(soup.find('a', string='外部链接')['href'], 'https://fixture.test/reference?q=1')
        self.assertEqual(len(book['assets']), 1)  # Equal pixels/bytes deduplicate.

    def test_snapshot_replay_all_writers_and_moved_export(self):
        snapshot = self.capture()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = JobRuntime(root/'data')
            job = runtime.upload('webpage.url', b'https://fixture.test/start', 'url')
            original = runtime.source_path(job)
            targets = ('markdown', 'html', 'pdf') if os.environ.get('ANY_TO_MD_TEST_PDF') == '1' else ('markdown', 'html')
            # Register a queued multi-target job through file ingestion as the CLI does.
            with patch('any_to_md.application.jobs.capture_url_source', return_value=snapshot):
                queued = runtime.file_job('https://fixture.test/start', targets)
            with patch('any_to_md.application.jobs.capture_url_source', return_value=snapshot) as fetch:
                result = convert(ConversionRequest(data_dir=root/'data', job_id=queued.job_id))
            fetch.assert_called_once()
            self.assertTrue(original.is_file())
            source = read_url_source(runtime.source_path(runtime.get(queued.job_id)))
            self.assertEqual(source.members['response.html'], HTML.encode())
            self.assertEqual(source.manifest['final_url'], 'https://fixture.test/article/index.html')
            self.assertIn('https://fixture.test/styles/extra.css', source.manifest['resources'])
            self.assertEqual(result.status, 'completed')
            self.check_export(result.output_dir)
            moved = root/'moved'
            shutil.copytree(result.output_dir, moved)
            self.check_export(moved)
            if 'pdf' in targets:
                import pymupdf
                with pymupdf.open(moved/'book.pdf') as pdf:
                    text = ''.join(page.get_text() for page in pdf)
                for marker in MARKERS:
                    self.assertEqual(text.count(marker), 1)
            self.assertTrue(runtime.download(queued.job_id).is_file())
            # A terminal Web job creates a new export from the same frozen source.
            queued = runtime.queue(queued.job_id, 'html')
            with patch('httpx.Client', side_effect=AssertionError('replay accessed network')):
                replay = convert(ConversionRequest(data_dir=root/'data', job_id=queued.job_id))
            self.assertEqual(result.revision_id, replay.revision_id)

    def test_failed_resources_remain_partial_and_http_failure_is_durable(self):
        with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200, text=HTML, headers={'content-type': 'text/html'}) if request.url.path == '/' else httpx.Response(404))) as client:
            snapshot = capture_url_source('https://fixture.test/', client=client)
        with tempfile.TemporaryDirectory() as directory:
            with patch('any_to_md.application.jobs.capture_url_source', return_value=snapshot):
                result = convert(ConversionRequest('https://fixture.test/', directory))
            self.assertEqual(result.status, 'partial')
            report = json.loads(result.report_path.read_text())
            self.assertIn('url_resource_failed', {item['code'] for item in report['diagnostics']})
            with patch('any_to_md.application.jobs.capture_url_source', side_effect=httpx.ConnectError('offline')):
                with self.assertRaises(ConversionFailure) as failed:
                    convert(ConversionRequest('https://fixture.test/', directory))
            self.assertTrue(failed.exception.report_path.is_file())

    def test_capture_bounds_invalid_schemes_and_base_url(self):
        with httpx.Client(transport=httpx.MockTransport(response), follow_redirects=True) as client:
            for value in ('file:///etc/passwd', 'https://user:secret@fixture.test/'):
                with self.assertRaises(ValueError):
                    capture_url_source(value, client=client)
            with patch('any_to_md.adapters.url_source.MAX_RESPONSE_BYTES', 8):
                with self.assertRaises(ValueError):
                    capture_url_source('https://fixture.test/start', client=client)
        seen = []
        def base_response(request):
            seen.append(str(request.url))
            if request.url.path == '/':
                return httpx.Response(200, text='<base href="/assets/"><main><p>正文</p><img src="x.png"></main>', headers={'content-type': 'text/html'})
            return httpx.Response(200, content=png(), headers={'content-type': 'image/png'})
        with httpx.Client(transport=httpx.MockTransport(base_response)) as client:
            capture_url_source('https://fixture.test/', client=client)
        self.assertIn('https://fixture.test/assets/x.png', seen)

    def test_http_and_cli_batch_share_real_capture(self):
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                result = response(httpx.Request('GET', 'http://local'+self.path))
                self.send_response(result.status_code)
                for key, value in result.headers.items():
                    self.send_header(key, value)
                self.end_headers()
                self.wfile.write(result.content)

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                url = f'http://127.0.0.1:{server.server_port}/start'
                with patch.object(settings, 'DATA_DIR', root/'web'), TestClient(app) as client:
                    fetched = client.post('/api/fetch', json={'url': url})
                    self.assertEqual(fetched.status_code, 200, fetched.text)
                    task = fetched.json()['task_id']
                    for output in ('md_single', 'html', *(('pdf',) if os.environ.get('ANY_TO_MD_TEST_PDF') == '1' else ())):
                        result = client.post('/api/convert', json={'task_id': task, 'output_format': output})
                        self.assertEqual(result.status_code, 200, result.text)
                        task = result.json()['task_id']
                        status = client.get('/api/status/'+task).json()
                        self.assertEqual(status['status'], 'completed', status)
                        self.assertEqual(client.get(status['download_url']).status_code, 200)
                environment = os.environ.copy()
                environment['PYTHONPATH'] = str(Path(any_to_md.__file__).resolve().parents[1])
                urls = root/'inputs'; urls.mkdir()
                (urls/'one.url').write_text(url)
                for arguments in (['--url', url, url], ['--input-dir', str(urls)]):
                    result = subprocess.run([sys.executable, '-m', 'any_to_md.interfaces.cli.convert_book', *arguments, '--data-dir', str(root/'cli'), '--target', 'markdown', 'html'], cwd=root, env=environment, text=True, capture_output=True, timeout=60)
                    self.assertEqual(result.returncode, 0, result.stdout+result.stderr)
                    self.assertIn('状态：completed', result.stdout)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


if __name__ == '__main__':
    unittest.main()
