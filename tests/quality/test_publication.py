"""Published exports must remain immutable and agree with catalog checksums."""

import hashlib
import json
import sqlite3
import tempfile
import threading
import unittest
from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from any_to_md.application.convert import ConversionRequest, convert
from any_to_md.application.jobs import JobRuntime
from any_to_md.adapters import storage
from tests.quality.fixtures import build_fixtures


class PublicationTests(unittest.TestCase):
    def test_concurrent_exports_share_one_immutable_revision_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = build_fixtures(root / 'inputs')['epub-plain']
            JobRuntime(root / 'data')
            barrier = threading.Barrier(2, timeout=15)
            arrivals = []
            original = storage.os.rename
            def concurrent_rename(source_path, destination):
                if Path(source_path).parent.resolve() == (root / 'data' / 'revisions').resolve():
                    arrivals.append(True)
                    barrier.wait()
                return original(source_path, destination)
            with patch.object(storage.os, 'rename', concurrent_rename):
                with ThreadPoolExecutor(max_workers=2) as executor:
                    results = list(executor.map(lambda _: convert(ConversionRequest(source, root / 'data')), range(2)))
            self.assertEqual(len(arrivals), 2)
            self.assertEqual(results[0].revision_id, results[1].revision_id)
            runtime = JobRuntime(root / 'data')
            for result in results:
                self.assertTrue(runtime.download(result.job_id).is_file())
            self.assertEqual(len(list(runtime.storage.revisions_dir.iterdir())), 1)

    def test_repeated_conversion_preserves_previous_export_and_catalog_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = build_fixtures(root / "inputs")["epub-plain"]
            first = convert(ConversionRequest(source, root / "data"))
            previous = {path.relative_to(first.output_dir): path.read_bytes() for path in first.output_dir.rglob("*") if path.is_file()}
            second = convert(ConversionRequest(source, root / "data"))
            self.assertEqual(first.revision_id, second.revision_id)
            self.assertNotEqual(first.export_id, second.export_id)
            self.assertNotEqual(first.output_dir, second.output_dir)
            self.assertEqual(previous, {path.relative_to(first.output_dir): path.read_bytes() for path in first.output_dir.rglob("*") if path.is_file()})
            with closing(sqlite3.connect(root / "data" / "catalog.sqlite")) as connection:
                self.assertEqual(connection.execute("SELECT count(*) FROM sources").fetchone()[0], 1)
                self.assertEqual(connection.execute("SELECT count(*) FROM revisions").fetchone()[0], 1)
                self.assertEqual(connection.execute("SELECT count(*) FROM exports").fetchone()[0], 2)
                records = connection.execute("SELECT relative_path, sha256, size_bytes FROM artifacts WHERE export_id = ?", (second.export_id,)).fetchall()
            self.assertEqual({name for name, _, _ in records}, {str(path.relative_to(second.output_dir)) for path in second.output_dir.rglob("*") if path.is_file()})
            for name, digest, size in records:
                data = (second.output_dir / name).read_bytes()
                self.assertEqual(digest, hashlib.sha256(data).hexdigest())
                self.assertEqual(size, len(data))
            manifest = json.loads((second.output_dir / "manifest.json").read_text())
            self.assertEqual(
                {item["path"] for item in manifest["files"]},
                {str(path.relative_to(second.output_dir)) for path in second.output_dir.rglob("*") if path.is_file() and path.name != "manifest.json"},
            )
            for item in manifest["files"]:
                data = (second.output_dir / item["path"]).read_bytes()
                self.assertEqual(item["sha256"], hashlib.sha256(data).hexdigest())
                self.assertEqual(item["size_bytes"], len(data))


if __name__ == "__main__":
    unittest.main()
