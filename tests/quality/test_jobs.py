"""Jobs must survive interfaces/restarts and never publish failed work."""

import json
import io
import os
import sqlite3
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile

from fastapi.testclient import TestClient
from bs4 import BeautifulSoup
from markdown import markdown as render_markdown

from any_to_md.application.convert import ConversionRequest, convert
from any_to_md.application.jobs import JobRuntime
from any_to_md.adapters.persistence.sqlite import SQLiteCatalog
from any_to_md.domain.jobs import JobConflict, JobStatus
from tests.quality.fixtures import build_fixtures
from web.app import app
from web.config import settings


class DurableJobTests(unittest.TestCase):
    def test_reader_and_missing_file_failures_remain_queryable(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            damaged = root / 'bad.epub'
            damaged.write_bytes(b'not an EPUB')
            for source in (damaged, root / 'missing.epub'):
                with self.assertRaises(Exception):
                    convert(ConversionRequest(source, root / 'data'))
            runtime = JobRuntime(root / 'data')
            with closing(sqlite3.connect(root / 'data' / 'catalog.sqlite')) as connection:
                rows = connection.execute('SELECT job_id, status, report_key FROM jobs ORDER BY created_at').fetchall()
            self.assertEqual(len(rows), 2)
            for job_id, status, report_key in rows:
                self.assertEqual(status, 'failed')
                report = json.loads(runtime.storage.resolve(report_key).read_text())
                self.assertEqual(report['job_id'], job_id)
                self.assertEqual(report['status'], 'failed')
                with self.assertRaises(ValueError):
                    runtime.download(job_id)
            missing_job = next(row[0] for row in rows if runtime.get(row[0]).input_key is None)
            with self.assertRaises(ValueError):
                runtime.queue(missing_job, 'html')
            with closing(sqlite3.connect(root / 'data' / 'catalog.sqlite')) as connection:
                self.assertEqual(connection.execute('SELECT count(*) FROM jobs').fetchone()[0], 2)

    def test_queue_claim_is_atomic_and_active_job_cannot_be_queued_twice(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = build_fixtures(root / 'inputs')['epub-plain']
            runtime = JobRuntime(root / 'data')
            job = runtime.upload(source.name, source.read_bytes())
            def queue():
                try:
                    return runtime.queue(job.job_id, 'html').status
                except JobConflict:
                    return 'conflict'
            with ThreadPoolExecutor(max_workers=2) as executor:
                results = list(executor.map(lambda _: queue(), range(2)))
            self.assertCountEqual(results, [JobStatus.QUEUED, 'conflict'])

    def test_http_status_and_download_survive_restart_with_one_job_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = build_fixtures(root / 'inputs')['epub-plain']
            with patch.object(settings, 'DATA_DIR', str(root / 'data')):
                with TestClient(app) as client:
                    uploaded = client.post('/api/upload', files={'file': (source.name, source.read_bytes(), 'application/epub+zip')}).json()
                    job_id = uploaded['task_id']
                    response = client.post('/api/convert', json={'task_id': job_id, 'output_format': 'html'})
                    self.assertEqual(response.status_code, 200, response.text)
                    self.assertEqual(response.json()['task_id'], job_id)
                    status = client.get(f'/api/status/{job_id}').json()
                    original = client.get(status['download_url']).content
                with TestClient(app) as restarted:
                    status = restarted.get(f'/api/status/{job_id}').json()
                    self.assertIn(status['status'], ('completed', 'completed_with_warnings'))
                    self.assertEqual(restarted.get(status['download_url']).content, original)
                    retry = restarted.post('/api/convert', json={'task_id': job_id, 'output_format': 'md_single'}).json()['task_id']
                    self.assertNotEqual(retry, job_id)
                    self.assertEqual(restarted.get(f'/api/status/{job_id}').json()['download_url'], status['download_url'])
                with closing(sqlite3.connect(root / 'data' / 'catalog.sqlite')) as connection:
                    self.assertEqual(connection.execute('SELECT count(*) FROM jobs').fetchone()[0], 2)
                    self.assertEqual(connection.execute('SELECT count(DISTINCT job_id) FROM exports').fetchone()[0], 2)

    def test_publish_failure_retains_diagnostics_without_a_download(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = build_fixtures(root / 'inputs')['epub-plain']
            with patch('any_to_md.adapters.persistence.sqlite.SQLiteCatalog.publish_export', side_effect=OSError('simulated commit failure')):
                with self.assertRaises(Exception):
                    convert(ConversionRequest(source, root / 'data'))
            runtime = JobRuntime(root / 'data')
            with closing(sqlite3.connect(root / 'data' / 'catalog.sqlite')) as connection:
                job_id, status, report_key = connection.execute('SELECT job_id, status, report_key FROM jobs').fetchone()
                self.assertEqual(connection.execute('SELECT status FROM exports').fetchone()[0], 'failed')
            self.assertEqual(status, 'failed')
            self.assertEqual(json.loads(runtime.storage.resolve(report_key).read_text())['status'], 'failed')
            self.assertEqual(list(runtime.storage.exports_dir.iterdir()), [])
            with self.assertRaises(ValueError):
                runtime.download(job_id)

    def test_exception_after_commit_preserves_successful_files_and_download(self):
        original = SQLiteCatalog.publish_export
        def commit_then_raise(catalog, **kwargs):
            original(catalog, **kwargs)
            raise OSError('simulated error after commit')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = build_fixtures(root / 'inputs')['epub-plain']
            with patch.object(SQLiteCatalog, 'publish_export', commit_then_raise):
                with self.assertLogs('any_to_md.application.jobs', level='ERROR'):
                    result = convert(ConversionRequest(source, root / 'data'))
            runtime = JobRuntime(root / 'data')
            self.assertTrue(runtime.get(result.job_id).status.downloadable)
            self.assertTrue(runtime.download(result.job_id).is_file())
            self.assertEqual(json.loads(result.report_path.read_text())['status'], result.status)
            runtime.recover()
            self.assertTrue(result.output_dir.is_dir())
            self.assertFalse((runtime.storage.staging_dir / result.export_id).exists())

    def test_cli_job_is_visible_and_downloadable_through_web(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = build_fixtures(root / 'inputs')['epub-plain']
            result = convert(ConversionRequest(source, root / 'data', ('html',)))
            with patch.object(settings, 'DATA_DIR', str(root / 'data')):
                with TestClient(app) as client:
                    status = client.get(f'/api/status/{result.job_id}').json()
                    self.assertEqual(status['status'], result.status)
                    download = client.get(status['download_url'])
                    self.assertEqual(download.status_code, 200)
                    capability = client.get('/api/capabilities').json()
                    self.assertIn('.docx', capability['docx']['suffixes'])
                    self.assertNotIn('.doc', capability['docx']['suffixes'])

    def test_existing_formats_use_cataloged_nested_paths_and_portable_resources(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sources = build_fixtures(root / 'inputs')
            docx = next(path for path in sources.values() if path.suffix == '.docx')
            html = root / 'nested.html'
            html.write_text('<html><body><p>BODY_NESTED_710</p></body></html>')
            with patch.object(settings, 'DATA_DIR', str(root / 'data')):
                with TestClient(app) as client:
                    for source in (html, docx):
                        job_id = client.post('/api/upload', files={'file': (source.name, source.read_bytes())}).json()['task_id']
                        client.post('/api/convert', json={'task_id': job_id, 'output_format': 'md_single'})
                        status = client.get(f'/api/status/{job_id}').json()
                        self.assertEqual(status['status'], 'completed', status)
                        downloaded = client.get(status['download_url'])
                        self.assertEqual(downloaded.status_code, 200)
                        job = JobRuntime(root / 'data').get(job_id)
                        if source == html:
                            self.assertEqual(job.download_relative, 'book.md')
                            self.assertIn('BODY_NESTED_710', BeautifulSoup(render_markdown(downloaded.content.decode()), 'html.parser').get_text())
                        else:
                            with ZipFile(io.BytesIO(downloaded.content)) as archive:
                                names = archive.namelist()
                                images = [name for name in names if name.startswith('assets/') and name.endswith('.png')]
                                self.assertEqual(len(images), 1)
                                with ZipFile(source) as original:
                                    self.assertEqual(archive.read(images[0]), original.read('word/media/red.png'))
                                markdown = archive.read(next(name for name in names if name.endswith('.md'))).decode()
                                self.assertIn(images[0], markdown)
                                text = BeautifulSoup(render_markdown(markdown), 'html.parser').get_text()
                                self.assertIn('DOCX_FIRST_401', text)

    def test_recovery_retries_a_failed_quarantine_without_exposing_download(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = build_fixtures(root / 'inputs')['epub-plain']
            runtime = JobRuntime(root / 'data')
            job = runtime.queue(runtime.upload(source.name, source.read_bytes()).job_id, 'html')
            runtime.catalog.claim_job(job.job_id, 'dead', 2**30, time.time() + 60)
            export_id = '22222222-2222-4222-8222-222222222222'
            runtime.catalog.start_export(job.job_id, 'dead', export_id, ('html',))
            staging = runtime.storage.create_staging(export_id)
            (staging / 'unfinished.html').write_text('unfinished')
            published = runtime.storage.publish(export_id)
            with patch.object(runtime.storage, 'rollback_publish', side_effect=OSError('busy')):
                with self.assertLogs('any_to_md.application.jobs', level='ERROR'):
                    runtime.recover()
            self.assertEqual(runtime.get(job.job_id).status, JobStatus.FAILED)
            self.assertTrue(published.exists())
            with self.assertRaises(ValueError):
                runtime.download(job.job_id)
            restarted = JobRuntime(root / 'data')
            restarted.recover()
            self.assertFalse(published.exists())
            report = json.loads(restarted.storage.diagnostic_path(job.job_id).read_text())
            self.assertIsNone(report['rollback_error'])

    def test_recovery_preserves_live_leases_and_quarantines_interrupted_publication(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = build_fixtures(root / 'inputs')['epub-plain']
            runtime = JobRuntime(root / 'data')
            jobs = [runtime.upload(source.name, source.read_bytes()) for _ in range(2)]
            for job in jobs:
                runtime.queue(job.job_id, 'html')
            runtime.catalog.claim_job(jobs[0].job_id, 'live', os.getpid(), time.time() + 60)
            runtime.catalog.claim_job(jobs[1].job_id, 'dead', 2**30, time.time() + 60)
            export_id = '11111111-1111-4111-8111-111111111111'
            runtime.catalog.start_export(jobs[1].job_id, 'dead', export_id, ('html',))
            staging = runtime.storage.create_staging(export_id)
            (staging / 'unfinished.html').write_text('unfinished')
            runtime.storage.publish(export_id)
            runtime.recover()
            self.assertEqual(runtime.get(jobs[0].job_id).status, JobStatus.PROCESSING)
            self.assertEqual(runtime.get(jobs[1].job_id).status, JobStatus.FAILED)
            self.assertFalse((runtime.storage.exports_dir / export_id).exists())
            self.assertTrue((runtime.storage.staging_dir / export_id / 'unfinished.html').is_file())
            with self.assertRaises(JobConflict):
                runtime.catalog.progress(jobs[1].job_id, 'dead', 90, 'stale worker')


if __name__ == '__main__':
    unittest.main()
