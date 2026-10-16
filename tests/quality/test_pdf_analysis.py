"""Page facts use independently authored visible/native PDF content."""

import asyncio
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pymupdf
from PIL import Image, ImageDraw

from any_to_md.readers.pdf.analysis import PdfInputError, analyze_pdf
from pdf_to_md.detector import get_pdf_info, is_scanned_pdf
from pdf_to_md.converter import convert_pdf_to_markdown, convert_pdf_to_markdown_sync
from any_to_md.application.jobs import JobRuntime
from web.services.converter import ConversionService


def image_bytes(text='SCANNED_VISIBLE_970'):
    image = Image.new('RGB', (400, 100), 'white')
    if text:
        ImageDraw.Draw(image).text((10, 20), text, fill='black', font_size=20)
    output = io.BytesIO()
    image.save(output, format='PNG')
    return output.getvalue()


class PdfAnalysisTests(unittest.TestCase):
    def test_actual_native_web_outputs_remain_available_without_ocr(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root/'native.pdf'
            with pymupdf.open() as document:
                page = document.new_page()
                page.insert_text((40, 60), 'NATIVE_WEB_983 This source stays on native extraction.')
                page.draw_rect(pymupdf.Rect(30, 40, 400, 90), color=(0, 0, 0))
                page.insert_image(pymupdf.Rect(400, 40, 440, 50), stream=image_bytes())
                document.save(source)
            self.assertEqual(analyze_pdf(source).pages[0].kind, 'mixed')
            self.assertFalse(analyze_pdf(source).needs_ocr)
            runtime = JobRuntime(root/'data')
            uploaded = runtime.upload(source.name, source.read_bytes())
            outputs = ('md_single', 'html', 'pdf') if os.environ.get('ANY_TO_MD_TEST_PDF') == '1' else ('md_single', 'html')
            with patch('ocr_to_md.convert_with_ocr', new_callable=AsyncMock) as provider:
                for output in outputs:
                    job = runtime.queue(uploaded.job_id, output)
                    path = asyncio.run(ConversionService(job, runtime).convert(output))
                    self.assertTrue(path.is_file())
                    state = runtime.get(job.job_id)
                    self.assertEqual(state.status.value, 'completed')
                    exported = runtime.storage.resolve(state.storage_key)
                    view = json.loads((exported/'manifest.json').read_text())['views'][output]
                    if output == 'pdf':
                        with pymupdf.open(exported/view) as pdf:
                            text = ''.join(page.get_text() for page in pdf)
                    else:
                        text = (exported/view).read_text()
                    self.assertEqual(text.count('NATIVE_WEB_983'), 1)
                provider.assert_not_called()

    def test_rotated_native_raster_pages_remain_mixed_without_forcing_ocr(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'rotated.pdf'
            with pymupdf.open() as document:
                for rotation in (90, 270):
                    page = document.new_page(width=600, height=800)
                    page.insert_text((20, 30), 'NATIVE_HEADER_984')
                    page.insert_image(pymupdf.Rect(20, 650, 120, 750), stream=image_bytes())
                    page.set_rotation(rotation)
                document.save(path)
            analysis = analyze_pdf(path)
            self.assertEqual([page.kind for page in analysis.pages], ['mixed', 'mixed'])
            self.assertEqual([page.image_count for page in analysis.pages], [1, 1])
            self.assertFalse(analysis.needs_ocr)

    def test_all_pages_not_first_five_and_short_native_text(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'pages.pdf'
            with pymupdf.open() as document:
                for index in range(6):
                    page = document.new_page(width=400, height=400)
                    page.insert_text((20, 30), f'NATIVE_PAGE_{index+1}')
                    page.draw_rect(pymupdf.Rect(10, 10, 300, 100), color=(0, 0, 0))
                page = document.new_page(width=400, height=400)
                page.insert_image(pymupdf.Rect(0, 0, 400, 100), stream=image_bytes())
                page = document.new_page(width=400, height=400)
                page.insert_text((20, 160), 'NATIVE_MIXED_971')
                page.insert_image(pymupdf.Rect(0, 0, 400, 100), stream=image_bytes())
                document.new_page(width=400, height=400)
                page = document.new_page(width=400, height=400)
                page.insert_image(pymupdf.Rect(0, 0, 400, 100), stream=image_bytes(''))
                page = document.new_page(width=400, height=400)
                page.draw_rect(pymupdf.Rect(20, 20, 100, 100), color=(0, 0, 0))
                document.save(path)
            analysis = analyze_pdf(path)
            self.assertEqual([page.page_index for page in analysis.pages], list(range(11)))
            self.assertEqual([page.kind for page in analysis.pages], ['native']*6+['scanned', 'mixed', 'blank', 'blank', 'scanned'])
            self.assertTrue(analysis.needs_ocr)
            self.assertTrue(is_scanned_pdf(path))
            info = get_pdf_info(path)
            self.assertEqual(info['pages'], analysis.to_dict()['pages'])
            self.assertEqual(info['page_count'], 11)
            with self.assertRaises(ValueError):
                convert_pdf_to_markdown_sync(path, Path(directory)/'output')

    def test_invalid_encrypted_and_forced_ocr_never_hide_parse_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            damaged = root/'damaged.pdf'; damaged.write_bytes(b'%PDF broken')
            empty = root/'empty.pdf'; empty.write_bytes(b'')
            encrypted = root/'encrypted.pdf'
            with pymupdf.open() as document:
                document.new_page().insert_text((20, 30), 'SECRET')
                document.save(encrypted, encryption=pymupdf.PDF_ENCRYPT_AES_256, owner_pw='owner', user_pw='password')
            for source in (damaged, empty, encrypted):
                with self.subTest(source=source.name):
                    with self.assertRaises(PdfInputError):
                        analyze_pdf(source)
                    with patch('ocr_to_md.convert_with_ocr', new_callable=AsyncMock) as provider:
                        with self.assertRaises(PdfInputError):
                            asyncio.run(convert_pdf_to_markdown(source, root/'out', force_ocr=True))
                        provider.assert_not_called()

    def test_native_async_and_sync_share_extractor(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); source = root/'native.pdf'
            with pymupdf.open() as document:
                document.new_page().insert_text((20, 30), 'ONE_NATIVE_SOURCE_972')
                document.save(source)
            with patch('pymupdf4llm.to_markdown', return_value='ONE_NATIVE_SOURCE_972') as extractor:
                sync = convert_pdf_to_markdown_sync(source, root/'sync')
                asynchronous = asyncio.run(convert_pdf_to_markdown(source, root/'async'))
            self.assertEqual(sync.read_text(), asynchronous.read_text())
            self.assertEqual(extractor.call_count, 2)


if __name__ == '__main__':
    unittest.main()
