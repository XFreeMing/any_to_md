"""The shared conversion application used by CLI, Web and batch callers."""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from pathlib import Path

from ..domain.book import BookRevision
from ..export.planning import create_export_plan
from ..export.bundle import write_bundle
from .capabilities import CAPABILITIES
from .jobs import JobRuntime, source_ids
from ..domain.jobs import JobStatus


@dataclass(frozen=True)
class ConversionRequest:
    input_path: Path | str | None = None
    data_dir: Path | str = 'var'
    targets: tuple[str, ...] = ('markdown',)
    job_id: str | None = None
    collect: bool = False


@dataclass(frozen=True)
class ConversionResult:
    job_id: str
    book_id: str | None
    edition_id: str | None
    revision_id: str | None
    export_id: str | None
    status: str
    output_dir: Path | None
    report_path: Path | None
    warning_count: int
    receipt: dict | None = None

    @property
    def saved_only(self) -> bool:
        return self.status == 'saved_only'


def _semantic_snapshot_sha256(book: BookRevision) -> str:
    payload = book.to_dict()
    payload['revision_id'] = None
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')
    return hashlib.sha256(serialized).hexdigest()


def _result_from_job(runtime: JobRuntime, job_id: str) -> ConversionResult:
    receipt = runtime.result(job_id) or {}
    conversion = receipt.get('conversion') or {}
    return ConversionResult(
        job_id=job_id,
        book_id=conversion.get('book_id'),
        edition_id=conversion.get('edition_id'),
        revision_id=conversion.get('revision_id'),
        export_id=conversion.get('export_id'),
        status=str(receipt.get('status') or 'saved_only'),
        output_dir=None,
        report_path=None,
        warning_count=0,
        receipt=receipt,
    )


def convert(request: ConversionRequest) -> ConversionResult:
    """Persist a job before reading, then publish only while owning its lease."""
    runtime = JobRuntime(request.data_dir)
    if request.job_id is not None:
        if request.input_path is not None:
            # Collection callers bind their request id to the job before the
            # input is touched, so the receipt is addressable even if the
            # process dies later. Extraction-only callers address a job that
            # was already collected.
            if not request.collect:
                raise ValueError('job_id 与 input_path 不能同时提供')
            job = runtime.file_job(
                request.input_path,
                request.targets,
                job_id=request.job_id,
                collect=True,
            )
        else:
            job = runtime.get(request.job_id)
        if job is None:
            raise KeyError(request.job_id)
    else:
        if request.input_path is None:
            raise ValueError('需要输入文件或已创建的 job_id')
        job = runtime.file_job(request.input_path, request.targets, collect=request.collect)
    if job.status == JobStatus.SAVED_ONLY:
        return _result_from_job(runtime, job.job_id)
    if job.source_id is None:
        runtime.reconcile()
        job = runtime.get(job.job_id)
        if job is None or job.source_id is None:
            raise ValueError('任务没有已登记的原件，需重新提交输入')
    if job.status in {JobStatus.COMPLETED, JobStatus.COMPLETED_WITH_WARNINGS, JobStatus.PARTIAL}:
        return _result_from_job(runtime, job.job_id)
    try:
        return _extract(runtime, job)
    except Exception as error:
        runtime.record_failed(job.job_id, error)
        raise


def _extract(runtime: JobRuntime, job) -> ConversionResult:
    with runtime.execute(job.job_id) as execution:
        book = None
        if job.selected_revision_id is not None:
            record = runtime.catalog.revision_record(job.selected_revision_id,job.source_id)
            execution.progress(15,'Loading selected published revision')
            book = runtime.storage.read_revision(record)
        reused = book is not None
        if not reused:
            reader, pipeline_version = CAPABILITIES[job.input_format].load_reader()
            source = runtime.capture_source(job, execution.token)
            source_id, book_id, edition_id = source_ids(job.input_format, source.stem)
            execution.progress(15, 'Reading source')
            reader_options = {'original_filename': job.filename} if job.input_format in {'html', 'markdown', 'text'} else {}
            book = reader(
                source, fallback_title=Path(job.filename).stem,
                source_sha256=source.stem, source_id=source_id, book_id=book_id,
                edition_id=edition_id,
                revision_id=str(uuid.uuid5(uuid.UUID(edition_id), f'pending:{source.stem}:{pipeline_version}')),
                **reader_options,
            )
        semantic_sha256 = _semantic_snapshot_sha256(book)
        if reused:
            if semantic_sha256 != record['semantic_sha256']:
                raise ValueError('持久正文语义身份校验失败')
        else:
            book.revision_id = str(uuid.uuid5(uuid.UUID(book.edition_id), f'revision:{pipeline_version}:{semantic_sha256}'))
        plan = create_export_plan(book)
        plan.targets = job.targets
        staging = execution.begin(plan.export_id)
        runtime.catalog.attach_revision(book=book, semantic_sha256=semantic_sha256, job_id=job.job_id, token=execution.token, plan=plan)
        execution.progress(50, 'Writing requested views')
        report, artifacts = write_bundle(book, plan, staging, job.targets, job_id=job.job_id)
        output_dir = execution.publish(report, artifacts, book, plan, rebuilt_from_original=not reused)
        receipt = runtime.record_published(job.job_id, book=book, plan=plan, report=report, export_dir=output_dir)
        return ConversionResult(
            job_id=job.job_id, book_id=book.book_id, edition_id=book.edition_id,
            revision_id=book.revision_id, export_id=plan.export_id,
            status=report['status'], output_dir=output_dir,
            report_path=output_dir / 'report.json', warning_count=report['summary']['warning_count'],
            receipt=receipt,
        )
