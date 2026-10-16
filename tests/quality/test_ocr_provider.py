"""Protocol tests cannot establish real OCR quality or page capability."""

import asyncio
import io
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile, ZipInfo

import httpx

from any_to_md.adapters.ocr import OcrConfig, parse_ocr_response, recognize
from any_to_md.adapters.archive import write_members
from ocr_to_md import convert_with_ocr


def archive(members):
    stream = io.BytesIO()
    with ZipFile(stream, 'w') as output:
        for name, value in members.items():
            output.writestr(name, value)
    return stream.getvalue()


class OcrProviderTests(unittest.TestCase):
    def test_transport_injection_resources_and_response_preservation(self):
        body = archive({'result.md': b'# BODY_980\n\n![image](images/picture.png)', 'images/picture.png': b'PIXELS_981'})
        seen = []
        def handler(request):
            seen.append(request)
            return httpx.Response(200, content=body)
        config = OcrConfig('http://ocr.test/api/v1/ocr/process', 30)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory);source = root/'page.png';source.write_bytes(b'UPLOAD_982')
            with httpx.Client(transport=httpx.MockTransport(handler)) as client:
                content = recognize(source, config, client=client)
            self.assertEqual(seen[0].method, 'POST')
            self.assertIn(b'UPLOAD_982', seen[0].content)
            self.assertIn('BODY_980', content.markdown)
            self.assertEqual(content.resources['images/picture.png'], b'PIXELS_981')
            self.assertEqual(content.raw_response, body)
            with patch('ocr_to_md.converter.recognize', return_value=content):
                markdown, images = asyncio.run(convert_with_ocr(source, root/'output', config.api_url, config.timeout))
            self.assertEqual(markdown.read_text(), content.markdown)
            self.assertTrue(images.is_dir())
            self.assertEqual((markdown.parent/'.ocr-response.zip').read_bytes(), body)

    def test_unsafe_duplicate_symlink_ambiguous_and_oversized_archives(self):
        unsafe = [archive({'result.md': 'body', '../escape.txt': 'bad'}),
                  archive({'result.md': 'body', '/absolute.txt': 'bad'}),
                  archive({'one.md': 'body', 'two.md': 'ambiguous'}),
                  archive({'result.md': '   '})]
        for mode in ('duplicate', 'symlink'):
            stream = io.BytesIO()
            with ZipFile(stream, 'w') as output:
                output.writestr('result.md', 'body')
                if mode == 'duplicate':
                    output.writestr('a/./file.txt', 'one')
                    output.writestr('a/file.txt', 'two')
                else:
                    info = ZipInfo('images/link');info.create_system = 3;info.external_attr = (stat.S_IFLNK | 0o777)<<16
                    output.writestr(info, '/outside')
            unsafe.append(stream.getvalue())
        for body in unsafe:
            with self.assertRaises(ValueError):
                parse_ocr_response(body)
        with patch('any_to_md.adapters.ocr.MAX_OCR_RESPONSE_BYTES', 8):
            with self.assertRaises(ValueError):
                parse_ocr_response(archive({'result.md': 'body'}))
        from any_to_md.adapters.archive import validate_archive
        with ZipFile(io.BytesIO(archive({'result.md': 'long body'}))) as source:
            with self.assertRaises(ValueError):
                validate_archive(source, max_bytes=4)

    def test_status_errors_and_preexisting_symlink_cannot_write_outside(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory);source = root/'page.png';source.write_bytes(b'input')
            with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(503))) as client:
                with self.assertRaises(httpx.HTTPStatusError):
                    recognize(source, OcrConfig('http://ocr.test/process', 1), client=client)
            out = root/'out';out.mkdir();outside = root/'outside';outside.mkdir()
            (out/'images').symlink_to(outside, target_is_directory=True)
            with self.assertRaises(ValueError):
                write_members(out, {'images/escape.txt': b'bad'})
            self.assertFalse((outside/'escape.txt').exists())
            with self.assertRaises(ValueError):
                write_members(out, {'a.png': b'first', 'A.png': b'second'})


if __name__ == '__main__':
    unittest.main()
