"""Input fixtures and public-entry transport for PDF/image scenarios."""

from __future__ import annotations

from contextlib import contextmanager
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
from zipfile import ZipFile, ZIP_DEFLATED

from tests.scenarios.support import Blocked


@contextmanager
def ocr_endpoint(body: bytes = b"unavailable", status: int = 503):
    """Controlled external HTTP response; never a substitute for real OCR quality."""
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            requests.append(self.rfile.read(int(self.headers.get("Content-Length", 0))))
            self.send_response(status)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    try:
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    except OSError as error:
        raise Blocked(f"场景需要监听本地 OCR 测试端口：{error}") from error
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/api/v1/ocr/process", requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@contextmanager
def api(data_dir: Path, ocr_url: str | None = None):
    from fastapi.testclient import TestClient
    from web.app import app
    from web.config import settings

    before = settings.DATA_DIR, settings.OCR_API_URL, settings.OCR_TIMEOUT
    settings.DATA_DIR = str(data_dir)
    if ocr_url is not None:
        settings.OCR_API_URL = ocr_url
    settings.OCR_TIMEOUT = 10
    try:
        with TestClient(app) as client:
            yield client
    finally:
        settings.DATA_DIR, settings.OCR_API_URL, settings.OCR_TIMEOUT = before


def submit(client, source: Path, output: str = "md_single") -> dict:
    uploaded = client.post("/api/upload", files={"file": (source.name, source.read_bytes())})
    assert uploaded.status_code == 200, uploaded.text
    converted = client.post("/api/convert", json={"task_id": uploaded.json()["task_id"], "output_format": output})
    assert converted.status_code == 200, converted.text
    response = client.get(f"/api/status/{converted.json()['task_id']}")
    assert response.status_code == 200, response.text
    return response.json()


def published(data_dir: Path, task_id: str) -> Path:
    from any_to_md.application.jobs import JobRuntime

    runtime = JobRuntime(data_dir)
    job = runtime.get(task_id)
    assert job is not None and job.storage_key, "任务没有发布产物"
    return runtime.storage.resolve(job.storage_key)


def diagnostic(data_dir: Path, task_id: str) -> dict:
    from any_to_md.application.jobs import JobRuntime

    runtime = JobRuntime(data_dir)
    job = runtime.get(task_id)
    assert job is not None and job.report_key, "任务没有可追溯诊断"
    return json.loads(runtime.storage.resolve(job.report_key).read_text())


def archive(members: dict[str, str | bytes]) -> bytes:
    stream = io.BytesIO()
    with ZipFile(stream, "w", compression=ZIP_DEFLATED) as bundle:
        for name, value in members.items():
            bundle.writestr(name, value)
    return stream.getvalue()


def image_source(path: Path, text: str = "IMAGE_OCR_601 source text") -> Path:
    from PIL import Image, ImageDraw

    picture = Image.new("RGB", (900, 240), "white")
    draw = ImageDraw.Draw(picture)
    draw.text((20, 20), text, fill="black", font_size=28)
    draw.rectangle((20, 100, 260, 210), outline="black", width=2)
    draw.line((140, 100, 140, 210), fill="black", width=2)
    draw.text((35, 125), "ITEM", fill="black", font_size=20)
    draw.text((155, 125), "42", fill="black", font_size=20)
    picture.save(path)
    return path


def pdf_source(path: Path, *, mixed: bool = False, illustration: bool = False) -> tuple[Path, list[str]]:
    import pymupdf
    from PIL import Image

    markers = ["NATIVE_PAGE_1"]
    with pymupdf.open() as document:
        page = document.new_page(width=600, height=600)
        page.insert_text((40, 60), markers[0])
        if illustration:
            stream = io.BytesIO()
            Image.new("RGB", (30, 30), "red").save(stream, format="PNG")
            page.insert_image(pymupdf.Rect(50, 120, 100, 170), stream=stream.getvalue())
        if mixed:
            image = image_source(path.with_suffix(".png"), "SCANNED_PAGE_2")
            document.new_page(width=600, height=600).insert_image(pymupdf.Rect(20, 20, 580, 170), filename=str(image))
            markers.append("SCANNED_PAGE_2")
            image_source(image, "MIXED_IMAGE_3")
            page = document.new_page(width=600, height=600)
            page.insert_text((40, 40), "MIXED_NATIVE_3")
            page.insert_image(pymupdf.Rect(20, 100, 580, 250), filename=str(image))
            markers.extend(["MIXED_NATIVE_3", "MIXED_IMAGE_3"])
            document.new_page(width=600, height=600)
        document.save(path, no_new_id=True)
    return path, markers


def real_ocr_url() -> str:
    value = os.environ.get("ANY_TO_MD_SCENARIO_OCR_URL")
    if not value:
        raise Blocked("需要 ANY_TO_MD_SCENARIO_OCR_URL 指向真实 OCR；模拟响应不能证明识别质量")
    return value


