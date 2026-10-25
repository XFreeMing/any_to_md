"""Capture existing converter behaviour without retaining a legacy code copy.

Run against a Git archive with --source-tree. All large input/output files and
private-book diagnostics stay under the ignored --output directory. A compact
public report can be copied separately; it contains only self-authored cases.
"""

from __future__ import annotations

if __name__ == "__main__" and not __package__:
    import runpy
    from pathlib import Path

    runpy.run_path(str(Path(__file__).resolve().with_name("bootstrap.py")))

import argparse
import asyncio
import hashlib
import importlib.abc
import importlib.machinery
import importlib.metadata
import json
import sys
import time
import traceback
from pathlib import Path
from unittest.mock import patch

from tests.common.fixtures import MANIFEST, build_fixtures, load_cases
from tests.common.inspection import inspect_output, inspect_pdf

SOURCE_PACKAGES = (
    "any_to_md", "web", "epub_to_md", "epub_to_html", "html_to_md",
    "html_to_pdf", "docx_to_md", "url_to_md", "pdf_to_md", "ocr_to_md",
)


class _SourceTreeFinder(importlib.abc.MetaPathFinder):
    """Fail closed instead of falling back to an editable/current installation."""

    def __init__(self, root: Path):
        self.root = root.resolve()

    def find_spec(self, fullname, path=None, target=None):
        if fullname.split(".")[0] not in SOURCE_PACKAGES:
            return None
        search = path if path is not None else [str(self.root / "src"), str(self.root)]
        if any(not Path(directory).resolve().is_relative_to(self.root) for directory in search):
            raise ImportError(f"Converter import outside selected source tree: {fullname}")
        spec = importlib.machinery.PathFinder.find_spec(fullname, search, target)
        if spec is None or spec.origin is None or not Path(spec.origin).resolve().is_relative_to(self.root):
            raise ImportError(f"Converter absent from selected source tree: {fullname}")
        return spec


def _module_origins(root: Path) -> dict:
    origins = {}
    for name, module in sys.modules.copy().items():
        if name.split(".")[0] not in SOURCE_PACKAGES or module is None:
            continue
        filename = getattr(module, "__file__", None)
        if filename is None or not Path(filename).resolve().is_relative_to(root):
            raise RuntimeError(f"Baseline isolation violated by module: {name}")
        path = Path(filename).resolve()
        origins[name] = {"path": str(path.relative_to(root)), "sha256": _digest(path)}
    return origins


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _inspect(paths: list[Path], case: dict, root: Path) -> dict:
    if len(paths) == 1 and paths[0].suffix == ".pdf":
        return inspect_pdf(paths[0], case)
    return inspect_output(paths, case, root)


def _record(case: dict, lane: str, target: str, output: Path, action) -> dict:
    started = time.perf_counter()
    entry = {"case": case["id"], "lane": lane, "target": target}
    if lane == "html-cli":
        entry["entry_scope"] = "CLI conversion library; command-line parsing not exercised"
    try:
        outcome = action()
        paths, root = outcome[:2]
        if len(outcome) > 2:
            entry.update(outcome[2])
        entry.update({
            "execution": "completed",
            "artifacts": [str(path.relative_to(output)) for path in paths],
            "artifact_sha256": [_digest(path) for path in paths],
            "quality": _inspect(paths, case, root),
        })
        if entry.get("download_path_resolves") is False:
            entry["quality"]["passed"] = False
            entry["quality"]["failures"].append("Web download filename does not resolve to the actual artifact")
    except Exception as error:
        entry.update({"execution": "error", "error_type": type(error).__name__, "error": str(error)})
        trace_path = output / "errors" / f"{case['id']}-{lane}-{target}.txt"
        trace_path.parent.mkdir(exist_ok=True)
        trace_path.write_text(traceback.format_exc(), encoding="utf-8")
    entry["elapsed_seconds"] = round(time.perf_counter() - started, 4)
    print(f"{entry['case']} / {lane} / {target}: {entry['execution']} "
          f"quality={entry.get('quality', {}).get('passed', 'unverified')}", flush=True)
    return entry


def _new_book(source: Path, root: Path):
    from any_to_md.application.convert_book import convert_book

    result = convert_book(source, root)
    return [result.output_dir / "book.md"], result.output_dir


