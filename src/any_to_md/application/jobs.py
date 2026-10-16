"""Shared job ingestion, execution ownership, publication and recovery."""

from __future__ import annotations

import errno
import json
import logging
import os
import threading
import time
import uuid
from pathlib import Path

from ..adapters.persistence.sqlite import SQLiteCatalog
from ..adapters.html_source import capture_html_source, pack_html_source
from ..adapters.url_source import capture_url_source
from ..adapters.storage import LocalStorage, sha256_file
from ..domain.artifacts import ArtifactRecord, ExportPlan
from ..domain.book import BookRevision
from ..domain.jobs import Job, JobConflict
from ..export.downloads import prepare_download
from ..export.files import write_json
from .capabilities import CAPABILITIES, OUTPUT_TARGET, input_format, validate_outputs

APPLICATION_NAMESPACE = uuid.UUID('ca7eb847-13d5-48a3-b0ca-44d59fedb155')
LEASE_SECONDS = 60
logger = logging.getLogger(__name__)


def source_ids(format_name: str, digest: str) -> tuple[str, str, str]:
    source_id = str(uuid.uuid5(APPLICATION_NAMESPACE, f'source:{format_name}:{digest}'))
    book_id = str(uuid.uuid5(APPLICATION_NAMESPACE, f'book:{digest}'))
    return source_id, book_id, str(uuid.uuid5(uuid.UUID(book_id), f'edition:{digest}'))


class ConversionFailure(RuntimeError):
    def __init__(self, job_id: str, report: Path, error: BaseException):
        self.job_id = job_id
        self.report_path = report
        super().__init__(f'{error}；job_id：{job_id}；诊断：{report}')


