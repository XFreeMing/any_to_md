"""Independent old-schema fixtures exercise atomic, non-destructive upgrades."""

import hashlib
import json
import sqlite3
import tempfile
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path

from any_to_md.adapters.persistence.sqlite import SQLiteCatalog
from any_to_md.adapters.storage import sha256_file
from any_to_md.application.convert import ConversionRequest, convert
from any_to_md.application.jobs import JobRuntime, source_ids
from tests.quality.fixtures import build_fixtures


def old_catalog(root: Path, source: Path) -> dict:
    """Seed the six-column jobs / nonnullable export schema, without new code."""
    root.mkdir(parents=True)
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    source_id, book_id, edition_id = source_ids('epub', digest)
    revision_id, job_id, export_id = (str(uuid.uuid4()) for _ in range(3))
    original = root / 'sources' / (digest + '.epub')
    snapshot = root / 'revisions' / revision_id / 'book.json'
    exported = root / 'exports' / export_id / 'old.md'
    for path in (original, snapshot, exported):
        path.parent.mkdir(parents=True, exist_ok=True)
    original.write_bytes(source.read_bytes())
    snapshot.write_text(json.dumps({'schema_version': 1, 'revision_id': revision_id, 'edition_id': edition_id, 'source_sha256': digest, 'nodes': {'old-node': {'content_markdown': 'HISTORICAL CONTENT'}}}))
    exported.write_text('HISTORICAL EXPORT')
    database = root / 'catalog.sqlite'
    with closing(sqlite3.connect(database)) as connection:
        connection.executescript('''
            PRAGMA foreign_keys = ON;
            CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL);
            INSERT INTO schema_migrations VALUES(1, 'old');
            INSERT INTO schema_migrations VALUES(2, 'old');
            CREATE TABLE books(book_id TEXT PRIMARY KEY, title TEXT NOT NULL, authors_json TEXT NOT NULL, created_at TEXT NOT NULL);
            CREATE TABLE editions(edition_id TEXT PRIMARY KEY, book_id TEXT NOT NULL REFERENCES books(book_id), source_sha256 TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE(book_id, source_sha256));
            CREATE TABLE sources(source_id TEXT PRIMARY KEY, edition_id TEXT NOT NULL REFERENCES editions(edition_id), format TEXT NOT NULL, sha256 TEXT NOT NULL, storage_key TEXT NOT NULL, original_filename TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE(format, sha256));
            CREATE TABLE revisions(revision_id TEXT PRIMARY KEY, edition_id TEXT NOT NULL REFERENCES editions(edition_id), content_storage_key TEXT, content_sha256 TEXT, semantic_sha256 TEXT NOT NULL, pipeline_version TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL);
            CREATE TABLE jobs(job_id TEXT PRIMARY KEY, source_id TEXT NOT NULL REFERENCES sources(source_id), status TEXT NOT NULL, error_json TEXT, created_at TEXT NOT NULL, finished_at TEXT);
            CREATE TABLE exports(export_id TEXT PRIMARY KEY, revision_id TEXT NOT NULL REFERENCES revisions(revision_id), job_id TEXT NOT NULL REFERENCES jobs(job_id), config_json TEXT NOT NULL, status TEXT NOT NULL, storage_key TEXT, created_at TEXT NOT NULL, published_at TEXT);
            CREATE TABLE artifacts(artifact_id TEXT PRIMARY KEY, export_id TEXT NOT NULL REFERENCES exports(export_id), role TEXT NOT NULL, relative_path TEXT NOT NULL, sha256 TEXT NOT NULL, size_bytes INTEGER NOT NULL, media_type TEXT NOT NULL, UNIQUE(export_id, relative_path));
        ''')
        connection.execute('INSERT INTO books VALUES(?, ?, ?, ?)', (book_id, 'Old Book', '[]', 'old'))
        connection.execute('INSERT INTO editions VALUES(?, ?, ?, ?)', (edition_id, book_id, digest, 'old'))
        connection.execute('INSERT INTO sources VALUES(?, ?, ?, ?, ?, ?, ?)', (source_id, edition_id, 'epub', digest, original.relative_to(root).as_posix(), source.name, 'old'))
        connection.execute('INSERT INTO revisions VALUES(?, ?, ?, ?, ?, ?, ?, ?)', (revision_id, edition_id, snapshot.relative_to(root).as_posix(), sha256_file(snapshot), 'old-semantic', 'old-pipeline', 'ready', 'old'))
        connection.execute('INSERT INTO jobs VALUES(?, ?, ?, ?, ?, ?)', (job_id, source_id, 'completed', None, 'old', 'old'))
        connection.execute('INSERT INTO exports VALUES(?, ?, ?, ?, ?, ?, ?, ?)', (export_id, revision_id, job_id, '{"targets":["markdown"]}', 'completed', exported.parent.relative_to(root).as_posix(), 'old', 'old'))
        connection.execute('INSERT INTO artifacts VALUES(?, ?, ?, ?, ?, ?, ?)', ('old-artifact', export_id, 'markdown', exported.name, sha256_file(exported), exported.stat().st_size, 'text/markdown'))
        connection.commit()
    return {'revision_id': revision_id, 'job_id': job_id, 'export_id': export_id, 'snapshot': snapshot, 'exported': exported, 'original': original}


