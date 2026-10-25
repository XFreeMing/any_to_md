"""Publishable artifacts from one content revision and one export plan."""

from __future__ import annotations

from pathlib import Path
from dataclasses import replace

from ..domain.artifacts import ArtifactRecord, ExportPlan
from ..domain.book import BookRevision
from ..writers.html import write_html
from ..writers.markdown import write_markdown
from ..writers.pdf import render_pdf
from .downloads import write_downloads
from .files import file_record, write_json


def write_bundle(book: BookRevision, plan: ExportPlan, root: Path, targets: tuple[str, ...], *, job_id: str) -> tuple[dict, list[ArtifactRecord]]:
    book.validate()
    root.mkdir(parents=True, exist_ok=True)
    assets = root / "assets"
    assets.mkdir(exist_ok=True)
    for asset in book.assets.values():
        (assets / asset.file_name).write_bytes(asset.content)
    if "markdown" in targets:
        write_markdown(book, plan, root)
    if "html" in targets or "pdf" in targets:
        html = write_html(book, plan, root)
        if "pdf" in targets:
            render_pdf(html, output_path=root / "book.pdf")
    write_json(root / "book.json", book.to_dict())
    write_json(root / "toc.json", {
        "schema_version": 2, "revision_id": book.revision_id, "roots": book.roots,
        "nodes": [node.to_dict() for node in book.nodes.values()],
    })
    write_json(root / "locations.json", {
        "schema_version": 2, "export_id": plan.export_id, "revision_id": book.revision_id,
        "nodes": {node_id: {
            **({"single": vars(plan.single_locations[node_id]), "split": vars(plan.split_locations[node_id])} if "markdown" in targets else {}),
            **({"html": {"path": "book.html", "anchor": node.anchor}} if {"html", "pdf"}.intersection(targets) else {}),
        } for node_id, node in book.nodes.items()},
    })
    errors = [item for item in book.diagnostics if item.severity == "error"]
    status = "partial" if errors else "completed_with_warnings" if book.diagnostics else "completed"
    report = {
        "schema_version": 2, "status": status, "job_id": job_id, "book_id": book.book_id,
        "revision_id": book.revision_id, "export_id": plan.export_id,
        "pipeline_version": book.pipeline_version, "targets": targets,
        "summary": {"section_count": len(book.nodes), "fragment_count": len(book.content), "asset_count": len(book.assets), "warning_count": sum(item.severity == "warning" for item in book.diagnostics), "error_count": len(errors)},
        "diagnostics": [item.to_dict() for item in book.diagnostics],
        "limitations": ["Markdown 中原始 HTML 的显示取决于阅读器；PDF 是重排版视图。"],
    }
    return report, finalize_bundle(root, report, targets, plan.export_id, book.revision_id)


def finalize_bundle(root: Path, report: dict, targets: tuple[str, ...], export_id: str, revision_id: str | None = None, views: dict[str, str] | None = None) -> list[ArtifactRecord]:
    if views is None:
        views = {}
        if 'markdown' in targets:
            views.update(md_single='book.md', md_chapters='TOC.md')
        views.update({target: 'book.' + target for target in ('html', 'pdf') if target in targets})
    write_json(root / 'report.json', report)
    write_downloads(root, report['status'] == 'partial', views)
    paths = sorted(root.rglob('*'))
    if any(path.is_symlink() or not path.resolve().is_relative_to(root.resolve()) for path in paths):
        raise ValueError('导出包含符号链接或越界产物')
    records = [file_record(path, root) for path in paths if path.is_file()]
    roles = {'md_single': 'markdown_single', 'md_chapters': 'human_toc', 'html': 'html', 'pdf': 'pdf'}
    overrides = {path: roles[view] for view, path in views.items()}
    records = [replace(record, role=overrides.get(record.relative_path, record.role)) for record in records]
    primary = next(iter(views.values()))
    write_json(root / 'manifest.json', {
        'schema_version': 2, 'export_id': export_id, 'revision_id': revision_id,
        'primary_artifact': primary,
        'views': views,
        'files': [{'role': item.role, 'path': item.relative_path, 'sha256': item.sha256, 'size_bytes': item.size_bytes, 'media_type': item.media_type} for item in records],
    })
    records.append(file_record(root / 'manifest.json', root))
    return records
