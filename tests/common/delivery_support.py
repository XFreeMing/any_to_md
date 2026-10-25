"""Shared fixtures and artifact observations for delivery scenarios."""

from __future__ import annotations

import functools
import errno
import hashlib
import io
import json
import os
import subprocess
import sys
import threading
from contextlib import contextmanager
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile

from tests.common.fixtures import build_fixtures, load_cases
from tests.common.inspection import inspect_output, inspect_pdf


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def tree_hashes(root: Path) -> dict[str, str]:
    return {path.relative_to(root).as_posix(): digest(path)
            for path in sorted(root.rglob('*')) if path.is_file()}


def reading_identity(path: Path) -> dict:
    """Ignore job metadata; compare reading bytes and PDF page pixels exactly."""
    suffixes = {'.md', '.html', '.htm', '.css', '.js', '.svg', '.png', '.jpg', '.jpeg',
                '.gif', '.webp', '.bmp', '.ttf', '.ttc', '.otf', '.woff', '.woff2',
                '.mp3', '.mp4', '.ogg', '.webm', '.wav', '.pdf'}
    files = [path] if path.is_file() else sorted(path.rglob('*'))
    result = {}
    for item in files:
        relative = item.name if path.is_file() else item.relative_to(path).as_posix()
        if not item.is_file() or (item.suffix.lower() not in suffixes and 'assets' not in Path(relative).parts):
            continue
        name = relative
        if item.suffix.lower() == '.pdf':
            import pymupdf
            with pymupdf.open(item) as document:
                pages = []
                for page in document:
                    pixels = page.get_pixmap(matrix=pymupdf.Matrix(1.5, 1.5), alpha=False)
                    payload = f'{pixels.width}:{pixels.height}:{pixels.n}:'.encode() + pixels.samples
                    pages.append(hashlib.sha256(payload).hexdigest())
            result[name] = {'page_pixels_sha256': pages}
        else:
            result[name] = {'sha256': digest(item)}
    assert result, f'没有可阅读产物：{path}'
    return result


def visual_identity(scenario_id: str, sources: list[Path], exports: list[Path],
                    required_checks: list[str]) -> dict:
    """Identify original samples and the actual content a reviewer reads."""
    assert sources and exports and required_checks, '人工复核必须关联样本、产物和检查项'
    return {
        'scenario': scenario_id,
        'sources': [{'name': path.name, 'sha256': digest(path)} for path in sources],
        'exports': [{'files': reading_identity(path)} for path in exports],
        'required_checks': required_checks,
    }


def validate_review(identity: dict, evidence_path: Path) -> dict:
    """Validate a human conclusion against the retained run, without conversion."""
    from tests.common.support import Blocked

    document = json.loads(evidence_path.read_text())
    reviews = document.get('reviews', [document])
    matches = [review for review in reviews if review.get('scenario') == identity['scenario']
               and review.get('sources') == identity['sources'] and review.get('exports') == identity['exports']]
    if not matches:
        raise Blocked(f'{identity["scenario"]} 尚无与本次样本、产物哈希一致的人工证据。')
    assert len(matches) == 1, '同一场景与产物不能有多份冲突复核结论'
    review = matches[0]
    assert all(isinstance(review.get(key), str) and review[key].strip()
               for key in ('reviewer', 'reviewed_at', 'conclusion')), '人工证据缺少署名、时间或具体结论'
    for name in identity['required_checks']:
        checks = [item for item in review.get('checks', []) if item.get('name') == name]
        assert len(checks) == 1 and checks[0].get('accepted') is True and checks[0].get('notes'), name
    return review


def require_visual_evidence(scenario_id: str, sources: list[Path], exports: list[Path],
                            required_checks: list[str], *, technical_checks: list[str]) -> None:
    """Call only after every technical path passed; retain pending reading review."""
    from tests.common.support import Blocked, VisualReviewRequired

    identity = visual_identity(scenario_id, sources, exports, required_checks)
    request = exports[0].parent / f'{scenario_id}-visual-requirements.json'
    request.write_text(json.dumps(identity, ensure_ascii=False, indent=2) + '\n')
    evidence_path = os.environ.get('ANY_TO_MD_VISUAL_EVIDENCE')
    try:
        if not evidence_path:
            raise Blocked(f'{scenario_id} 技术流程已执行；缺少关联当前样本与产物哈希的人工阅读结论。')
        validate_review(identity, Path(evidence_path))
    except Blocked as error:
        raise VisualReviewRequired(str(error), identity=identity, sources=sources, exports=exports,
                                   required_checks=required_checks, technical_checks=technical_checks) from error