class JobRuntime:
    def __init__(self, data_dir: Path | str):
        self.storage = LocalStorage(Path(data_dir))
        self.catalog = SQLiteCatalog(self.storage.root / 'catalog.sqlite')

    def get(self, job_id: str) -> Job | None:
        return self.catalog.get_job(job_id)

    def _register(self, job: Job, key: str, digest: str, token: str | None = None) -> Job:
        source_id, book_id, edition_id = source_ids(job.input_format, digest)
        self.catalog.register_source(job_id=job.job_id, source_id=source_id, book_id=book_id, edition_id=edition_id, format_name=job.input_format, sha256=digest, storage_key=key, filename=job.filename, token=token)
        return self.get(job.job_id)

    def _ingestion_failed(self, job: Job, error: Exception) -> None:
        report = self.storage.diagnostic_path(job.job_id)
        write_json(report, {'schema_version': 2, 'job_id': job.job_id, 'status': 'failed', 'error': str(error)})
        self.catalog.fail_job(job.job_id, str(error), report.relative_to(self.storage.root).as_posix())
        raise ConversionFailure(job.job_id, report, error) from error

    def upload(self, filename: str, content: bytes, format_name: str | None = None) -> Job:
        format_name = format_name or input_format(filename) or 'unknown'
        job = self.catalog.create_job(str(uuid.uuid4()), filename, format_name, (), None)
        try:
            if format_name not in CAPABILITIES:
                raise ValueError(f'不支持的输入：{filename}')
            suffix = '.url' if format_name == 'url' else Path(filename).suffix
            if format_name == 'html' and suffix.lower() != '.zip':
                content = pack_html_source(filename, content)
                suffix = '.zip'
            _, key, digest = self.storage.store_bytes(content, suffix)
            return self._register(job, key, digest)
        except Exception as error:
            self._ingestion_failed(job, error)

    def file_job(self, path: Path | str, targets: tuple[str, ...]) -> Job:
        is_url = isinstance(path, str) and path.startswith(('http://', 'https://'))
        source = Path('webpage.url') if is_url else Path(path).expanduser().absolute()
        format_name = 'url' if is_url else input_format(source.name) or 'unknown'
        output = 'md_single' if 'markdown' in targets else 'pdf' if 'pdf' in targets else 'html'
        job = self.catalog.create_job(str(uuid.uuid4()), source.name, format_name, targets, output)
        try:
            validate_outputs(format_name, targets, output)
            if format_name == 'url':
                content = path.encode('utf-8') if is_url else source.read_bytes()
                _, key, digest = self.storage.store_bytes(content, '.url')
            elif not source.is_file():
                raise FileNotFoundError(f'输入文件不存在：{source}')
            elif format_name == 'html' and source.suffix.lower() != '.zip':
                _, key, digest = self.storage.store_bytes(capture_html_source(source), '.zip')
            else:
                digest = sha256_file(source)
                _, key = self.storage.store_source(source, digest)
            return self._register(job, key, digest)
        except Exception as error:
            self._ingestion_failed(job, error)

    def queue(self, job_id: str, output_format: str) -> Job:
        job = self.get(job_id)
        if job is None:
            raise KeyError(job_id)
        target = OUTPUT_TARGET.get(output_format)
        validate_outputs(job.input_format, (target,) if target else (), output_format)
        if job.source_id is None or job.input_key is None:
            raise ValueError('任务没有保留的原件，请重新上传')
        if job.status.terminal:
            job = self.catalog.create_job(str(uuid.uuid4()), job.filename, job.input_format, (), None, job.source_id)
        return self.catalog.queue_job(job.job_id, (target,), output_format, os.getpid(), time.time() + LEASE_SECONDS)

    def source_path(self, job: Job) -> Path:
        if job.input_key is None:
            raise ValueError('任务没有可读取的原件')
        path = self.storage.resolve(job.input_key)
        # Stored originals use their content hash as their filename.
        if not path.is_file() or sha256_file(path) != path.stem:
            raise ValueError('原件不存在或校验和不一致')
        return path

    def capture_source(self, job: Job, token: str) -> Path:
        """Capture URL originals only while holding the conversion job lease.

        Prior URL-only jobs follow the same path; their originals and older
        terminal jobs remain intact. A captured source replays without HTTP.
        """
        source = self.source_path(job)
        if job.input_format == 'url' and source.suffix == '.url':
            snapshot = capture_url_source(source.read_text(encoding='utf-8').strip())
            _, key, digest = self.storage.store_bytes(snapshot, '.zip')
            return self.source_path(self._register(job, key, digest, token))
        return source

    def execute(self, job_id: str) -> JobExecution:
        return JobExecution(self, job_id)

    def download(self, job_id: str) -> Path:
        record = self.catalog.download_record(job_id)
        if record is None:
            raise ValueError('任务没有已发布的下载产物')
        path = self.storage.resolve(record['storage_key'] + '/' + record['relative_path'])
        if not path.is_file() or path.stat().st_size != record['size_bytes'] or sha256_file(path) != record['sha256']:
            raise ValueError('已发布产物不存在或校验和不一致')
        return path

    def rebuilt_revisions(self, book: BookRevision) -> tuple[str, ...]:
        """Identify immutable schema-1 snapshots replaced by reading the original.

        Old content is never imported into the new body or rewritten in place.
        The lineage is committed only together with a successful new export.
        """
        old_ids = []
        for row in self.catalog.revision_snapshots(book.edition_id):
            path = self.storage.resolve(row['content_storage_key'])
            if not path.is_file() or sha256_file(path) != row['content_sha256']:
                raise ValueError(f"历史修订校验失败：{row['revision_id']}")
            snapshot = json.loads(path.read_text(encoding='utf-8'))
            if snapshot.get('schema_version') == 1:
                if snapshot.get('revision_id') != row['revision_id'] or snapshot.get('edition_id') != book.edition_id or snapshot.get('source_sha256') != book.source_sha256:
                    raise ValueError('历史修订与保留原件的身份不一致')
                old_ids.append(row['revision_id'])
        return tuple(old_ids)

    def recover(self) -> int:
        recovered = 0
        for row in self.catalog.active_jobs():
            alive = False
            if row['owner_pid'] is not None:
                try:
                    os.kill(row['owner_pid'], 0)
                    alive = True
                except OSError as error:
                    if error.errno == errno.EPERM:
                        alive = True
                    elif error.errno != errno.ESRCH:
                        raise
            if alive and row['lease_expires'] is not None and row['lease_expires'] > time.time():
                continue
            report = self.storage.diagnostic_path(row['job_id'])
            # A heartbeat renewal between observation and this CAS cancels recovery.
            if not self.catalog.interrupt_job(row['job_id'], row['owner_pid'], row['lease_expires'], report.relative_to(self.storage.root).as_posix()):
                continue
            recovered += 1
        # Repair the filesystem/report even if a previous recovery crashed after
        # committing the failure, or its first quarantine attempt failed.
        for job in self.catalog.failed_jobs():
            self._reconcile_failure(job)
        return recovered

    def _reconcile_failure(self, job: Job) -> None:
        report = self.storage.diagnostic_path(job.job_id)
        published = self.storage.exports_dir / job.export_id if job.export_id else None
        if report.exists() and (published is None or not published.exists()):
            return
        retained = self.storage.staging_dir / job.export_id if job.export_id else None
        rollback_error = None
        if published is not None and published.exists():
            try:
                retained = self.storage.rollback_publish(job.export_id)
            except OSError as failure:
                if published.exists() or not retained.exists():
                    logger.exception('Could not quarantine failed export %s', job.export_id)
                    retained, rollback_error = published, str(failure)
        failure_report = {'schema_version': 2, 'job_id': job.job_id, 'export_id': job.export_id, 'status': 'failed', 'error': job.message, 'rollback_error': rollback_error, 'retained_dir': str(retained) if retained else None}
        write_json(report, failure_report)
        if retained is not None and retained.exists() and retained != published:
            write_json(retained / 'report.json', failure_report)