def _old_md(source: Path, root: Path):
    from epub_to_md import convert_epub_to_single_markdown

    root.mkdir(parents=True)
    path = root / "book.md"
    convert_epub_to_single_markdown(source, output_file=path, assets_dir=root / "assets")
    return [path], root


def _html_pipeline(source: Path, root: Path):
    from epub_to_html import convert_epub
    from html_to_md import convert_html_to_markdown_single

    path = convert_epub(source, root / "source")
    result = convert_html_to_markdown_single(path, output_root=root / "markdown")
    return [result.output_path], root / "markdown"


def _html_chapters(source: Path, root: Path):
    from html_to_md import convert_html_to_markdown_chapters

    documents = convert_html_to_markdown_chapters(source, output_root=root)
    return [document.output_path for document in documents], root


def _web(source: Path, case: dict, target: str, root: Path):
    from web.config import settings
    from web.services.converter import ConversionService
    from web.services.file_manager import FileManager
    from web.services.task_manager import task_manager

    with patch.object(settings, "TEMP_DIR", str(root)):
        # The production URL upload stores the URL as task.filename while writing
        # url.txt. Exercise that behaviour instead of silently correcting it here.
        filename = "https://quality.invalid/article.html" if case["kind"] == "url" else source.name
        task = task_manager.create_task(filename, case["kind"])
        manager = FileManager(task.task_id)
        if case["kind"] == "url":
            manager.save_upload("url.txt", b"https://quality.invalid/article.html")
        else:
            manager.save_upload(source.name, source.read_bytes())
        try:
            result = asyncio.run(ConversionService(task, manager).convert(target))
            details = {"entry_scope": "Web conversion service; HTTP transport not exercised", "download_path_resolves": manager.get_output_path(task.download_filename).resolve() == result.resolve()}
            if target == "md_chapters":
                import zipfile

                unpacked = manager.output_dir / "unpacked"
                with zipfile.ZipFile(result) as archive:
                    # Only consume generated self-authored outputs, not arbitrary
                    # third-party ZIP responses.
                    archive.extractall(unpacked)
                paths = sorted(unpacked.rglob("*.md"))
                return paths, unpacked, details
            return [result], manager.output_dir, details
        finally:
            task_manager.delete_task(task.task_id)


def _url_snapshot(source: Path, root: Path, synchronous: bool):
    import httpx
    from url_to_md.converter import fetch_url_to_markdown, fetch_url_to_markdown_sync

    html = source.read_text(encoding="utf-8")
    original_sync, original_async = httpx.Client, httpx.AsyncClient
    transport = httpx.MockTransport(lambda request: httpx.Response(200, text=html))
    with patch("httpx.Client", side_effect=lambda **kwargs: original_sync(transport=transport, **kwargs)), patch(
        "httpx.AsyncClient", side_effect=lambda **kwargs: original_async(transport=transport, **kwargs)
    ):
        url = "https://quality.invalid/article.html"
        content, _ = fetch_url_to_markdown_sync(url) if synchronous else asyncio.run(fetch_url_to_markdown(url))
    root.mkdir(parents=True)
    path = root / "article.md"
    path.write_text(content, encoding="utf-8")
    return [path], root


def _public_report(report: dict) -> dict:
    """Exclude machine-local paths, timings and tracebacks from checked-in data."""
    entries = []
    for entry in report["results"]:
        item = {key: value for key, value in entry.items() if key not in {"artifacts", "artifact_sha256", "elapsed_seconds", "error"}}
        entries.append(item)
    return {key: value for key, value in report.items() if key not in {"results", "output"}} | {"results": entries}