class CatalogMigrationTests(unittest.TestCase):
    def test_upgrade_preserves_old_records_files_and_backup_and_records_rebuild(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = build_fixtures(root / 'inputs')['epub-plain']
            data = root / 'data'
            old = old_catalog(data, source)
            files = {name: old[name].read_bytes() for name in ('snapshot', 'exported', 'original')}
            with closing(sqlite3.connect(data / 'catalog.sqlite')) as connection:
                before = {table: connection.execute(f'SELECT * FROM {table}').fetchall() for table in ('sources', 'revisions', 'exports', 'artifacts')}
            runtime = JobRuntime(data)
            self.assertEqual(runtime.get(old['job_id']).filename, source.name)
            backups = list(data.glob('catalog.before-v4.*.sqlite'))
            self.assertEqual(len(backups), 1)
            with closing(sqlite3.connect(backups[0])) as connection:
                self.assertEqual(connection.execute('PRAGMA user_version').fetchone()[0], 0)
                for table, rows in before.items():
                    self.assertEqual(connection.execute(f'SELECT * FROM {table}').fetchall(), rows)
            JobRuntime(data)
            self.assertEqual(list(data.glob('catalog.before-v4.*.sqlite')), backups)
            result = convert(ConversionRequest(data_dir=data, job_id=runtime.queue(old['job_id'], 'md_single').job_id))
            self.assertNotEqual(result.revision_id, old['revision_id'])
            for name, content in files.items():
                self.assertEqual(old[name].read_bytes(), content)
            with closing(sqlite3.connect(data / 'catalog.sqlite')) as connection:
                self.assertEqual(connection.execute('PRAGMA user_version').fetchone()[0], 4)
                self.assertEqual(connection.execute('PRAGMA foreign_key_check').fetchall(), [])
                for table in ('sources', 'exports', 'artifacts'):
                    self.assertTrue(set(before[table]).issubset(set(connection.execute(f'SELECT * FROM {table}').fetchall())))
                old_revision = connection.execute('SELECT * FROM revisions WHERE revision_id=?', (old['revision_id'],)).fetchall()
                self.assertEqual(old_revision, before['revisions'])
                self.assertEqual(connection.execute('SELECT old_revision_id, new_revision_id, from_schema, to_schema, reason FROM revision_migrations').fetchall(), [(old['revision_id'], result.revision_id, 1, 2, 'rebuilt_from_original')])

    def test_concurrent_initializers_keep_new_job_configuration(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = build_fixtures(root / 'inputs')['epub-plain']
            data = root / 'data'
            old_catalog(data, source)
            def initialize(index):
                runtime = JobRuntime(data)
                job = runtime.file_job(source, ('markdown', 'html'))
                return job.job_id
            with ThreadPoolExecutor(max_workers=4) as executor:
                job_ids = list(executor.map(initialize, range(8)))
            catalog = SQLiteCatalog(data / 'catalog.sqlite')
            for job_id in job_ids:
                job = catalog.get_job(job_id)
                self.assertEqual(job.targets, ('markdown', 'html'))
                self.assertEqual(job.output_format, 'md_single')
                self.assertEqual(job.message, 'Waiting to start')
            self.assertEqual(len(list(data.glob('catalog.before-v4.*.sqlite'))), 1)


if __name__ == '__main__':
    unittest.main()