class JobExecution:
    def __init__(self, runtime: JobRuntime, job_id: str):
        self.runtime = runtime
        self.job_id = job_id
        self.token = uuid.uuid4().hex
        self.job: Job | None = None
        self.export_id: str | None = None
        self.root: Path | None = None
        self.published: Path | None = None
        self.finished = False
        self._stop = threading.Event()
        self._heartbeat_error: Exception | None = None
        self._thread: threading.Thread | None = None

    def __enter__(self) -> JobExecution:
        self.runtime.catalog.claim_job(self.job_id, self.token, os.getpid(), time.time() + LEASE_SECONDS)
        self.job = self.runtime.get(self.job_id)
        self._thread = threading.Thread(target=self._heartbeat, daemon=True)
        self._thread.start()
        return self

    def _heartbeat(self) -> None:
        while not self._stop.wait(LEASE_SECONDS / 3):
            try:
                self.runtime.catalog.renew_job(self.job_id, self.token, time.time() + LEASE_SECONDS)
            except Exception as error:
                self._heartbeat_error = error
                return

    def progress(self, value: int, message: str) -> None:
        self.runtime.catalog.progress(self.job_id, self.token, value, message)

    def begin(self, export_id: str | None = None) -> Path:
        if self.export_id is not None:
            raise JobConflict('同一任务不能创建两个导出')
        self.export_id = export_id or str(uuid.uuid4())
        self.runtime.catalog.start_export(self.job_id, self.token, self.export_id, self.job.targets)
        self.root = self.runtime.storage.create_staging(self.export_id)
        return self.root

    def publish(self, report: dict, artifacts: list[ArtifactRecord], book: BookRevision | None = None, plan: ExportPlan | None = None) -> Path:
        if self._heartbeat_error is not None:
            raise JobConflict(f'任务续约失败：{self._heartbeat_error}')
        if self.root is None or self.export_id is None:
            raise JobConflict('任务尚未创建导出')
        download_relative = prepare_download(self.root, self.job.output_format).relative_to(self.root).as_posix()
        rebuilt_revisions = self.runtime.rebuilt_revisions(book) if book is not None else ()
        self.published = self.runtime.storage.publish(self.export_id)
        revision_key = self.runtime.storage.preserve_revision(book.revision_id, self.published) if book is not None else None
        revision_hash = sha256_file(self.published / 'book.json') if book is not None else None
        try:
            self.runtime.catalog.publish_export(job_id=self.job_id, token=self.token, export_id=self.export_id, storage_key=self.published.relative_to(self.runtime.storage.root).as_posix(), final_status=report['status'], artifacts=artifacts, download_relative=download_relative, book=book, plan=plan, revision_storage_key=revision_key, revision_content_sha256=revision_hash, rebuilt_revisions=rebuilt_revisions)
        except Exception:
            if not self._committed():
                raise
            logger.exception('Publication committed despite a subsequent error: %s', self.job_id)
        self.finished = True
        self._stop.set()
        return self.published

    def _committed(self) -> bool:
        job = self.runtime.get(self.job_id)
        return bool(job and job.status.downloadable and job.export_id == self.export_id
                    and self.published is not None
                    and job.storage_key == self.published.relative_to(self.runtime.storage.root).as_posix())

    def __exit__(self, exception_type, error, traceback) -> bool:
        try:
            if self.finished:
                return False
            if self._committed():
                return False
            error = error or RuntimeError('任务退出前未发布结果')
            report = self.runtime.storage.diagnostic_path(self.job_id)
            # Commit the conditional terminal transition before changing files.
            # If ownership/commit outcome cannot be established, leave them intact.
            self.runtime.catalog.fail_job(self.job_id, str(error), report.relative_to(self.runtime.storage.root).as_posix(), self.token)
            self.runtime._reconcile_failure(self.runtime.get(self.job_id))
            if not isinstance(error, Exception):
                return False
            raise ConversionFailure(self.job_id, report, error) from error
        finally:
            self._stop.set()
            if self._thread is not None:
                self._thread.join(timeout=5)