def capture(args: argparse.Namespace) -> int:
    tool_paths = {
        "quality_contract_sha256": MANIFEST,
        "inspection_source_sha256": Path(__file__).with_name("inspection.py"),
        "fixture_builder_sha256": Path(__file__).with_name("fixtures.py"),
        "capture_source_sha256": Path(__file__),
    }
    tool_hashes = {key: _digest(path) for key, path in tool_paths.items()}
    source_tree = args.source_tree.resolve()
    for package in SOURCE_PACKAGES:
        directory = source_tree / "src" / package if package == "any_to_md" else source_tree / package
        if not (directory / "__init__.py").is_file():
            raise ValueError(f"Incomplete baseline source tree: {package}")
    if any(name.split(".")[0] in SOURCE_PACKAGES for name in sys.modules):
        raise RuntimeError("Start capture in a fresh process, before importing converters")
    # Resolve converter imports from the archived version, not an editable install.
    sys.meta_path.insert(0, _SourceTreeFinder(source_tree))
    sys.path[:0] = [str(source_tree / "src"), str(source_tree)]
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    fixtures = build_fixtures(output / "fixtures")
    results = []
    from web.services.file_manager import get_available_outputs

    for case in load_cases():
        source = fixtures[case["id"]]
        base = output / case["id"]
        if case["kind"] == "epub":
            for lane, action in (("book", _new_book), ("epub-md", _old_md), ("epub-html-md", _html_pipeline)):
                results.append(_record(case, lane, "md_single", output, lambda action=action, lane=lane: action(source, base / lane)))
        if case["kind"] == "html":
            results.append(_record(case, "html-cli", "md_chapters", output, lambda: _html_chapters(source, base / "chapters")))
        if case["kind"] == "url":
            for synchronous in (False, True):
                lane = "url-sync" if synchronous else "url-async"
                entry = _record(case, lane, "md_single", output, lambda synchronous=synchronous, lane=lane: _url_snapshot(source, base / lane, synchronous))
                entry["transport"] = "immutable_snapshot_mock; live acquisition not verified"
                results.append(entry)
        for target in get_available_outputs(case["kind"]):
            if target == "pdf" and not args.render_pdf:
                results.append({"case": case["id"], "lane": "web", "target": target, "execution": "not_run", "reason": "PDF rendering not requested"})
            elif case.get("requires_ocr"):
                results.append({"case": case["id"], "lane": "web", "target": target, "execution": "not_run", "reason": "Real OCR service and independent recognition validation required"})
            else:
                results.append(_record(case, "web", target, output, lambda target=target: _web(source, case, target, base / f"web-{target}")))
    from pdf_to_md.detector import get_pdf_info, is_scanned_pdf

    detection = {
        case_id: {"is_scanned_pdf": is_scanned_pdf(fixtures[case_id]), "get_pdf_info": get_pdf_info(fixtures[case_id])}
        for case_id in ("pdf-native", "pdf-scanned", "pdf-mixed")
    }
    # Metadata alone does not prove isolation: record every converter source hash.
    source_hashes = {
        str(path.relative_to(source_tree)): _digest(path)
        for directory in ("src", "web", "epub_to_md", "epub_to_html", "html_to_md", "html_to_pdf", "docx_to_md", "url_to_md", "pdf_to_md", "ocr_to_md")
        for path in sorted((source_tree / directory).rglob("*.py"))
    }
    if tool_hashes != {key: _digest(path) for key, path in tool_paths.items()}:
        raise RuntimeError("Quality tools or contract changed during capture; rerun in a fresh output directory")
    report = {
        "schema_version": 1,
        "source_revision": args.revision,
        **tool_hashes,
        "source_sha256": source_hashes,
        "loaded_converter_modules": _module_origins(source_tree),
        "fixture_sha256": {case_id: _digest(path) for case_id, path in fixtures.items()},
        "python": sys.version,
        "dependencies": {name: importlib.metadata.version(name) for name in ("ebooklib", "beautifulsoup4", "markdownify", "weasyprint", "mammoth", "trafilatura", "pymupdf4llm")},
        "output": str(output),
        "pdf_detection": detection,
        "results": results,
        "limitations": ["Self-authored structural fixtures do not prove real-book/OCR quality", "Visual checks remain not_run", "URL extraction snapshots do not verify HTTP acquisition", "No live OCR requests made", "CLI argument parsing and HTTP routes are not exercised by the capture runner"],
    }
    (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (output / "public-report.json").write_text(json.dumps(_public_report(report), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Report: {output / 'report.json'}", flush=True)
    return 0  # A recorded legacy failure is evidence, not a crashed capture run.


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-tree", type=Path, required=True)
    parser.add_argument("--revision", required=True, help="Git revision used to create the source archive")
    parser.add_argument("--output", type=Path, required=True, help="New directory, normally under ignored var/quality")
    parser.add_argument("--render-pdf", action="store_true")
    return capture(parser.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
