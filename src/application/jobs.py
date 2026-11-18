"""Shared job ingestion, execution ownership, publication and recovery."""

from __future__ import annotations

import errno
import hashlib
import json
import logging
import os
import threading
import time
import uuid
from pathlib import Path

from ..adapters.persistence.sqlite import SQLiteCatalog
from ..adapters.html_source import capture_html_members, pack_html_source
from ..adapters.markdown_source import capture_resources, pack_markdown_source
from ..adapters.source_package import resources_fingerprint
from ..adapters.url_source import capture_url_source
from ..adapters.storage import LocalStorage, sha256_file
from ..domain.artifacts import ArtifactRecord, ExportPlan
from ..domain.book import BookRevision
from ..domain.jobs import Job, JobConflict, JobStatus
from ..export.downloads import prepare_download
from ..export.files import write_json
from .capabilities import CAPABILITIES, OUTPUT_TARGET, input_format, validate_outputs
from .library import choose_current_export

APPLICATION_NAMESPACE = uuid.UUID('ca7eb847-13d5-48a3-b0ca-44d59fedb155')
LEASE_SECONDS = 60
logger = logging.getLogger(__name__)


def _exception_chain(error: BaseException) -> list[dict[str, str]]:
    """Keep typed causes/contexts even when the outer error has a short message."""
    chain, seen = [], set()
    relation = 'raised'
    while error is not None and id(error) not in seen:
        seen.add(id(error))
        chain.append({'type': f'{type(error).__module__}.{type(error).__qualname__}',
                      'message': str(error), 'relation': relation})
        if error.__cause__ is not None:
            error, relation = error.__cause__, 'cause'
        else:
            # Transport libraries may use `raise ... from None` to shorten a
            # traceback. The original OS error still matters in durable evidence.
            error, relation = error.__context__, 'context'
    return chain


