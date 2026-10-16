"""Upstream MD fixtures enter the same DOM, resources and shared writers."""

import io
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
import uuid

from bs4 import BeautifulSoup
import markdown
from PIL import Image

from any_to_md.application.jobs import source_ids
from any_to_md.content.markdown import import_markdown
from any_to_md.content.revision import populate_html_revision
from any_to_md.domain.book import BookMetadata, BookRevision, Diagnostic
from any_to_md.export.bundle import write_bundle
from any_to_md.export.planning import create_export_plan
from web.services.converter import ConversionService

CODE = 'if value < 3:\n    print("中文")'
SOURCE = '''# Imported article

<a id="source-start"></a>

MD_BEGIN_990 中文正文。注释[^note]。

```python
if value < 3:
    print("中文")
```

<table><tr><th>项目</th><th>值</th></tr><tr><td rowspan="2">MD_SPAN_991</td><td>第一行</td></tr><tr><td>第二行</td></tr></table>

![原图像素](images/chart.png)

[返回正文](#source-start) [外部资料](https://example.com/reference)

MD_END_992

[^note]: MD_NOTE_993 脚注正文。

<script>alert('active')</script>
<iframe src="https://example.com/frame"></iframe>
<p onclick="alert('active')">MD_SAFE_994</p>
<a href="javascript:alert('active')">禁止脚本链接</a>
'''


class MarkdownImportTests(unittest.TestCase):
    def test_imported_body_assets_notes_code_and_spans_survive_shared_views(self):
        stream = io.BytesIO()
        Image.new('RGB', (4, 3), (36, 81, 169)).save(stream, format='PNG')
        picture = stream.getvalue()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_id, book_id, edition_id = source_ids('image', 'a'*64)
            book = BookRevision(book_id, edition_id, str(uuid.uuid4()), source_id, root/'source.png', 'a'*64,
                                BookMetadata('OCR result'), [], {}, [], {},
                                [Diagnostic('ocr_markdown_only', '上游只有 Markdown，原版面不可恢复。')],
                                'markdown-import-test-v1', source_format='image')
            calls = []
            def resource(path):
                calls.append(path)
                return (picture, 'image/png') if path == 'ocr/images/chart.png' else None
            populate_html_revision(book, {'ocr/result.md': import_markdown(SOURCE)}, resource)
            self.assertIn('ocr/images/chart.png', calls)
            self.assertEqual(len(book.assets), 1)
            targets = ('markdown', 'html', 'pdf') if os.environ.get('ANY_TO_MD_TEST_PDF') == '1' else ('markdown', 'html')
            plan = create_export_plan(book); plan.targets = targets
            report, _ = write_bundle(book, plan, root/'export', targets, job_id='md-import-test')
            self.assertEqual(report['status'], 'completed_with_warnings')
            self.assertEqual(report['diagnostics'][0]['code'], 'ocr_markdown_only')
            for name in ('book.md', 'book.html'):
                text = (root/'export'/name).read_text()
                if name.endswith('.md'):
                    text = markdown.markdown(text, extensions=['tables', 'fenced_code'])
                soup = BeautifulSoup(text, 'html.parser')
                body = soup.get_text(' ', strip=True)
                for marker in ('MD_BEGIN_990', 'MD_SPAN_991', 'MD_END_992', 'MD_NOTE_993', 'MD_SAFE_994'):
                    self.assertEqual(body.count(marker), 1, (name, marker))
                self.assertEqual(soup.pre.get_text().strip('\n'), CODE)
                self.assertEqual(soup.find('td', rowspan='2').get_text(), 'MD_SPAN_991')
                self.assertFalse(soup.find(['script', 'iframe']))
                self.assertFalse(soup.select('[onclick]'))
                for link in soup.find_all('a', href=True):
                    self.assertFalse(link['href'].lower().startswith('javascript:'))
                    if link['href'].startswith('#'):
                        self.assertTrue(soup.find(id=link['href'][1:]), (name, link['href']))
                with Image.open(root/'export'/soup.find('img')['src']) as image:
                    self.assertEqual(image.size, (4, 3))
                    self.assertEqual(image.convert('RGB').getpixel((0, 0)), (36, 81, 169))
            if 'pdf' in targets:
                import pymupdf
                with pymupdf.open(root/'export/book.pdf') as pdf:
                    text = ''.join(page.get_text() for page in pdf)
                for marker in ('MD_BEGIN_990', 'MD_SPAN_991', 'MD_END_992', 'MD_NOTE_993'):
                    self.assertEqual(text.count(marker), 1)

    def test_existing_web_markdown_path_uses_clean_dom_and_shared_page_shell(self):
        service = ConversionService(SimpleNamespace(filename='<Source & title>'), None)
        soup = BeautifulSoup(service._markdown_to_html(SOURCE), 'html.parser')
        self.assertEqual(soup.title.get_text(), '<Source & title>')
        self.assertFalse(soup.find(['script', 'iframe']))
        self.assertFalse(soup.select('[onclick]'))
        self.assertFalse(soup.find('a', href=lambda value: value and value.startswith('javascript:')))
        self.assertEqual(soup.pre.get_text().strip('\n'), CODE)
        self.assertTrue(soup.find('a', href='#fn:note'))
        self.assertTrue(soup.find(id='fn:note'))


if __name__ == '__main__':
    unittest.main()
