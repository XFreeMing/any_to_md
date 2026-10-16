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


@dataclass(frozen=True)
class ConversionRequest:
    input_path: Path | str | None = None
    data_dir: Path | str = 'var'
    targets: tuple[str, ...] = ('markdown',)
    job_id: str | None = None


@dataclass(frozen=True)
class ConversionResult:
    job_id: str
    book_id: str
    edition_id: str
    revision_id: str
    export_id: str
    status: str
    output_dir: Path
    report_path: Path
    warning_count: int


def _semantic_snapshot_sha256(book: BookRevision) -> str:
    payload = book.to_dict()
    payload['revision_id'] = None
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')
    return hashlib.sha256(serialized).hexdigest()


def convert(request: ConversionRequest) -> ConversionResult:
    """Persist a job before reading, then publish only while owning its lease."""
    runtime = JobRuntime(request.data_dir)
    if request.job_id is not None:
        if request.input_path is not None:
            raise ValueError('job_id 与 input_path 不能同时提供')
        job = runtime.get(request.job_id)
        if job is None:
            raise KeyError(request.job_id)
    else:
        if request.input_path is None:
            raise ValueError('需要输入文件或已创建的 job_id')
        job = runtime.file_job(request.input_path, request.targets)
    with runtime.execute(job.job_id) as execution:
        reader, pipeline_version = CAPABILITIES[job.input_format].load_reader()
        source = runtime.source_path(job)
        source_id, book_id, edition_id = source_ids(job.input_format, source.stem)
        execution.progress(15, 'Reading source')
        reader_options = {'original_filename': job.filename} if job.input_format == 'html' else {}
        book = reader(
            source, fallback_title=Path(job.filename).stem,
            source_sha256=source.stem, source_id=source_id, book_id=book_id,
            edition_id=edition_id,
            revision_id=str(uuid.uuid5(uuid.UUID(edition_id), f'pending:{source.stem}:{pipeline_version}')),
            **reader_options,
        )
        semantic_sha256 = _semantic_snapshot_sha256(book)
        book.revision_id = str(uuid.uuid5(uuid.UUID(edition_id), f'revision:{pipeline_version}:{semantic_sha256}'))
        plan = create_export_plan(book)
        plan.targets = job.targets
        staging = execution.begin(plan.export_id)
        runtime.catalog.attach_revision(book=book, semantic_sha256=semantic_sha256, job_id=job.job_id, token=execution.token, plan=plan)
        execution.progress(50, 'Writing requested views')
        report, artifacts = write_bundle(book, plan, staging, job.targets, job_id=job.job_id)
        output_dir = execution.publish(report, artifacts, book, plan)
        return ConversionResult(
            job_id=job.job_id, book_id=book.book_id, edition_id=book.edition_id,
            revision_id=book.revision_id, export_id=plan.export_id,
            status=report['status'], output_dir=output_dir,
            report_path=output_dir / 'report.json', warning_count=report['summary']['warning_count'],
        )
