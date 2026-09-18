"""Orchestrate the M01 EPUB book conversion workflow."""

from __future__ import annotations

import hashlib
import json
import mimetypes
import uuid
from dataclasses import dataclass
from pathlib import Path

from ..adapters.persistence.sqlite import SQLiteCatalog
from ..adapters.storage import LocalStorage
from ..domain.artifacts import ArtifactRecord
from ..domain.book import BookRevision
from ..export.planning import create_export_plan
from ..readers.epub import PIPELINE_VERSION, read_epub_book
from ..writers.markdown import artifact_role, write_markdown_bundle

_APPLICATION_NAMESPACE = uuid.UUID("ca7eb847-13d5-48a3-b0ca-44d59fedb155")


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


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _stable_id(namespace: uuid.UUID, value: str) -> str:
    return str(uuid.uuid5(namespace, value))


def _semantic_snapshot_sha256(book: BookRevision) -> str:
    payload = book.to_dict()
    payload["revision_id"] = None
    serialized = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(serialized).hexdigest()


def _artifact_records(output_dir: Path) -> list[ArtifactRecord]:
    records: list[ArtifactRecord] = []
    for path in sorted(item for item in output_dir.rglob("*") if item.is_file()):
        relative = path.relative_to(output_dir).as_posix()
        media_type = mimetypes.guess_type(path.name)[0]
        if path.suffix.lower() == ".md":
            media_type = "text/markdown"
        elif path.suffix.lower() == ".json":
            media_type = "application/json"
        records.append(
            ArtifactRecord(
                role=artifact_role(relative),
                relative_path=relative,
                sha256=_sha256_file(path),
                size_bytes=path.stat().st_size,
                media_type=media_type or "application/octet-stream",
            )
        )
    return records


def convert_book(input_path: Path | str, data_dir: Path | str = "var") -> ConversionResult:
    """Convert one EPUB into a versioned whole/split Markdown bundle."""
    source = Path(input_path).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(f"输入文件不存在：{source}")
    if source.suffix.lower() != ".epub":
        raise ValueError("M01 当前只支持 EPUB 输入")

    data_root = Path(data_dir).expanduser().resolve()
    storage = LocalStorage(data_root)
    catalog = SQLiteCatalog(data_root / "catalog.sqlite")
    source_sha256 = _sha256_file(source)
    stored_source, source_storage_key = storage.store_source(source, source_sha256)

    source_id = _stable_id(_APPLICATION_NAMESPACE, f"source:epub:{source_sha256}")
    book_id = _stable_id(_APPLICATION_NAMESPACE, f"book:{source_sha256}")
    edition_id = _stable_id(uuid.UUID(book_id), f"edition:{source_sha256}")
    provisional_revision_id = _stable_id(
        uuid.UUID(edition_id), f"pending:{source_sha256}:{PIPELINE_VERSION}"
    )
    job_id = str(uuid.uuid4())

    book = read_epub_book(
        stored_source,
        fallback_title=source.stem,
        source_sha256=source_sha256,
        source_id=source_id,
        book_id=book_id,
        edition_id=edition_id,
        revision_id=provisional_revision_id,
    )
    semantic_sha256 = _semantic_snapshot_sha256(book)
    book.revision_id = _stable_id(
        uuid.UUID(edition_id),
        f"revision:{PIPELINE_VERSION}:{semantic_sha256}",
    )
    plan = create_export_plan(book)
    catalog.begin_export(
        book=book,
        source_storage_key=source_storage_key,
        original_filename=source.name,
        semantic_sha256=semantic_sha256,
        job_id=job_id,
        plan=plan,
    )
    staging = storage.staging_dir / plan.export_id
    output_dir: Path | None = None
    try:
        staging = storage.create_staging(plan.export_id)
        report = write_markdown_bundle(book, plan, staging)
        output_dir = storage.publish(plan.export_id)
        revision_storage_key = storage.preserve_revision(book.revision_id, output_dir)
        revision_content_sha256 = _sha256_file(output_dir / "book.json")
        artifacts = _artifact_records(output_dir)
        catalog.publish_export(
            book=book,
            job_id=job_id,
            plan=plan,
            storage_key=output_dir.relative_to(data_root).as_posix(),
            revision_storage_key=revision_storage_key,
            revision_content_sha256=revision_content_sha256,
            final_status=str(report["status"]),
            artifacts=artifacts,
        )
    except Exception as exc:
        retained_dir = staging
        rollback_error: str | None = None
        if output_dir and output_dir.exists():
            try:
                retained_dir = storage.rollback_publish(plan.export_id)
                output_dir = None
            except Exception as rollback_exc:
                retained_dir = output_dir
                rollback_error = str(rollback_exc)
        failure_report = {
            "schema_version": 1,
            "status": "failed",
            "job_id": job_id,
            "export_id": plan.export_id,
            "error": str(exc),
            "rollback_error": rollback_error,
        }
        if retained_dir.exists():
            (retained_dir / "report.json").write_text(
                json.dumps(failure_report, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        try:
            catalog.fail_export(job_id=job_id, export_id=plan.export_id, error=str(exc))
        except Exception:
            pass
        raise RuntimeError(f"{exc}；诊断文件保留在：{retained_dir}") from exc

    assert output_dir is not None

    return ConversionResult(
        job_id=job_id,
        book_id=book.book_id,
        edition_id=book.edition_id,
        revision_id=book.revision_id,
        export_id=plan.export_id,
        status=str(report["status"]),
        output_dir=output_dir,
        report_path=output_dir / "report.json",
        warning_count=int(report["summary"]["warning_count"]),
    )