def fixture_cases(root: Path) -> list[tuple[Path, dict]]:
    sources = build_fixtures(root)
    return [(sources[case['id']], case) for case in load_cases()]


def inspect_export(root: Path, case: dict, targets: tuple[str, ...]) -> list[dict]:
    """Check manifest integrity and return content observations for UC assertions."""
    manifest = json.loads((root / 'manifest.json').read_text())
    for item in manifest['files']:
        path = (root / item['path']).resolve()
        assert path.is_relative_to(root.resolve()), item
        assert path.is_file(), item
        assert path.stat().st_size == item['size_bytes'], item
        assert digest(path) == item['sha256'], item
    checks = []
    views = {'markdown': ('md_single', 'md_chapters'), 'html': ('html',), 'pdf': ('pdf',)}
    for target in targets:
        for view in views[target]:
            if view not in manifest['views']:
                # A one-document reader need not expose split downloads.
                assert view == 'md_chapters', manifest
                continue
            path = root / manifest['views'][view]
            paths = sorted((root / 'chapters').glob('*.md')) if view == 'md_chapters' else [path]
            result = inspect_pdf(path, case) if view == 'pdf' else inspect_output(paths, case, root)
            checks.append({'view': view, 'inspection': result,
                           'artifacts': {p.relative_to(root).as_posix(): digest(p) for p in paths}})
    return checks


def extract_download(content: bytes, destination: Path, filename: str) -> Path:
    destination.mkdir(parents=True)
    if content.startswith(b'PK'):
        with ZipFile(io.BytesIO(content)) as archive:
            for member in archive.infolist():
                assert (destination / member.filename).resolve().is_relative_to(destination.resolve())
            archive.extractall(destination)
        return destination
    (destination / filename).write_bytes(content)
    return destination


@contextmanager
def web_client(data: Path, **settings_override):
    from fastapi.testclient import TestClient
    from web.app import app
    from web.config import settings

    if os.environ.get('ANY_TO_MD_SCENARIO_OCR_URL'):
        settings_override.setdefault('OCR_API_URL', os.environ['ANY_TO_MD_SCENARIO_OCR_URL'])
    with patch.multiple(settings, DATA_DIR=str(data), **settings_override), TestClient(app) as client:
        yield client


@contextmanager
def source_server(directory: Path, port: int = 0):
    class Handler(SimpleHTTPRequestHandler):
        def log_message(self, *_args):
            pass

    try:
        server = ThreadingHTTPServer(('127.0.0.1', port), functools.partial(Handler, directory=str(directory)))
    except OSError as error:
        if error.errno not in (errno.EPERM, errno.EACCES, errno.EADDRINUSE):
            raise
        from tests.common.support import Blocked
        raise Blocked(f'本地 HTTP 端口不可用，无法执行真实 URL 来源场景：{error}') from error
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}'
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def subprocess_environment() -> dict[str, str]:
    import src

    environment = dict(os.environ)
    if environment.get('ANY_TO_MD_SCENARIO_OCR_URL'):
        environment['OCR_API_URL'] = environment['ANY_TO_MD_SCENARIO_OCR_URL']
    environment.pop('PYTHONHOME', None)
    environment.pop('PYTHONOPTIMIZE', None)
    if not Path(src.__file__).resolve().is_relative_to(Path(sys.prefix).resolve()):
        project = Path(__file__).resolve().parents[2]
        environment['PYTHONPATH'] = str(project)
    else:
        environment.pop('PYTHONPATH', None)
    return environment


def cli(arguments: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, '-m', 'src.interfaces.cli.convert_book', *arguments],
                          cwd=cwd, env=subprocess_environment(), capture_output=True, text=True, timeout=180)
