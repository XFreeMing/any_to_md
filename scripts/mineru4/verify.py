"""TV-01-C3 download protocol checks; tiny local HTTP fixtures, no model quality claim.

Run with the isolated downloader project. Reports stay outside product acceptance.
The fixture wrapper changes only model identities, endpoint and state directory;
the actual Hub SDK, HTTP transport, CLI parsing and file verification run normally.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.metadata
import importlib.util
import json
import os
from pathlib import Path
import signal
import shutil
import subprocess
import sys
import tempfile
import threading
from urllib.parse import unquote

from filelock import FileLock
from huggingface_hub._local_folder import get_local_download_paths


ROOT = Path(__file__).resolve().parents[2]
REVISION = "a" * 40
PAYLOADS = {
    "fixture/small": {"layout/model.bin": b"small-model\n" * 100, "layout/config.json": b'{"test": true}\n'},
    "fixture/vlm": {"vlm.gguf": b"vlm\n" * 100},
    "fixture/title": {"title.gguf": b"title\n" * 100},
}
COUNTS = {"payload": 0, "metadata": 0, "range": 0}
BAD_PAYLOAD = threading.Event()
SLOW_PAYLOAD = threading.Event()
STREAM_STARTED = threading.Event()
RELEASE_STREAM = threading.Event()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args: object) -> None:
        pass

    def do_HEAD(self) -> None:
        self.serve(head=True)

    def do_GET(self) -> None:
        self.serve(head=False)

    def serve(self, *, head: bool) -> None:
        path = unquote(self.path.split("?", 1)[0])
        if path.startswith("/api/models/"):
            COUNTS["metadata"] += 1
            repo = path.removeprefix("/api/models/").split("/revision/")[0]
            siblings = []
            for name, data in PAYLOADS[repo].items():
                git_hash = hashlib.sha1(f"blob {len(data)}\0".encode() + data, usedforsecurity=False).hexdigest()
                item = {"rfilename": name, "size": len(data), "blobId": git_hash}
                if not name.endswith(".json"):
                    item["lfs"] = {"sha256": hashlib.sha256(data).hexdigest(), "size": len(data), "pointerSize": 128}
                siblings.append(item)
            body = json.dumps({"id": repo, "sha": REVISION, "siblings": siblings}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if not head:
                self.wfile.write(body)
            return
        repo, rest = path.lstrip("/").split("/resolve/", 1)
        revision, name = rest.split("/", 1)
        assert revision == REVISION
        data = PAYLOADS[repo][name]
        digest = hashlib.sha256(data).hexdigest() if not name.endswith(".json") else hashlib.sha1(f"blob {len(data)}\0".encode() + data, usedforsecurity=False).hexdigest()
        offset = int(self.headers.get("Range", "bytes=0-").removeprefix("bytes=").split("-", 1)[0])
        self.send_response(206 if offset else 200)
        self.send_header("ETag", f'"{digest}"')
        self.send_header("X-Repo-Commit", REVISION)
        self.send_header("Content-Length", str(len(data) - offset))
        if offset:
            self.send_header("Content-Range", f"bytes {offset}-{len(data)-1}/{len(data)}")
        self.end_headers()
        if not head:
            COUNTS["payload"] += 1
            COUNTS["range"] += bool(offset)
            if SLOW_PAYLOAD.is_set():
                STREAM_STARTED.set()
                RELEASE_STREAM.wait(15)
            try:
                self.wfile.write((b"x" * len(data) if BAD_PAYLOAD.is_set() else data)[offset:])
            except (BrokenPipeError, ConnectionResetError):
                pass


def run_checks(report_dir: Path) -> list[str]:
    passed = []
    with tempfile.TemporaryDirectory(prefix="mineru4-download-") as temporary:
        fixture = Path(temporary)
        state = fixture / "var" / "mineru4"
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        endpoint = f"http://127.0.0.1:{server.server_port}"
        wrapper = fixture / "protocol.py"
        wrapper.write_text(f'''
import importlib.util
import os
from pathlib import Path
import sys
from types import SimpleNamespace
spec = importlib.util.spec_from_file_location("download", {str(ROOT / 'scripts/mineru4/download.py')!r})
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
module.__file__ = {str(fixture / 'scripts/mineru4/download.py')!r}
module.ENDPOINTS = {{"huggingface": {endpoint!r}, "hf-mirror": {endpoint!r}}}
module.TITLE_REVISION = {REVISION!r}
module.TITLE_SHA256 = {hashlib.sha256(PAYLOADS['fixture/title']['title.gguf']).hexdigest()!r}
module.MODELS = (
    ("fixture/small", "small", ("layout",), None),
    ("fixture/vlm", "vlm", ("vlm.gguf",), None),
    ("fixture/title", "title", ("title.gguf",), {REVISION!r}),
)
if "FIXTURE_FREE_SPACE" in os.environ:
    module.shutil.disk_usage = lambda _path: SimpleNamespace(free=int(os.environ["FIXTURE_FREE_SPACE"]))
raise SystemExit(module.main())
''', encoding="utf-8")
        env = {key: value for key, value in os.environ.items() if not key.startswith(("HF_", "HUGGINGFACE_"))}
        env.update({"HF_HOME": str(fixture / "cache"), "HF_HUB_DISABLE_XET": "1", "HF_HUB_DISABLE_TELEMETRY": "1", "NO_PROXY": "127.0.0.1", "PYTHONUNBUFFERED": "1"})

        def run(label: str, *args: str, expected: int = 0) -> None:
            result = subprocess.run([sys.executable, str(wrapper), *args], env=env, text=True, capture_output=True, timeout=30)
            (report_dir / f"{label}.log").write_text(result.stdout + result.stderr, encoding="utf-8")
            assert result.returncode == expected, f"{label}: exit={result.returncode}\n{result.stdout}\n{result.stderr}"
            passed.append(label)

        def status() -> dict:
            return json.loads((state / "download-status.json").read_text())

        try:
            run("check-without-plan", "--check", expected=1)
            assert status()["status"] == "failed"
            run("dry-run", "--dry-run")
            assert COUNTS["payload"] == 0 and not (state / "models").exists()
            # Seed an interrupted SDK transfer; the pinned SDK must send Range.
            data = PAYLOADS["fixture/vlm"]["vlm.gguf"]
            paths = get_local_download_paths(state / "models" / "vlm", "vlm.gguf")
            paths.incomplete_path(hashlib.sha256(data).hexdigest()).write_bytes(data[:30])
            run("download-and-resume")
            assert COUNTS["range"] == 1
            assert status()["status"] == "verified" and not status()["runtime_verified"]
            assert len(status()["files"]) == 4
            assert not list(state.rglob(".mineru_complete"))
            history_path = state / "download-provenance.jsonl"
            history = history_path.read_bytes()
            before = dict(COUNTS)
            run("reuse-valid-files")
            run("offline-check", "--check")
            assert COUNTS == before, "valid files should need no network"
            assert history_path.read_bytes() == history, "reuse/check must retain source history"
            resumed_model = state / "models" / "vlm" / "vlm.gguf"
            resumed_model.unlink()
            paths.incomplete_path(hashlib.sha256(data).hexdigest()).write_bytes(data[:300])
            env["FIXTURE_FREE_SPACE"] = str(256 * 1024 * 1024 + len(data) - 300)
            run("resume-with-only-remaining-space")
            env.pop("FIXTURE_FREE_SPACE")
            assert COUNTS["range"] == 2
            damaged = state / "models" / "title" / "title.gguf"
            damaged.write_bytes(b"x" * damaged.stat().st_size)
            run("detect-same-size-corruption", "--check", expected=1)
            assert status()["status"] == "failed"
            run("repair-corruption")
            assert damaged.read_bytes() == PAYLOADS["fixture/title"]["title.gguf"]
            damaged.unlink()
            BAD_PAYLOAD.set()
            run("reject-corrupt-transfer", expected=1)
            assert status()["status"] == "failed"
            BAD_PAYLOAD.clear()
            run("retry-after-failure", "--source", "hf-mirror")
            assert history_path.read_bytes().startswith(history)
            assert status()["transfer_history_sha256"] == hashlib.sha256(history_path.read_bytes()).hexdigest()
            with FileLock(str(state / "download.lock")):
                run("concurrent-command", expected=1)
            assert status()["status"] == "verified", "lock loser must not overwrite active command state"
            damaged.unlink()
            run("detect-missing-file", "--check", expected=1)
            # Actual process interruption while the HTTP response is held open.
            SLOW_PAYLOAD.set()
            with (report_dir / "interrupt.log").open("w") as log:
                process = subprocess.Popen([sys.executable, str(wrapper)], env=env, stdout=log, stderr=log)
                try:
                    assert STREAM_STARTED.wait(10), "download did not start"
                    process.send_signal(signal.SIGINT)
                    assert process.wait(timeout=10) == 130
                finally:
                    RELEASE_STREAM.set()
                    if process.poll() is None:
                        process.kill()
                        process.wait()
            assert status()["status"] == "interrupted"
            passed.append("client-interrupt-130")
            SLOW_PAYLOAD.clear()
            run("retry-after-interrupt")
            # Copy only the entry point and locked downloader project, so the
            # actual launcher runs uv in an isolated checkout with fixture HTTP.
            project = fixture / "scripts" / "mineru4"
            project.mkdir(parents=True)
            for filename in ("pyproject.toml", "uv.lock"):
                shutil.copyfile(ROOT / "scripts/mineru4" / filename, project / filename)
            shutil.copyfile(ROOT / "pyproject.toml", fixture / "pyproject.toml")
            (project / "download.py").write_text(wrapper.read_text(), encoding="utf-8")
            launcher = fixture / "src/interfaces/cli/mineru4.py"
            launcher.parent.mkdir(parents=True)
            shutil.copyfile(ROOT / "src/interfaces/cli/mineru4.py", launcher)
            damaged.unlink()
            STREAM_STARTED.clear()
            RELEASE_STREAM.clear()
            SLOW_PAYLOAD.set()
            with (report_dir / "launcher-interrupt.log").open("w") as log:
                process = subprocess.Popen([sys.executable, str(launcher), "downlod"], env=env, stdout=log, stderr=log)
                try:
                    assert STREAM_STARTED.wait(15), "public launcher did not start HTTP download"
                    process.send_signal(signal.SIGINT)
                    assert process.wait(timeout=15) == 130
                finally:
                    RELEASE_STREAM.set()
                    if process.poll() is None:
                        process.kill()
                        process.wait()
            assert status()["status"] == "interrupted"
            with FileLock(str(state / "download.lock"), timeout=2):
                assert not damaged.exists(), "interrupted launcher must not leave transfer running"
            passed.append("public-launcher-interrupt-130")
            SLOW_PAYLOAD.clear()
            for command, options in (("download", []), ("downlod", []), ("download", ["--check"])):
                label = f"public-{command}{'-check' if options else ''}"
                result = subprocess.run([sys.executable, str(launcher), command, *options], env=env, text=True, capture_output=True, timeout=30)
                (report_dir / f"{label}.log").write_text(result.stdout + result.stderr, encoding="utf-8")
                assert result.returncode == 0 and status()["status"] == "verified"
                passed.append(label)
            plan = json.loads((state / "download-plan.json").read_text())
            plan["models"][0]["files"][0]["path"] = "../../outside"
            (state / "download-plan.json").write_text(json.dumps(plan))
            run("reject-unsafe-plan", "--check", expected=1)
            assert status()["status"] == "failed"
            run("reject-unknown-source", "--source", "other", expected=2)
        finally:
            RELEASE_STREAM.set()
            server.shutdown()
            server.server_close()
            worker.join(timeout=5)
    return passed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--real-metadata", action="store_true", help="同时核对真实 Hugging Face 清单；不下载权重")
    args = parser.parse_args()
    report_dir = ROOT / "var" / "quality" / "mineru4" / "download" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    report_dir.mkdir(parents=True)
    report = {
        "technical_item": "TV-01-C3", "scope": "controlled HTTP download protocol; no real weights or inference",
        "huggingface_hub": importlib.metadata.version("huggingface-hub"),
        "sources": {str(path): hashlib.sha256((ROOT / path).read_bytes()).hexdigest() for path in (
            Path("TODO-TECH.md"), Path("pyproject.toml"), Path("uv.lock"), Path("src/interfaces/cli/mineru4.py"),
            Path("scripts/mineru4/download.py"), Path("scripts/mineru4/verify.py"),
            Path("scripts/mineru4/pyproject.toml"), Path("scripts/mineru4/uv.lock"),
        )},
    }
    try:
        report["checks"] = run_checks(report_dir)
        if args.real_metadata:
            spec = importlib.util.spec_from_file_location("download", ROOT / "scripts/mineru4/download.py")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            plan = module.build_plan("https://huggingface.co")
            module.validate_plan(plan)
            report["real_metadata"] = plan
        for command in ("download", "downlod"):
            result = subprocess.run([sys.executable, str(ROOT / "src/interfaces/cli/mineru4.py"), command, "--help"], text=True, capture_output=True, timeout=10)
            assert result.returncode == 0 and "--check" in result.stdout
            report["checks"].append(f"entry-{command}-help")
        report["status"] = "passed"
    except Exception as exc:
        report.update({"status": "failed", "error": str(exc)})
        raise
    finally:
        (report_dir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"协议证据：{report_dir}")
    print(f"{len(report['checks'])} 项下载协议检查通过；真实权重与推理未验证。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
