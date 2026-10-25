"""Real browser and isolated Web server operations shared by UC-04 scenarios."""

from __future__ import annotations

import os
import json
import socket
import subprocess
import sys
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator
from urllib.error import URLError
from urllib.request import urlopen

from tests.common.support import Blocked


@dataclass
class BrowserSession:
    page: Any
    context: Any
    base_url: str


@contextmanager
def browser_session(workdir: Path, *, upload_limit: int = 1024 * 1024) -> Iterator[BrowserSession]:
    """Start the actual application and Chrome, retaining logs and a trace."""
    try:
        from playwright.sync_api import Error, sync_playwright
    except ImportError as error:
        raise Blocked("缺少浏览器测试依赖；请先执行 uv sync --group test。") from error

    workdir.mkdir(parents=True, exist_ok=True)
    environment = os.environ.copy()
    environment.update(DATA_DIR=str(workdir / "data"), MAX_UPLOAD_SIZE=str(upload_limit))
    if environment.get("ANY_TO_MD_SCENARIO_OCR_URL"):
        environment["OCR_API_URL"] = environment["ANY_TO_MD_SCENARIO_OCR_URL"]
    root = Path(__file__).resolve().parents[2]
    import src

    installed = Path(src.__file__).resolve().is_relative_to(Path(sys.prefix).resolve())
    if installed:
        environment.pop("PYTHONPATH", None)
        environment.pop("PYTHONHOME", None)
    module_record = workdir / "server-modules.json"
    bootstrap = """
import json
import sys
from pathlib import Path
import uvicorn
import web.app

def record_modules():
    origins = {name: str(Path(module.__file__).resolve())
               for name, module in tuple(sys.modules.items())
               if name.split('.')[0] in {'src', 'web', 'pdf_to_md', 'ocr_to_md'}
               and getattr(module, '__file__', None)}
    Path(sys.argv[2]).write_text(json.dumps(origins, indent=2) + '\\n')
    if sys.argv[3] == 'installed':
        for name, path in origins.items():
            if not Path(path).is_relative_to(Path(sys.prefix).resolve()):
                raise RuntimeError(f'Web child imported source code: {name}: {path}')

record_modules()
try:
    uvicorn.run('web.app:app', fd=int(sys.argv[1]))
finally:
    record_modules()
"""
    with socket.socket() as listener, (workdir / "server.log").open("w", encoding="utf-8") as log:
        try:
            listener.bind(("127.0.0.1", 0))
        except PermissionError as error:
            raise Blocked("当前执行环境不允许启动本机 Web 监听端口，无法运行真实浏览器场景。") from error
        listener.listen()
        base_url = f"http://127.0.0.1:{listener.getsockname()[1]}"
        process = subprocess.Popen(
            [sys.executable, *(["-I"] if installed else []), "-c", bootstrap,
             str(listener.fileno()), str(module_record), "installed" if installed else "source"],
            cwd=workdir if installed else root,
            env=environment,
            pass_fds=(listener.fileno(),),
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        try:
            deadline = time.monotonic() + 30
            while True:
                if process.poll() is not None:
                    raise RuntimeError(f"Web 服务启动失败；查看 {workdir / 'server.log'}")
                try:
                    with urlopen(f"{base_url}/health", timeout=1) as response:
                        if response.status == 200:
                            break
                except (URLError, TimeoutError):
                    pass
                if time.monotonic() >= deadline:
                    raise RuntimeError(f"Web 服务未就绪；查看 {workdir / 'server.log'}")
                time.sleep(0.1)

            with sync_playwright() as playwright:
                channel = os.environ.get("ANY_TO_MD_BROWSER_CHANNEL", "chrome")
                options = {"channel": channel} if channel != "chromium" else {}
                try:
                    browser = playwright.chromium.launch(headless=True, **options)
                except Error as error:
                    raise Blocked(
                        f"无法启动真实浏览器 {channel}：{error}。可安装 Chrome，或执行 "
                        "uv run playwright install chromium 并设置 ANY_TO_MD_BROWSER_CHANNEL=chromium。"
                    ) from error
                context = browser.new_context(accept_downloads=True, viewport={"width": 1280, "height": 900})
                context.set_default_timeout(15000)
                context.tracing.start(screenshots=True, snapshots=True, sources=True)
                page = context.new_page()
                try:
                    page.goto(base_url, wait_until="domcontentloaded")
                    page.wait_for_function("!document.querySelector('#fileInput').disabled")
                    yield BrowserSession(page, context, base_url)
                finally:
                    try:
                        if not page.is_closed():
                            page.screenshot(path=str(workdir / "browser.png"), full_page=True)
                        context.tracing.stop(path=str(workdir / "trace.zip"))
                    finally:
                        context.close()
                        browser.close()
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            if installed:
                if not module_record.is_file():
                    raise RuntimeError("独立安装的 Web 子进程没有提供模块来源证据")
                origins = json.loads(module_record.read_text(encoding="utf-8"))
                outside = {name: path for name, path in origins.items()
                           if not Path(path).is_relative_to(Path(sys.prefix).resolve())}
                if outside:
                    raise RuntimeError(f"独立安装的 Web 子进程使用了源码：{outside}")


def write_html_sample(workdir: Path) -> Path:
    """A small input with independently authored content expectations in each scenario."""
    source = workdir / "browser-sample.html"
    source.write_text(
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<title>Browser scenario</title></head><body><article>'
        '<h1>Browser scenario</h1><p>Paragraph alpha: one visible source statement.</p>'
        '<h2 id="section-two">Second section</h2>'
        '<p>Paragraph beta: the final source statement.</p>'
        '</article></body></html>',
        encoding="utf-8",
    )
    return source


def upload(page: Any, source: Path) -> tuple[int, dict]:
    with page.expect_response(lambda response: response.url.endswith("/api/upload")) as event:
        page.locator("#fileInput").set_input_files(str(source))
    return event.value.status, event.value.json()


def convert(page: Any, output_label: str) -> tuple[dict, dict]:
    page.locator("#formatOptions").get_by_text(output_label, exact=True).click()
    with page.expect_response(lambda response: response.url.endswith("/api/convert")) as event:
        page.get_by_role("button", name="Start Conversion", exact=True).click()
    response = event.value
    return response.request.post_data_json, response.json()


def download(page: Any, destination: Path) -> Path:
    with page.expect_download() as event:
        page.get_by_role("button", name="Download Result", exact=True).click()
    result = event.value
    path = destination / result.suggested_filename
    destination.mkdir(parents=True, exist_ok=True)
    result.save_as(str(path))
    return path
