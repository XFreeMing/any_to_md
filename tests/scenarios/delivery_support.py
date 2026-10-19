"""Shared fixtures and artifact observations for delivery scenarios."""

from __future__ import annotations

import functools
import base64
import errno
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import threading
from contextlib import contextmanager
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile

from tests.quality.fixtures import _archive, build_fixtures, load_cases
from tests.quality.inspection import inspect_output, inspect_pdf


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
    from tests.scenarios.support import Blocked

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
    from tests.scenarios.support import Blocked, VisualReviewRequired

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


def assert_export(root: Path, case: dict, targets=('markdown', 'html')) -> list[dict]:
    """Inspect actual exported views against the independent source manifest."""
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
            assert result['passed'], {'view': view, 'failures': result['failures']}
            checks.append({'view': view, 'artifacts': {p.relative_to(root).as_posix(): digest(p) for p in paths}})
    assert checks
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
        from tests.scenarios.support import Blocked
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
    import any_to_md

    environment = dict(os.environ)
    if environment.get('ANY_TO_MD_SCENARIO_OCR_URL'):
        environment['OCR_API_URL'] = environment['ANY_TO_MD_SCENARIO_OCR_URL']
    environment.pop('PYTHONHOME', None)
    environment.pop('PYTHONOPTIMIZE', None)
    if not Path(any_to_md.__file__).resolve().is_relative_to(Path(sys.prefix).resolve()):
        project = Path(__file__).resolve().parents[2]
        environment['PYTHONPATH'] = os.pathsep.join((str(project / 'src'), str(project)))
    else:
        environment.pop('PYTHONPATH', None)
    return environment


def cli(arguments: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, '-m', 'any_to_md.interfaces.cli.convert_book', *arguments],
                          cwd=cwd, env=subprocess_environment(), capture_output=True, text=True, timeout=180)


