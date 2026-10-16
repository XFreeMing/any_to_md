"""EPUB HTTP and CLI/batch entries must use the canonical application."""

import io
import hashlib
import json
import os
import subprocess
import sqlite3
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile

from fastapi.testclient import TestClient
import any_to_md

from tests.quality.fixtures import build_fixtures, load_cases
from tests.quality.inspection import inspect_output, inspect_pdf
from web.app import app
from web.config import settings


class EpubEntryTests(unittest.TestCase):
    @unittest.skipUnless(os.environ.get("ANY_TO_MD_TEST_PDF") == "1", "需要显式启用原生 PDF 渲染环境")
    def test_http_pdf_download_preserves_source_body_and_images(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = build_fixtures(root / "inputs")["epub-structured"]
            case = next(case for case in load_cases() if case["id"] == "epub-structured")
            with patch.object(settings, "DATA_DIR", str(root / "catalog")), TestClient(app) as client:
                uploaded = client.post("/api/upload", files={"file": (source.name, source.read_bytes(), "application/epub+zip")})
                self.assertEqual(uploaded.status_code, 200, uploaded.text)
                task_id = uploaded.json()["task_id"]
                response = client.post("/api/convert", json={"task_id": task_id, "output_format": "pdf"})
                self.assertEqual(response.status_code, 200, response.text)
                status = client.get(f"/api/status/{task_id}").json()
                self.assertIn(status["status"], ("completed", "completed_with_warnings"), status)
                downloaded = client.get(status["download_url"])
                self.assertEqual(downloaded.status_code, 200)
                self.assertTrue(downloaded.content.startswith(b"%PDF-"))
                target = root / "book.pdf"
                target.write_bytes(downloaded.content)
                inspection = inspect_pdf(target, case)
                self.assertTrue(inspection["passed"], inspection["failures"])

    def test_http_upload_conversion_and_portable_download(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = build_fixtures(root / "inputs")["epub-structured"]
            case = next(case for case in load_cases() if case["id"] == "epub-structured")
            with patch.object(settings, "DATA_DIR", str(root / "catalog")), TestClient(app) as client:
                for target in ("md_single", "md_chapters", "html"):
                    response = client.post("/api/upload", files={"file": (source.name, source.read_bytes(), "application/epub+zip")})
                    self.assertEqual(response.status_code, 200, response.text)
                    task_id = response.json()["task_id"]
                    response = client.post("/api/convert", json={"task_id": task_id, "output_format": target})
                    self.assertEqual(response.status_code, 200, response.text)
                    status = client.get(f"/api/status/{task_id}").json()
                    self.assertIn(status["status"], ("completed", "completed_with_warnings"), status)
                    download = client.get(status["download_url"])
                    self.assertEqual(download.status_code, 200, download.text[:200])
                    export = root / "downloaded" / target
                    export.mkdir(parents=True)
                    with ZipFile(io.BytesIO(download.content)) as archive:
                        archive.extractall(export)
                    paths = sorted((export / "chapters").glob("*.md")) if target == "md_chapters" else [export / ("book.html" if target == "html" else "book.md")]
                    result = inspect_output(paths, case, export)
                    self.assertTrue(result["passed"], (target, result["failures"]))
                with closing(sqlite3.connect(root / "catalog" / "catalog.sqlite")) as connection:
                    self.assertEqual(connection.execute("SELECT count(*) FROM sources").fetchone()[0], 1)
                    self.assertEqual(connection.execute("SELECT count(*) FROM revisions").fetchone()[0], 1)

    def test_single_and_batch_cli_use_the_same_source_revision(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sources = build_fixtures(root / "inputs")
            project = Path(__file__).resolve().parents[2]
            installed = Path(any_to_md.__file__).is_relative_to(Path(sys.prefix))
            command = [sys.executable, *(["-I"] if installed else []), "-m", "any_to_md.interfaces.cli.convert_book"]
            environment = dict(os.environ) if installed else dict(os.environ, PYTHONPATH=str(project / "src"))
            for arguments in (
                ["--input", str(sources["epub-plain"])],
                ["--input", str(sources["epub-plain"]), str(sources["epub-no-toc"])],
                ["--input-dir", str(root / "inputs")],
            ):
                result = subprocess.run([*command, *arguments, "--data-dir", str(root / "data"), "--target", "markdown", "html"], cwd=root, env=environment, capture_output=True, text=True, timeout=30)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            exports = list((root / "data" / "exports").iterdir())
            self.assertEqual(len(exports), 11)  # Five EPUBs, two HTMLs and one DOCX in the shared batch.
            snapshots = [json.loads((export / "book.json").read_text()) for export in exports]
            source_hash = hashlib.sha256(sources["epub-plain"].read_bytes()).hexdigest()
            plain = [snapshot for snapshot in snapshots if snapshot["source_sha256"] == source_hash]
            self.assertEqual(len(plain), 3)
            self.assertEqual(len({snapshot["revision_id"] for snapshot in plain}), 1)


if __name__ == "__main__":
    unittest.main()
