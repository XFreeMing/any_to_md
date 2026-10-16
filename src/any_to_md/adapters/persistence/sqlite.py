"""The single catalog for source identity, durable jobs and published artifacts."""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from contextlib import closing, contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterator

from ...domain.artifacts import ArtifactRecord, ExportPlan, ViewLocation
from ...domain.book import BookRevision
from ...domain.jobs import Job, JobConflict, JobStatus


def _now() -> str:
    return datetime.now(UTC).isoformat()


class SQLiteCatalog:
    def __init__(self, database_path: Path):
        database_path.parent.mkdir(parents=True, exist_ok=True)
        self.database_path = database_path
        self._migrate()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        return connection

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _migrate(self) -> None:
        connection = self._connect()
        try:
            connection.execute('PRAGMA foreign_keys = OFF')
            connection.execute('BEGIN IMMEDIATE')
            # All schema decisions are made after acquiring the write lock.
            version = connection.execute('PRAGMA user_version').fetchone()[0]
            if version > 4:
                raise ValueError(f'不支持的数据库版本：{version}')
            if version == 4:
                return
            if connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='jobs'").fetchone():
                backup = self.database_path.with_name(f'{self.database_path.stem}.before-v4.{uuid.uuid4().hex}.sqlite')
                with closing(sqlite3.connect(backup)) as destination:
                    # A separate reader can back up the committed database while
                    # this connection holds the migration's reserved write lock.
                    with closing(self._connect()) as reader:
                        reader.backup(destination)
            schema = (
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version INTEGER PRIMARY KEY,
                    applied_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS books (
                    book_id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    authors_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS editions (
                    edition_id TEXT PRIMARY KEY,
                    book_id TEXT NOT NULL REFERENCES books(book_id),
                    source_sha256 TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(book_id, source_sha256)
                );
                CREATE TABLE IF NOT EXISTS sources (
                    source_id TEXT PRIMARY KEY,
                    edition_id TEXT NOT NULL REFERENCES editions(edition_id),
                    format TEXT NOT NULL,
                    sha256 TEXT NOT NULL,
                    storage_key TEXT NOT NULL,
                    original_filename TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(format, sha256)
                );
                CREATE TABLE IF NOT EXISTS revisions (
                    revision_id TEXT PRIMARY KEY,
                    edition_id TEXT NOT NULL REFERENCES editions(edition_id),
                    content_storage_key TEXT,
                    content_sha256 TEXT,
                    semantic_sha256 TEXT NOT NULL,
                    pipeline_version TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS section_identities (
                    node_id TEXT PRIMARY KEY,
                    edition_id TEXT NOT NULL REFERENCES editions(edition_id),
                    source_key TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(edition_id, source_key)
                );
                CREATE TABLE IF NOT EXISTS revision_migrations (
                    old_revision_id TEXT NOT NULL REFERENCES revisions(revision_id),
                    new_revision_id TEXT NOT NULL REFERENCES revisions(revision_id),
                    from_schema INTEGER NOT NULL,
                    to_schema INTEGER NOT NULL,
                    reason TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(old_revision_id, new_revision_id)
                );
                CREATE TABLE IF NOT EXISTS section_versions (
                    revision_id TEXT NOT NULL REFERENCES revisions(revision_id),
                    node_id TEXT NOT NULL REFERENCES section_identities(node_id),
                    parent_node_id TEXT,
                    sibling_order INTEGER NOT NULL,
                    depth INTEGER NOT NULL,
                    role TEXT NOT NULL,
                    title_original TEXT NOT NULL,
                    title_display TEXT NOT NULL,
                    ordinal_path TEXT NOT NULL,
                    source_locator_json TEXT NOT NULL,
                    PRIMARY KEY(revision_id, node_id)
                );
                CREATE INDEX IF NOT EXISTS idx_section_children
                    ON section_versions(revision_id, parent_node_id, sibling_order);
                CREATE TABLE IF NOT EXISTS jobs (
                    job_id TEXT PRIMARY KEY,
                    source_id TEXT REFERENCES sources(source_id),
                    status TEXT NOT NULL,
                    error_json TEXT,
                    created_at TEXT NOT NULL,
                    finished_at TEXT
                );
                CREATE TABLE IF NOT EXISTS exports (
                    export_id TEXT PRIMARY KEY,
                    revision_id TEXT REFERENCES revisions(revision_id),
                    job_id TEXT NOT NULL REFERENCES jobs(job_id),
                    config_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    storage_key TEXT,
                    created_at TEXT NOT NULL,
                    published_at TEXT
                );
                CREATE TABLE IF NOT EXISTS artifacts (
                    artifact_id TEXT PRIMARY KEY,
                    export_id TEXT NOT NULL REFERENCES exports(export_id),
                    role TEXT NOT NULL,
                    relative_path TEXT NOT NULL,
                    sha256 TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL,
                    media_type TEXT NOT NULL,
                    UNIQUE(export_id, relative_path)
                );
                CREATE TABLE IF NOT EXISTS export_locations (
                    export_id TEXT NOT NULL REFERENCES exports(export_id),
                    node_id TEXT NOT NULL REFERENCES section_identities(node_id),
                    view TEXT NOT NULL,
                    unit_id TEXT NOT NULL DEFAULT '',
                    relative_path TEXT NOT NULL,
                    anchor TEXT NOT NULL,
                    PRIMARY KEY(export_id, node_id, view)
                );
                CREATE INDEX IF NOT EXISTS idx_export_location_lookup
                    ON export_locations(export_id, node_id, view);
                """
            )
            for statement in schema.split(";"):
                if statement.strip():
                    connection.execute(statement)
            connection.execute(
                """
                INSERT OR IGNORE INTO schema_migrations(version, applied_at)
                VALUES(1, ?)
                """,
                (_now(),),
            )
            revision_columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(revisions)").fetchall()
            }
            if "semantic_sha256" not in revision_columns:
                connection.execute(
                    "ALTER TABLE revisions ADD COLUMN semantic_sha256 TEXT NOT NULL DEFAULT ''"
                )
            connection.execute(
                """
                INSERT OR IGNORE INTO schema_migrations(version, applied_at)
                VALUES(2, ?)
                """,
                (_now(),),
            )
            self._upgrade_jobs(connection)
            connection.execute('INSERT OR IGNORE INTO schema_migrations VALUES(4, ?)', (_now(),))
            connection.execute('PRAGMA user_version = 4')
            if connection.execute('PRAGMA foreign_key_check').fetchone():
                raise ValueError('数据库升级后的外键检查失败')
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _upgrade_jobs(self, connection: sqlite3.Connection) -> None:
        columns = {row['name']: row for row in connection.execute('PRAGMA table_info(jobs)')}
        exports = {row['name']: row for row in connection.execute('PRAGMA table_info(exports)')}
        if 'owner_token' in columns and not columns['source_id']['notnull'] and not exports['revision_id']['notnull']:
            return
        # Called only inside the migration transaction with FK enforcement off.
        connection.execute('''CREATE TABLE jobs_v3 (
            job_id TEXT PRIMARY KEY, source_id TEXT REFERENCES sources(source_id),
            status TEXT NOT NULL, error_json TEXT, created_at TEXT NOT NULL, finished_at TEXT,
            filename TEXT NOT NULL DEFAULT '', input_format TEXT NOT NULL DEFAULT 'unknown',
            targets_json TEXT NOT NULL DEFAULT '[]', output_format TEXT,
            progress INTEGER NOT NULL DEFAULT 0, message TEXT NOT NULL DEFAULT '',
            report_key TEXT, download_relative TEXT,
            owner_token TEXT, owner_pid INTEGER, lease_expires REAL
        )''')
        names = [row['name'] for row in connection.execute('PRAGMA table_info(jobs_v3)') if row['name'] in columns]
        identifiers = ', '.join(names)
        connection.execute(f'INSERT INTO jobs_v3 ({identifiers}) SELECT {identifiers} FROM jobs')
        connection.execute('DROP TABLE jobs')
        connection.execute('ALTER TABLE jobs_v3 RENAME TO jobs')
        if exports['revision_id']['notnull']:
            connection.execute('''CREATE TABLE exports_v3 (
                export_id TEXT PRIMARY KEY, revision_id TEXT REFERENCES revisions(revision_id),
                job_id TEXT NOT NULL REFERENCES jobs(job_id), config_json TEXT NOT NULL,
                status TEXT NOT NULL, storage_key TEXT, created_at TEXT NOT NULL, published_at TEXT
            )''')
            connection.execute('INSERT INTO exports_v3 SELECT * FROM exports')
            connection.execute('DROP TABLE exports')
            connection.execute('ALTER TABLE exports_v3 RENAME TO exports')
        connection.execute('''UPDATE jobs SET
            filename = COALESCE((SELECT original_filename FROM sources WHERE sources.source_id = jobs.source_id), filename),
            input_format = COALESCE((SELECT format FROM sources WHERE sources.source_id = jobs.source_id), input_format)
            WHERE filename = '' ''')
        if connection.execute('PRAGMA foreign_key_check').fetchone():
            raise ValueError('任务 schema 升级后的外键检查失败')
        connection.execute('INSERT OR IGNORE INTO schema_migrations VALUES(3, ?)', (_now(),))

    def create_job(self, job_id: str, filename: str, input_format: str, targets: tuple[str, ...], output_format: str | None, source_id: str | None = None) -> Job:
        with self._transaction() as connection:
            connection.execute('''INSERT INTO jobs(job_id, source_id, status, created_at, filename, input_format, targets_json, output_format, message)
                VALUES(?, ?, 'pending', ?, ?, ?, ?, ?, 'Waiting to start')''',
                (job_id, source_id, _now(), filename, input_format, json.dumps(targets), output_format))
        return self.get_job(job_id)

    def get_job(self, job_id: str) -> Job | None:
        connection = self._connect()
        try:
            row = connection.execute('''SELECT j.*, s.storage_key AS input_key, e.export_id, e.storage_key
                FROM jobs j LEFT JOIN sources s ON s.source_id = j.source_id
                LEFT JOIN exports e ON e.job_id = j.job_id WHERE j.job_id = ?
                ORDER BY e.created_at DESC LIMIT 1''', (job_id,)).fetchone()
            if row is None:
                return None
            return Job(**{name: row[name] for name in ('job_id', 'filename', 'input_format', 'output_format', 'source_id', 'input_key', 'progress', 'message', 'created_at', 'finished_at', 'report_key', 'export_id', 'download_relative', 'storage_key')}, status=JobStatus(row['status']), targets=tuple(json.loads(row['targets_json'])))
        finally:
            connection.close()

    @staticmethod
    def _owned(connection: sqlite3.Connection, job_id: str, token: str) -> None:
        row = connection.execute("SELECT 1 FROM jobs WHERE job_id = ? AND owner_token = ? AND status = 'processing' AND lease_expires > ?", (job_id, token, time.time())).fetchone()
        if row is None:
            raise JobConflict(f'任务已结束或执行权已失效：{job_id}')

    def queue_job(self, job_id: str, targets: tuple[str, ...], output_format: str, owner_pid: int, lease_expires: float) -> Job:
        with self._transaction() as connection:
            changed = connection.execute("UPDATE jobs SET status='queued', targets_json=?, output_format=?, message='Queued for conversion', owner_pid=?, lease_expires=? WHERE job_id=? AND status='pending' AND source_id IS NOT NULL", (json.dumps(targets), output_format, owner_pid, lease_expires, job_id)).rowcount
            if changed != 1:
                raise JobConflict(f'任务不可重复排队：{job_id}')
        return self.get_job(job_id)

    def claim_job(self, job_id: str, token: str, owner_pid: int, lease_expires: float) -> None:
        with self._transaction() as connection:
            changed = connection.execute("UPDATE jobs SET status='processing', owner_token=?, owner_pid=?, lease_expires=?, progress=5, message='Starting conversion' WHERE job_id=? AND status IN ('pending','queued')", (token, owner_pid, lease_expires, job_id)).rowcount
            if changed != 1:
                raise JobConflict(f'任务已被领取或已结束：{job_id}')

    def renew_job(self, job_id: str, token: str, lease_expires: float) -> None:
        with self._transaction() as connection:
            self._owned(connection, job_id, token)
            connection.execute('UPDATE jobs SET lease_expires=? WHERE job_id=?', (lease_expires, job_id))

    def progress(self, job_id: str, token: str, progress: int, message: str) -> None:
        with self._transaction() as connection:
            self._owned(connection, job_id, token)
            connection.execute('UPDATE jobs SET progress=?, message=? WHERE job_id=?', (max(0, min(progress, 99)), message, job_id))

    def register_source(self, *, job_id: str, source_id: str, book_id: str, edition_id: str, format_name: str, sha256: str, storage_key: str, filename: str) -> None:
        now = _now()
        with self._transaction() as connection:
            connection.execute('INSERT OR IGNORE INTO books VALUES(?, ?, ?, ?)', (book_id, Path(filename).stem, '[]', now))
            connection.execute('INSERT OR IGNORE INTO editions VALUES(?, ?, ?, ?)', (edition_id, book_id, sha256, now))
            connection.execute('INSERT OR IGNORE INTO sources VALUES(?, ?, ?, ?, ?, ?, ?)', (source_id, edition_id, format_name, sha256, storage_key, filename, now))
            connection.execute('UPDATE jobs SET source_id=? WHERE job_id=? AND status=\'pending\'', (source_id, job_id))

    def start_export(self, job_id: str, token: str, export_id: str, targets: tuple[str, ...]) -> None:
        with self._transaction() as connection:
            self._owned(connection, job_id, token)
            connection.execute("INSERT INTO exports VALUES(?, NULL, ?, ?, 'staging', NULL, ?, NULL)", (export_id, job_id, json.dumps({'targets': targets}), _now()))

    def download_record(self, job_id: str) -> sqlite3.Row | None:
        connection = self._connect()
        try:
            return connection.execute('''SELECT e.storage_key, a.relative_path, a.sha256, a.size_bytes FROM jobs j
                JOIN exports e ON e.job_id=j.job_id JOIN artifacts a ON a.export_id=e.export_id
                WHERE j.job_id=? AND j.status IN ('completed','completed_with_warnings','partial')
                AND e.status=j.status AND a.relative_path=j.download_relative''', (job_id,)).fetchone()
        finally:
            connection.close()

    def active_jobs(self) -> list[dict]:
        connection = self._connect()
        try:
            return [dict(row) for row in connection.execute("SELECT job_id, owner_pid, lease_expires FROM jobs WHERE status IN ('queued','processing')")]
        finally:
            connection.close()

    def failed_jobs(self) -> list[Job]:
        with closing(self._connect()) as connection:
            ids = [row['job_id'] for row in connection.execute("SELECT job_id FROM jobs WHERE status='failed'")]
        return [job for job_id in ids if (job := self.get_job(job_id)) is not None]

    def revision_snapshots(self, edition_id: str) -> list[dict]:
        with closing(self._connect()) as connection:
            return [dict(row) for row in connection.execute('SELECT revision_id, content_storage_key, content_sha256 FROM revisions WHERE edition_id=? AND content_storage_key IS NOT NULL', (edition_id,))]

    def attach_revision(
        self, *, book: BookRevision, semantic_sha256: str,
        job_id: str, token: str, plan: ExportPlan,
    ) -> None:
        created_at = _now()
        with self._transaction() as connection:
            self._owned(connection, job_id, token)
            connection.execute('UPDATE books SET title=?, authors_json=? WHERE book_id=?',
                (book.metadata.title, json.dumps(book.metadata.authors, ensure_ascii=False), book.book_id))
            connection.execute(
                """
                INSERT OR IGNORE INTO revisions(
                    revision_id, edition_id, content_storage_key,
                    content_sha256, semantic_sha256, pipeline_version,
                    status, created_at
                ) VALUES(?, ?, NULL, ?, ?, ?, 'ready', ?)
                """,
                (
                    book.revision_id,
                    book.edition_id,
                    semantic_sha256,
                    semantic_sha256,
                    book.pipeline_version,
                    created_at,
                ),
            )
            existing_revision = connection.execute(
                "SELECT edition_id, semantic_sha256, pipeline_version FROM revisions WHERE revision_id = ?",
                (book.revision_id,),
            ).fetchone()
            if (
                existing_revision is None
                or existing_revision["edition_id"] != book.edition_id
                or existing_revision["semantic_sha256"] != semantic_sha256
                or existing_revision["pipeline_version"] != book.pipeline_version
            ):
                raise ValueError(f"revision 身份冲突：{book.revision_id}")
            for node_id in book.walk_ids():
                node = book.nodes[node_id]
                connection.execute(
                    """
                    INSERT OR IGNORE INTO section_identities(
                        node_id, edition_id, source_key, created_at
                    ) VALUES(?, ?, ?, ?)
                    """,
                    (node.node_id, book.edition_id, node.source_key, created_at),
                )
                values = (
                    book.revision_id,
                    node.node_id,
                    node.parent_id,
                    node.sibling_order,
                    node.depth,
                    node.role,
                    node.title_original,
                    node.title_display,
                    ".".join(str(value) for value in node.ordinal_path),
                    json.dumps(
                        node.source_documents_dict(),
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                )
                connection.execute(
                    """
                    INSERT OR IGNORE INTO section_versions(
                        revision_id, node_id, parent_node_id, sibling_order, depth,
                        role, title_original, title_display, ordinal_path,
                        source_locator_json
                    ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    values,
                )
                existing_section = connection.execute(
                    """
                    SELECT revision_id, node_id, parent_node_id, sibling_order, depth,
                           role, title_original, title_display, ordinal_path,
                           source_locator_json
                    FROM section_versions WHERE revision_id = ? AND node_id = ?
                    """,
                    (book.revision_id, node.node_id),
                ).fetchone()
                if existing_section is None or tuple(existing_section) != values:
                    raise ValueError(f"已发布 revision 的章节内容不一致：{node.node_id}")
            changed = connection.execute(
                "UPDATE exports SET revision_id=?, config_json=? WHERE export_id=? AND job_id=? AND status='staging'",
                (book.revision_id, json.dumps({'targets': plan.targets, 'split_policy': plan.split_policy}), plan.export_id, job_id),
            ).rowcount
            if changed != 1:
                raise JobConflict('导出计划不属于当前任务')

    def publish_export(
        self, *, job_id: str, token: str, export_id: str,
        storage_key: str, final_status: str, artifacts: list[ArtifactRecord],
        download_relative: str, book: BookRevision | None = None,
        plan: ExportPlan | None = None, revision_storage_key: str | None = None,
        revision_content_sha256: str | None = None,
        rebuilt_revisions: tuple[str, ...] = (),
    ) -> None:
        if not JobStatus(final_status).downloadable:
            raise ValueError('失败任务不能发布下载')
        if download_relative not in {artifact.relative_path for artifact in artifacts}:
            raise ValueError('下载文件未列入产物清单')
        published_at = _now()
        with self._transaction() as connection:
            self._owned(connection, job_id, token)
            if book is not None:
                if plan is None or revision_storage_key is None or revision_content_sha256 is None:
                    raise ValueError('规范修订缺少发布信息')
                existing = connection.execute('SELECT content_storage_key, content_sha256 FROM revisions WHERE revision_id=?', (book.revision_id,)).fetchone()
                if existing is None:
                    raise ValueError(f'revision 不存在：{book.revision_id}')
                if existing['content_storage_key'] and existing['content_sha256'] != revision_content_sha256:
                    raise ValueError(f'revision 快照哈希冲突：{book.revision_id}')
                connection.execute('''UPDATE revisions SET content_storage_key=COALESCE(content_storage_key, ?),
                    content_sha256=CASE WHEN content_storage_key IS NULL THEN ? ELSE content_sha256 END WHERE revision_id=?''',
                    (revision_storage_key, revision_content_sha256, book.revision_id))
                for old_revision_id in rebuilt_revisions:
                    old = connection.execute('SELECT edition_id FROM revisions WHERE revision_id=?', (old_revision_id,)).fetchone()
                    if old is None or old['edition_id'] != book.edition_id or old_revision_id == book.revision_id:
                        raise ValueError('旧修订与重建修订的来源身份不一致')
                    connection.execute('INSERT OR IGNORE INTO revision_migrations VALUES(?, ?, 1, 2, ?, ?)', (old_revision_id, book.revision_id, 'rebuilt_from_original', published_at))
            for artifact in artifacts:
                connection.execute('INSERT INTO artifacts VALUES(?, ?, ?, ?, ?, ?, ?)',
                    (f'{export_id}:{artifact.relative_path}', export_id, artifact.role, artifact.relative_path, artifact.sha256, artifact.size_bytes, artifact.media_type))
            if book is not None:
                for node_id in book.walk_ids():
                    locations = []
                    if 'markdown' in plan.targets:
                        locations.extend((('single', plan.single_locations[node_id]), ('split', plan.split_locations[node_id])))
                    if {'html', 'pdf'}.intersection(plan.targets):
                        locations.append(('html', ViewLocation('book.html', book.nodes[node_id].anchor)))
                    for view, location in locations:
                        connection.execute('INSERT INTO export_locations VALUES(?, ?, ?, ?, ?, ?)',
                            (export_id, node_id, view, location.unit_id or '', location.path, location.anchor))
            changed = connection.execute("UPDATE exports SET status=?, storage_key=?, published_at=? WHERE export_id=? AND job_id=? AND status='staging'", (final_status, storage_key, published_at, export_id, job_id)).rowcount
            if changed != 1:
                raise JobConflict('导出已被终结')
            connection.execute("UPDATE jobs SET status=?, progress=100, message=?, finished_at=?, download_relative=?, report_key=?, lease_expires=NULL WHERE job_id=?", (final_status, 'Conversion is partial; see report' if final_status == 'partial' else 'Conversion completed', published_at, download_relative, storage_key + '/report.json', job_id))

    def fail_job(self, job_id: str, error: str, report_key: str, token: str | None = None) -> None:
        condition = "status='pending'" if token is None else "status='processing' AND owner_token=?"
        values = (job_id,) if token is None else (job_id, token)
        with self._transaction() as connection:
            changed = connection.execute(f"UPDATE jobs SET status='failed', error_json=?, message=?, report_key=?, finished_at=?, download_relative=NULL, lease_expires=NULL WHERE job_id=? AND {condition}",
                (json.dumps({'message': error}, ensure_ascii=False), error, report_key, _now(), *values)).rowcount
            if changed != 1:
                raise JobConflict(f'无法覆盖已经终结的任务：{job_id}')
            connection.execute("UPDATE exports SET status='failed' WHERE job_id=? AND status='staging'", (job_id,))

    def interrupt_job(self, job_id: str, owner_pid: int | None, lease_expires: float | None, report_key: str) -> bool:
        with self._transaction() as connection:
            changed = connection.execute("UPDATE jobs SET status='failed', error_json=?, message=?, report_key=?, finished_at=?, download_relative=NULL, lease_expires=NULL WHERE job_id=? AND status IN ('queued','processing') AND owner_pid IS ? AND lease_expires IS ?", (json.dumps({'code': 'interrupted'}), 'Worker interrupted; create a new conversion to retry', report_key, _now(), job_id, owner_pid, lease_expires)).rowcount
            if changed:
                connection.execute("UPDATE exports SET status='failed' WHERE job_id=? AND status='staging'", (job_id,))
            return changed == 1