def _process_alive(pid: int) -> bool:
    if os.name == 'nt':
        # os.kill(pid, 0) sends a console event on Windows; it is not a probe.
        # Waiting with zero timeout only observes the process, without signals
        # or PROCESS_TERMINATE rights. A retained handle may outlive its process.
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
        kernel32.WaitForSingleObject.restype = wintypes.DWORD
        kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
        kernel32.CloseHandle.restype = wintypes.BOOL
        handle = kernel32.OpenProcess(0x00100000, False, pid)  # SYNCHRONIZE
        if not handle:
            error = ctypes.get_last_error()
            if error == 87:  # ERROR_INVALID_PARAMETER: no such PID
                return False
            if error == 5:  # ERROR_ACCESS_DENIED: cannot disprove liveness
                return True
            raise ctypes.WinError(error)
        try:
            state = kernel32.WaitForSingleObject(handle, 0)
            if state == 0:  # WAIT_OBJECT_0: process exited
                return False
            if state == 258:  # WAIT_TIMEOUT: process still running
                return True
            raise ctypes.WinError(ctypes.get_last_error())
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except OSError as error:
        if error.errno == errno.EPERM:
            return True
        if error.errno == errno.ESRCH:
            return False
        raise


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
        receipt = self._base_receipt(job)
        receipt['status'] = 'failed'
        receipt['phase']['save'] = 'failed'
        receipt['diagnostic_code'] = 'save_failed'
        receipt['diagnostic_key'] = report.relative_to(self.storage.root).as_posix()
        self._write_receipt(job.job_id, receipt)
        raise ConversionFailure(job.job_id, report, error) from error

    # -- collection receipts and intents --------------------------------

    def _job_dir(self, job_id: str) -> Path:
        path = self.storage.root / 'jobs' / str(uuid.UUID(job_id))
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _receipt_path(self, job_id: str) -> Path:
        return self._job_dir(job_id) / 'result.json'

    def _intent_path(self, job_id: str) -> Path:
        return self._job_dir(job_id) / 'intent.json'

    def _write_receipt(self, job_id: str, receipt: dict) -> None:
        write_json(self._receipt_path(job_id), receipt)

    @staticmethod
    def _base_receipt(job: Job) -> dict:
        return {
            'schema_version': 1,
            'job_id': job.job_id,
            'input_format': job.input_format,
            'filename': job.filename,
            'status': str(job.status),
            'phase': {'save': 'pending', 'extraction': 'not_started', 'publish': 'not_started'},
            'diagnostic_code': None,
            'source': None,
            'conversion': None,
        }

    def result(self, job_id: str) -> dict | None:
        """Versioned machine-readable receipt for one job, if it exists."""
        path = self._receipt_path(job_id)
        if path.is_file():
            try:
                data = json.loads(path.read_text(encoding='utf-8'))
            except (json.JSONDecodeError, UnicodeError):
                data = None
            if isinstance(data, dict):
                return data
        job = self.get(job_id)
        if job is None:
            return None
        # A crash may land between a committed catalog transition and the
        # receipt write; rebuild the stable subset from the catalog.
        receipt = self._base_receipt(job)
        receipt['status'] = str(job.status)
        if job.source_id is not None and job.input_key is not None:
            receipt['phase']['save'] = 'saved'
            stored = job.input_key.split('/', 1)[-1]
            digest = stored.split('.', 1)[0]
            receipt['source'] = {
                'source_id': job.source_id,
                'storage_key': job.input_key,
                'source_sha256': digest,
                'original_sha256': None,
                'original_size_bytes': None,
                'package': None,
            }
        if job.status in {JobStatus.COMPLETED, JobStatus.COMPLETED_WITH_WARNINGS, JobStatus.PARTIAL}:
            receipt['phase']['extraction'] = str(job.status)
            receipt['phase']['publish'] = 'published'
            receipt['conversion'] = {
                'export_id': job.export_id,
                'status': str(job.status),
                'report_key': job.report_key,
            }
        elif job.status == JobStatus.SAVED_ONLY:
            receipt['phase']['save'] = 'saved'
            receipt['phase']['extraction'] = 'not_applicable'
        elif job.status == JobStatus.FAILED:
            receipt['diagnostic_code'] = 'extraction_failed'
            receipt['phase']['extraction'] = 'failed'
        return receipt

    def _write_source_receipt(self, job: Job, *, package: dict | None, original_sha256: str, original_size_bytes: int, stored_sha256: str, key: str) -> dict:
        receipt = self.result(job.job_id) or self._base_receipt(job)
        receipt['status'] = str(job.status)
        receipt['phase']['save'] = 'saved'
        receipt['source'] = {
            'source_id': job.source_id,
            'storage_key': key,
            'source_sha256': stored_sha256,
            'original_sha256': original_sha256,
            'original_size_bytes': original_size_bytes,
            'package': package,
        }
        self._write_receipt(job.job_id, receipt)
        return receipt

    def record_published(self, job_id: str, *, book: BookRevision, plan: ExportPlan, report: dict, export_dir: Path) -> dict:
        job = self.get(job_id)
        receipt = self.result(job_id) or self._base_receipt(job)
        receipt['status'] = report['status']
        receipt['phase']['extraction'] = report['status']
        receipt['phase']['publish'] = 'published'
        receipt['conversion'] = {
            'book_id': book.book_id,
            'edition_id': book.edition_id,
            'revision_id': book.revision_id,
            'export_id': plan.export_id,
            'status': report['status'],
            'pipeline_version': book.pipeline_version,
            'report_key': (export_dir / 'report.json').relative_to(self.storage.root).as_posix(),
        }
        self._write_receipt(job_id, receipt)
        return receipt

    def record_failed(self, job_id: str, error: BaseException) -> dict:
        job = self.get(job_id)
        if job is None:
            raise KeyError(job_id)
        receipt = self.result(job_id) or self._base_receipt(job)
        receipt['status'] = str(job.status)
        if receipt['phase']['save'] != 'failed':
            receipt['phase']['save'] = 'saved' if job.source_id else 'pending'
        interrupted = str(job.status) == 'failed' and 'interrupt' in (job.message or '').lower()
        receipt['phase']['extraction'] = 'interrupted' if interrupted else 'failed'
        receipt['diagnostic_code'] = 'interrupted' if interrupted else 'extraction_failed'
        receipt['diagnostic_message'] = str(error)
        self._write_receipt(job_id, receipt)
        return receipt

    # -- collection -----------------------------------------------------

    @staticmethod
    def _safe_filename(filename: str) -> str:
        name = Path(filename).name
        if name in {'', '.', '..'}:
            raise ValueError(f'无效的输入文件名：{filename!r}')
        return name

    @staticmethod
    def _stored_bytes(format_name: str, filename: str, content: bytes, *, resources: dict[str, bytes] | None = None, collect: bool) -> tuple[bytes, str, dict | None]:
        """Decide the stored representation before the blob is written."""
        suffix = Path(filename).suffix.lower()
        if format_name == 'html' and suffix != '.zip':
            return pack_html_source(filename, content, resources), '.zip', {'format': 'html-source-v1', 'entrypoint': Path(filename).name, 'resources_sha256': resources_fingerprint(resources or {})}
        if format_name == 'markdown':
            resources = resources if resources is not None else {}
            return pack_markdown_source(filename, content, resources), '.zip', {'format': 'markdown-source-v1', 'entrypoint': Path(filename).name, 'resources_sha256': resources_fingerprint(resources)}
        if format_name not in CAPABILITIES:
            if not collect:
                raise ValueError(f'不支持的输入：{filename}')
            return content, '.bin', None
        if format_name == 'url':
            return content, '.url', None
        return content, suffix or '.bin', None

    def _create_or_resume(self, job_id: str | None, filename: str, format_name: str, targets: tuple[str, ...], output_format: str | None, original_sha256: str) -> Job:
        if job_id is None:
            return self.catalog.create_job(str(uuid.uuid4()), filename, format_name, targets, output_format)
        try:
            uuid.UUID(job_id)
        except (ValueError, AttributeError) as error:
            raise ValueError(f'job_id 必须是 UUID：{job_id!r}') from error
        existing = self.get(job_id)
        if existing is None:
            return self.catalog.create_job(job_id, filename, format_name, targets, output_format)
        receipt = self.result(job_id) or {}
        source_receipt = receipt.get('source') or {}
        known = source_receipt.get('submitted_sha256') or source_receipt.get('original_sha256')
        if isinstance(known, str) and known and known != original_sha256:
            raise JobConflict('已绑定的 job 与本次输入指纹不一致')
        if existing.filename != filename or existing.input_format != format_name:
            raise JobConflict('已绑定的 job 与本次输入的文件名或格式不一致')
        return existing

    def _collect_stored(self, job: Job, *, content: bytes, stored: bytes, suffix: str, package: dict | None) -> Job:
        """Persist the intent, write and verify the blob, then register source."""
        filename = job.filename
        stored_sha = hashlib.sha256(stored).hexdigest()
        key = f'sources/{stored_sha}{suffix}'
        original_sha = hashlib.sha256(content).hexdigest()
        intent = {
            'schema_version': 1,
            'job_id': job.job_id,
            'filename': filename,
            'input_format': job.input_format,
            'storage_key': key,
            'source_sha256': stored_sha,
            'original_sha256': original_sha,
            'original_size_bytes': len(content),
            'package': package,
        }
        write_json(self._intent_path(job.job_id), intent)
        try:
            _, stored_key, digest = self.storage.store_bytes(stored, suffix)
            if stored_key != key or digest != stored_sha:
                raise ValueError('写入后的来源存储键或摘要不一致')
            job = self._register(job, stored_key, digest)
            if job.input_format not in CAPABILITIES:
                self.catalog.finish_saved_only(job.job_id, self._receipt_path(job.job_id).relative_to(self.storage.root).as_posix())
                job = self.get(job.job_id)
        except Exception as error:
            self._ingestion_failed(job, error)
        receipt = self._write_source_receipt(
            job, package=package, original_sha256=original_sha,
            original_size_bytes=len(content), stored_sha256=stored_sha, key=stored_key,
        )
        if job.status == JobStatus.SAVED_ONLY:
            receipt['status'] = 'saved_only'
            receipt['phase']['extraction'] = 'not_applicable'
            self._write_receipt(job.job_id, receipt)
        self._intent_path(job.job_id).unlink(missing_ok=True)
        return job

    def collect(self, filename: str, content: bytes, *, job_id: str | None = None, resources: dict[str, bytes] | None = None, targets: tuple[str, ...] = (), output_format: str | None = None) -> Job:
        """Accept and register a source before deciding whether to extract.

        Unknown formats are stored verbatim and end as ``saved_only``. Known
        formats are stored in the same way and remain pending extraction, which
        ``convert(..., job_id=...)`` performs while holding the job lease.
        """
        filename = self._safe_filename(filename)
        format_name = input_format(filename) or 'unknown'
        job = self._create_or_resume(job_id, filename, format_name, targets, output_format, hashlib.sha256(content).hexdigest())
        if job.source_id is not None:
            return job
        self._write_receipt(job.job_id, self._base_receipt(job))
        stored, suffix, package = self._stored_bytes(format_name, filename, content, resources=resources, collect=True)
        return self._collect_stored(job, content=content, stored=stored, suffix=suffix, package=package)

    def collect_path(self, path: Path | str, *, job_id: str | None = None, targets: tuple[str, ...] = (), output_format: str | None = None) -> Job:
        """Collect one local file, capturing the resources it references."""
        source = Path(path).expanduser().absolute()
        if not source.is_file():
            raise FileNotFoundError(f'输入文件不存在：{source}')
        return self.file_job(source, targets, job_id=job_id, output_format=output_format, collect=True)

    @classmethod
    def fingerprint(cls, path: Path | str) -> dict:
        """Prepared-input identities without creating a job or writing a blob."""
        source = Path(path).expanduser().absolute()
        if not source.is_file():
            raise FileNotFoundError(f'输入文件不存在：{source}')
        filename = cls._safe_filename(source.name)
        format_name = input_format(filename) or 'unknown'
        content = source.read_bytes()
        resources = None
        if format_name == 'markdown':
            resources = capture_resources(source, content)
        elif format_name == 'html' and source.suffix.lower() != '.zip':
            content, resources = capture_html_members(source)
        stored, _, package = cls._stored_bytes(format_name, filename, content, resources=resources, collect=True)
        return {
            'schema_version': 1,
            'input_format': format_name,
            'filename': filename,
            'input_sha256': hashlib.sha256(content).hexdigest(),
            'original_size_bytes': len(content),
            'package_sha256': hashlib.sha256(stored).hexdigest(),
            'resources_sha256': (package or {}).get('resources_sha256'),
            'package': package,
        }

    def reconcile(self) -> int:
        """Register blobs written before a crash; never invent a missing file."""
        root = self.storage.root / 'jobs'
        if not root.is_dir():
            return 0
        repaired = 0
        for intent_path in sorted(root.glob('*/intent.json')):
            try:
                intent = json.loads(intent_path.read_text(encoding='utf-8'))
                job_id = str(uuid.UUID(str(intent['job_id'])))
            except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
                continue
            job = self.get(job_id)
            if job is None:
                continue
            if job.source_id is not None:
                intent_path.unlink(missing_ok=True)
                continue
            try:
                path = self.storage.resolve(str(intent['storage_key']))
            except ValueError:
                continue
            if not path.is_file() or sha256_file(path) != intent.get('source_sha256'):
                continue
            job = self._register(job, str(intent['storage_key']), str(intent['source_sha256']))
            if job.input_format not in CAPABILITIES:
                self.catalog.finish_saved_only(job.job_id, self._receipt_path(job.job_id).relative_to(self.storage.root).as_posix())
                job = self.get(job.job_id)
            receipt = self._write_source_receipt(
                job, package=intent.get('package'),
                original_sha256=str(intent.get('original_sha256') or ''),
                original_size_bytes=int(intent.get('original_size_bytes') or 0),
                stored_sha256=str(intent['source_sha256']), key=str(intent['storage_key']),
            )
            if job.status == JobStatus.SAVED_ONLY:
                receipt['status'] = 'saved_only'
                receipt['phase']['extraction'] = 'not_applicable'
                self._write_receipt(job.job_id, receipt)
            intent_path.unlink(missing_ok=True)
            repaired += 1
        return repaired

    def upload(self, filename: str, content: bytes, format_name: str | None = None, *, collect_unknown: bool = False) -> Job:
        format_name = format_name or input_format(filename) or 'unknown'
        if format_name not in CAPABILITIES and not collect_unknown:
            filename = self._safe_filename(filename)
            job = self.catalog.create_job(str(uuid.uuid4()), filename, format_name, (), None)
            self._ingestion_failed(job, ValueError(f'不支持的输入：{filename}'))
        return self.collect(filename, content)

    def file_job(self, path: Path | str, targets: tuple[str, ...], *, job_id: str | None = None, output_format: str | None = None, collect: bool = False) -> Job:
        is_url = isinstance(path, str) and path.startswith(('http://', 'https://'))
        source = Path('webpage.url') if is_url else Path(path).expanduser().absolute()
        filename = self._safe_filename(source.name if not is_url else 'webpage.url')
        format_name = 'url' if is_url else input_format(filename) or 'unknown'
        if output_format is None:
            output_format = 'md_single' if 'markdown' in targets else 'pdf' if 'pdf' in targets else 'html'
        if format_name not in CAPABILITIES and not collect:
            job = self.catalog.create_job(str(uuid.uuid4()), filename, format_name, targets, output_format)
            try:
                validate_outputs(format_name, targets, output_format)
            except Exception as error:
                self._ingestion_failed(job, error)
        if format_name == 'url':
            content = path.encode('utf-8') if is_url else source.read_bytes()
            resources = None
        else:
            if not source.is_file():
                raise FileNotFoundError(f'输入文件不存在：{source}')
            content = source.read_bytes()
            resources = None
            if format_name == 'markdown':
                resources = capture_resources(source, content)
            elif format_name == 'html' and source.suffix.lower() != '.zip':
                content, resources = capture_html_members(source)
        return self.collect(filename, content, job_id=job_id, resources=resources, targets=targets, output_format=output_format)

    def queue(self, job_id: str, output_format: str) -> Job:
        job = self.get(job_id)
        if job is None:
            raise KeyError(job_id)
        target = OUTPUT_TARGET.get(output_format)
        validate_outputs(job.input_format, (target,) if target else (), output_format)
        if job.source_id is None or job.input_key is None:
            raise ValueError('任务没有保留的原件，请重新上传')
        if job.status.terminal:
            job = self.catalog.clone_terminal_job(job.job_id)
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
            submitted_sha = source.stem
            _, key, digest = self.storage.store_bytes(snapshot, '.zip')
            job = self._register(job, key, digest, token)
            # The captured snapshot replaces the address text as the durable
            # original. Record that identity now: the published export and any
            # later reader must agree on the ZIP's digest, never expose the
            # pre-fetch .url file as the user's original.
            receipt = self.result(job.job_id) or self._base_receipt(job)
            receipt['source'] = {
                'source_id': job.source_id,
                'storage_key': key,
                'source_sha256': digest,
                'original_sha256': digest,
                'original_size_bytes': len(snapshot),
                'package': {'format': 'url-source-v1', 'entrypoint': 'response.html'},
                'submitted_sha256': submitted_sha,
                'submitted_size_bytes': source.stat().st_size,
            }
            self._write_receipt(job.job_id, receipt)
            return self.source_path(job)
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
            alive = row['owner_pid'] is not None and _process_alive(row['owner_pid'])
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
        recovered += self.reconcile()
        return recovered

    def _reconcile_failure(self, job: Job, *, error: BaseException | None = None) -> None:
        report = self.storage.diagnostic_path(job.job_id)
        published = self.storage.exports_dir / job.export_id if job.export_id else None
        retained = self.storage.staging_dir / job.export_id if job.export_id else None
        if report.exists() and (published is None or not published.exists()):
            # Recovery can exit after writing the main diagnostic but before
            # replacing the quarantined bundle's former success report.
            if retained is not None and retained.is_dir():
                retained_report = retained / 'report.json'
                content = report.read_bytes()
                if not retained_report.is_file() or retained_report.read_bytes() != content:
                    write_json(retained_report, json.loads(content))
            return
        rollback_error = None
        if published is not None and published.exists():
            try:
                retained = self.storage.rollback_publish(job.export_id)
            except OSError as failure:
                if published.exists() or not retained.exists():
                    logger.exception('Could not quarantine failed export %s', job.export_id)
                    retained, rollback_error = published, str(failure)
        failure_report = {'schema_version': 2, 'job_id': job.job_id, 'export_id': job.export_id, 'status': 'failed', 'error': job.message, 'rollback_error': rollback_error, 'retained_dir': str(retained) if retained else None}
        if error is not None:
            failure_report['exception_chain'] = _exception_chain(error)
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

    def publish(self, report: dict, artifacts: list[ArtifactRecord], book: BookRevision | None = None, plan: ExportPlan | None = None, *, rebuilt_from_original: bool = True) -> Path:
        if self._heartbeat_error is not None:
            raise JobConflict(f'任务续约失败：{self._heartbeat_error}')
        if self.root is None or self.export_id is None:
            raise JobConflict('任务尚未创建导出')
        download_relative = prepare_download(self.root, self.job.output_format).relative_to(self.root).as_posix()
        rebuilt_revisions = self.runtime.rebuilt_revisions(book) if book is not None and rebuilt_from_original else ()
        self.published = self.runtime.storage.publish(self.export_id)
        revision_key = self.runtime.storage.preserve_revision(book.revision_id, self.published) if book is not None else None
        revision_hash = sha256_file(self.published / 'book.json') if book is not None else None
        current_export_id = None
        if book is not None:
            # Same Markdown keeps the existing directory current; a changed corpus moves the pointer.
            current_export_id = choose_current_export(self.runtime.catalog, self.runtime.storage, book.book_id, self.export_id, self.published, report['status'])
        try:
            self.runtime.catalog.publish_export(job_id=self.job_id, token=self.token, export_id=self.export_id, storage_key=self.published.relative_to(self.runtime.storage.root).as_posix(), final_status=report['status'], artifacts=artifacts, download_relative=download_relative, book=book, plan=plan, revision_storage_key=revision_key, revision_content_sha256=revision_hash, rebuilt_revisions=rebuilt_revisions, current_export_id=current_export_id)
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
            self.runtime._reconcile_failure(self.runtime.get(self.job_id), error=error)
            if not isinstance(error, Exception):
                return False
            raise ConversionFailure(self.job_id, report, error) from error
        finally:
            self._stop.set()
            if self._thread is not None:
                self._thread.join(timeout=5)