def export_matrix(workdir: Path, source: Path, ocr_url: str):
    """Run each public transport/output combination; assertions stay in the scenario."""
    import any_to_md

    workdir.mkdir(parents=True, exist_ok=True)
    installed = Path(any_to_md.__file__).resolve().is_relative_to(Path(sys.prefix).resolve())
    for entry in ("web", "cli", "batch"):
        for target, output in (("markdown", "md_single"), ("html", "html"), ("pdf", "pdf")):
            data_dir = workdir / entry / target
            if entry == "web":
                with api(data_dir, ocr_url) as client:
                    state = submit(client, source, output)
                    assert state["status"] in {"completed", "completed_with_warnings"}, state
                    assert client.get(state["download_url"]).status_code == 200
                    export = published(data_dir, state["task_id"])
            else:
                environment = dict(os.environ, OCR_API_URL=ocr_url, OCR_TIMEOUT="10")
                command = [sys.executable]
                if installed:
                    command.append("-I")
                    environment.pop("PYTHONPATH", None)
                else:
                    environment["PYTHONPATH"] = str(Path(any_to_md.__file__).resolve().parents[1])
                command += ["-m", "any_to_md.interfaces.cli.convert_book"]
                if entry == "batch":
                    inputs = data_dir.parent / f"{target}-input"
                    inputs.mkdir(parents=True)
                    shutil.copy2(source, inputs / source.name)
                    command += ["--input-dir", str(inputs)]
                else:
                    command += ["--input", str(source)]
                result = subprocess.run(command + ["--target", target, "--data-dir", str(data_dir)],
                                        cwd=workdir, env=environment, capture_output=True, text=True, timeout=120)
                assert result.returncode == 0, f"{entry}/{target}: {result.stdout}\n{result.stderr}"
                exports = list((data_dir / "exports").iterdir())
                assert len(exports) == 1, exports
                export = exports[0]
            yield entry, target, export


def rejected_cli(source: Path, workdir: Path, ocr_url: str, expected: tuple[str, ...]) -> None:
    """A rejected request must explain the same input/service failure in both CLI forms."""
    import any_to_md

    installed = Path(any_to_md.__file__).resolve().is_relative_to(Path(sys.prefix).resolve())
    environment = dict(os.environ, OCR_API_URL=ocr_url, OCR_TIMEOUT="10")
    command = [sys.executable, *(["-I"] if installed else []), "-m", "any_to_md.interfaces.cli.convert_book"]
    if installed:
        environment.pop("PYTHONPATH", None)
    else:
        environment["PYTHONPATH"] = str(Path(any_to_md.__file__).resolve().parents[1])
    inputs = workdir / "input"
    inputs.mkdir(parents=True)
    shutil.copy2(source, inputs / source.name)
    for entry, arguments in (("cli", ["--input", str(source)]), ("batch", ["--input-dir", str(inputs)])):
        for target in ("markdown", "html", "pdf"):
            data = workdir / entry / target
            result = subprocess.run(command + arguments + ["--target", target, "--data-dir", str(data)],
                                    cwd=workdir, env=environment, capture_output=True, text=True, timeout=60)
            message = result.stdout + result.stderr
            assert result.returncode == 1, (entry, target, message)
            assert any(value.lower() in message.lower() for value in expected), (entry, target, message)
            assert not list((data / "exports").glob("*"))


def failure_matrix(workdir: Path, source: Path, ocr_url: str):
    """Observe failed/partial jobs from each entry, without mocking application logic."""
    import any_to_md
    from any_to_md.application.jobs import JobRuntime

    installed = Path(any_to_md.__file__).resolve().is_relative_to(Path(sys.prefix).resolve())
    command = [sys.executable, *(["-I"] if installed else []), "-m", "any_to_md.interfaces.cli.convert_book"]
    environment = dict(os.environ, OCR_API_URL=ocr_url, OCR_TIMEOUT="10")
    if installed:
        environment.pop("PYTHONPATH", None)
    else:
        environment["PYTHONPATH"] = str(Path(any_to_md.__file__).resolve().parents[1])
    inputs = workdir / "inputs"
    inputs.mkdir(parents=True)
    shutil.copy2(source, inputs / source.name)
    for entry in ("web", "cli", "batch"):
        for target, output in (("markdown", "md_single"), ("html", "html"), ("pdf", "pdf")):
            data = workdir / entry / target
            if entry == "web":
                with api(data, ocr_url) as client:
                    state = submit(client, source, output)
                    assert state["status"] in {"failed", "partial"}, state
                    report = diagnostic(data, state["task_id"])
                    if state["status"] == "failed":
                        assert state["download_url"] is None
            else:
                arguments = ["--input", str(source)] if entry == "cli" else ["--input-dir", str(inputs)]
                result = subprocess.run(command + arguments + ["--target", target, "--data-dir", str(data)],
                                        cwd=workdir, env=environment, capture_output=True, text=True, timeout=60)
                assert result.returncode in {1, 2}, result.stdout + result.stderr
                reports = list((data / "jobs").glob("*/report.json"))
                assert len(reports) == 1, "输入被静默跳过或未保留任务诊断"
                report = json.loads(reports[0].read_text())
                job = JobRuntime(data).get(report["job_id"])
                assert job and job.status.value in {"failed", "partial"}
            yield entry, target, report


def body_text(export: Path, target: str) -> str:
    manifest = json.loads((export / "manifest.json").read_text())
    view = manifest["views"][{"markdown": "md_single", "html": "html", "pdf": "pdf"}[target]]
    path = export / view
    if target == "pdf":
        import pymupdf

        with pymupdf.open(path) as document:
            return "\n".join(page.get_text() for page in document)
    from bs4 import BeautifulSoup
    text = path.read_text()
    if target == "markdown":
        import markdown

        text = markdown.markdown(text, extensions=["tables", "fenced_code"])
    return BeautifulSoup(text, "html.parser").get_text(" ", strip=True)


def file_hashes(directory: Path) -> dict[str, str]:
    return {str(path.relative_to(directory)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in directory.rglob("*") if path.is_file()}