def installed_matrix(workdir: Path, wheel: Path, source_root: Path) -> dict:
    """Called only by an isolated interpreter; never re-enters the scenario runner."""
    from any_to_md.application.capabilities import CAPABILITIES, OUTPUT_TARGET
    from any_to_md.application.jobs import JobRuntime
    import any_to_md

    prefix = Path(sys.prefix).resolve()
    package = Path(any_to_md.__file__).resolve().parent
    assert package.is_relative_to(prefix), '运行的核心不来自独立安装环境'
    source_dirs = {'any_to_md': source_root / 'src/any_to_md',
                   **{name: source_root / name for name in ('web', 'pdf_to_md', 'ocr_to_md')}}
    site_packages = package.parent
    with ZipFile(wheel) as archive:
        members = set(archive.namelist())
        for name, directory in source_dirs.items():
            for source in directory.rglob('*'):
                if not source.is_file() or '__pycache__' in source.parts or source.suffix == '.pyc':
                    continue
                relative = name + '/' + source.relative_to(directory).as_posix()
                assert relative in members, f'最终源码不在 wheel：{relative}'
                assert archive.read(relative) == source.read_bytes(), f'wheel 与最终源码不同：{relative}'
                assert (site_packages / relative).read_bytes() == source.read_bytes(), f'安装内容不同：{relative}'
        for relative in members:
            top = relative.split('/', 1)[0]
            if top in source_dirs and relative.endswith('.py'):
                assert (source_dirs[top] / relative.split('/', 1)[1]).is_file(), f'wheel 残留旧源码：{relative}'
    fixtures = fixture_cases(workdir / 'inputs')
    from tests.quality.docx_fixtures import structured_docx
    structured = workdir / 'inputs' / 'structured.docx'
    structured_case = structured_docx(structured)
    fixtures.append((structured, dict(structured_case, id='docx-structured', kind='docx')))
    observations, reviewed_sources, reviewed_exports = [], [], []
    with source_server(workdir / 'inputs', int(os.environ.get('ANY_TO_MD_FIXTURE_PORT', '18763'))) as base:
        samples = []
        for source, case in fixtures:
            if case['kind'] == 'html':
                # A raw HTML upload can only carry its own bytes; make its images
                # self-contained. External local-resource portability is in S4.
                body = source.read_text()
                for resource in (source.parent / 'assets').glob('*.png'):
                    embedded = 'data:image/png;base64,' + base64.b64encode(resource.read_bytes()).decode()
                    body = body.replace('assets/' + resource.name, embedded)
                source.write_text(body)
            if case['kind'] == 'url':
                pointer = source.with_suffix('.url')
                pointer.write_text(base + '/' + source.name)
                source = pointer
            samples.append((source, case))
        # All advertised extensions are exercised, including alternate image encodings.
        from PIL import Image
        for kind, capability in CAPABILITIES.items():
            representative, case = next((p, c) for p, c in samples if c['kind'] == kind)
            present = {p.suffix for p, c in samples if c['kind'] == kind}
            for suffix in sorted(set(capability.suffixes) - present):
                variant = representative.with_name('variant' + suffix)
                if kind == 'image':
                    with Image.open(representative) as picture:
                        picture.convert('RGB').save(variant)
                elif suffix == '.zip':
                    members = {representative.name: representative.read_bytes()}
                    members.update({resource.relative_to(representative.parent).as_posix(): resource.read_bytes()
                                    for resource in sorted((representative.parent / 'assets').rglob('*'))
                                    if resource.is_file()})
                    _archive(variant, members)
                else:
                    shutil.copyfile(representative, variant)
                samples.append((variant, dict(case, id=case['id'] + suffix)))
        for index, (source, case) in enumerate(samples):
            if case.get('requires_ocr') and not os.environ.get('ANY_TO_MD_SCENARIO_OCR_URL'):
                from tests.scenarios.support import Blocked
                raise Blocked('真实识别验收需要 ANY_TO_MD_SCENARIO_OCR_URL，不使用默认地址替代。')
            reviewed_sources.append(str(source))
            capability = CAPABILITIES[case['kind']]
            for entry in ('web', 'cli', 'batch'):
                for view in capability.outputs:
                    target = OUTPUT_TARGET[view]
                    if target == 'pdf':
                        try:
                            import weasyprint  # noqa: F401
                        except (ImportError, OSError) as error:
                            from tests.scenarios.support import Blocked
                            raise Blocked(f'独立安装环境缺少可用 PDF 渲染依赖：{error}') from error
                    data = workdir / f'{index}-{entry}-{view}'
                    if entry == 'web':
                        with web_client(data) as client:
                            upload = client.post('/api/upload', files={'file': (source.name, source.read_bytes())})
                            assert upload.status_code == 200, upload.text
                            job_id = upload.json()['task_id']
                            conversion = client.post('/api/convert', json={'task_id': job_id, 'output_format': view})
                            assert conversion.status_code == 200, conversion.text
                            job_id = conversion.json()['task_id']
                            state = client.get('/api/status/' + job_id).json()
                            assert state['status'] in ('completed', 'completed_with_warnings'), state
                            download = client.get(state['download_url'])
                            assert download.status_code == 200 and download.content
                    else:
                        if entry == 'batch':
                            batch = workdir / f'batch-{index}-{view}'
                            batch.mkdir()
                            shutil.copyfile(source, batch / source.name)
                            if case['kind'] == 'html' and source.suffix != '.zip':
                                shutil.copytree(source.parent / 'assets', batch / 'assets')
                            arguments = ['--input-dir', str(batch)]
                        else:
                            arguments = ['--input', str(source)]
                        command = cli([*arguments, '--data-dir', str(data), '--target', target], workdir)
                        assert command.returncode == 0, command.stdout + command.stderr
                        import sqlite3
                        with sqlite3.connect(data / 'catalog.sqlite') as connection:
                            rows = connection.execute('SELECT job_id FROM jobs').fetchall()
                        assert len(rows) == 1, rows
                        job_id = rows[0][0]
                    runtime = JobRuntime(data)
                    job = runtime.get(job_id)
                    assert job and job.status.downloadable
                    export = runtime.storage.resolve(job.storage_key)
                    assert (export / 'book.json').is_file(), f'{case["kind"]} 未接入共同正文'
                    observed = assert_export(export, case, (target,))
                    reviewed_exports.append(str(export))
                    observations.append({'case': case['id'], 'entry': entry, 'view': view,
                                         'source_sha256': digest(source), 'views': observed})
    origins = {}
    for name, module in tuple(sys.modules.items()):
        if name.split('.')[0] in source_dirs and getattr(module, '__file__', None):
            path = Path(module.__file__).resolve()
            assert path.is_relative_to(prefix), f'源码泄漏到安装环境：{name}: {path}'
            origins[name] = str(path)
    return {'wheel_sha256': digest(wheel), 'python': sys.executable, 'observations': observations,
            'sources': reviewed_sources, 'exports': reviewed_exports,
            'production_modules': origins}
