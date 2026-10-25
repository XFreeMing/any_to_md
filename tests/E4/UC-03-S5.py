"""UC-03-S5：运行条件不满足时准确解释失败（E4 / US-05）。

作为工具使用者，我希望了解转换缺少的条件，以便补齐条件并保留已有资料。
Given 真实 OCR 连接故障、已收到完整请求但不响应的 HTTP 服务、独立进程缺少 WeasyPrint。
When 经 Web API 提交图片 → Markdown 或 EPUB → PDF，重新打开服务查询结果。
Then 持久 failed；OCR 状态和诊断分别同时说明服务上下文及原因，诊断保留异常链；
无有效发布或下载，原件及早期成功修订、导出和下载保持。
受控 HTTP 只验证故障边界，不代表真实 OCR 质量或 PDF / 图片 Reader 迁移完成。
"""

if __name__ == "__main__" and not __package__:
    import runpy
    from pathlib import Path

    runpy.run_path(str(Path(__file__).resolve().parents[1] / "common" / "bootstrap.py"))

from contextlib import contextmanager
import hashlib
import importlib.abc
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from tests.common.delivery_support import digest, fixture_cases, subprocess_environment, tree_hashes


def _write(path: Path, value) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')


def _rows(data: Path) -> dict:
    with sqlite3.connect(data / 'catalog.sqlite') as connection:
        connection.row_factory = sqlite3.Row
        names = [row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        return {name: [dict(row) for row in connection.execute(f'SELECT * FROM "{name}" ORDER BY rowid')]
                for name in names}


@contextmanager
def _fault(condition: str, evidence: Path, source: Path):
    """Reserve a non-listening port, or withhold HTTP response after reading the body."""
    if condition == 'pdf':
        yield {}
        return
    if condition == 'connect':
        with socket.socket() as guard:
            guard.bind(('127.0.0.1', 0))
            url = f'http://127.0.0.1:{guard.getsockname()[1]}/api/v1/ocr/process'
            _write(evidence / 'http-fault.json', {'condition': condition, 'url': url, 'pid': os.getpid(),
                   'phase': 'bound_without_listen', 'timeout_seconds': 5,
                   'note': 'Real OS connection attempt; actual exception class is checked, not assumed to be refusal.'})
            yield {'OCR_API_URL': url, 'OCR_TIMEOUT': 5}
        return

    received, release, done = threading.Event(), threading.Event(), threading.Event()
    request_id = uuid.uuid4().hex
    events = []
    lock = threading.Lock()

    def emit(phase, **values):
        with lock:
            events.append({'phase': phase, 'monotonic': time.monotonic(), 'pid': os.getpid(), **values})
            _write(evidence / 'http-fault.json', {'condition': condition, 'request_id': request_id,
                   'timeout_seconds': 1, 'maximum_hold_seconds': 10, 'events': events})

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            try:
                self.connection.settimeout(5)
                body = self.rfile.read(int(self.headers['Content-Length']))
                emit('request_body_received', path=self.path, bytes=len(body),
                     body_sha256=hashlib.sha256(body).hexdigest(),
                     source_present=source.read_bytes() in body)
                received.set()
                emit('withholding_response')
                released = release.wait(10)
                emit('handler_released', released_by_test=released)
                # Deliberately send no status line, headers or body.
            finally:
                done.set()

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f'http://127.0.0.1:{server.server_port}/api/v1/ocr/process?request_id={request_id}'
    emit('listening', url=url)
    try:
        yield {'OCR_API_URL': url, 'OCR_TIMEOUT': 1}
        emit('client_finished', request_received=received.is_set())
        assert received.is_set(), events
        arrival = next(event for event in events if event['phase'] == 'request_body_received')
        assert arrival['source_present'] and request_id in arrival['path'], events
        assert events[-1]['monotonic'] - arrival['monotonic'] >= 0.8, events
        assert not done.is_set(), '服务必须仍在等待，不能用主动断开冒充读取超时'
    finally:
        release.set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        assert not thread.is_alive()
        if received.is_set():
            assert done.wait(5), '受控 HTTP handler 未退出'
        emit('service_stopped', thread_alive=thread.is_alive(), handler_done=done.is_set())


def _exercise(condition: str, source_arg: str, early_arg: str, directory: str) -> None:
    source, early, evidence = Path(source_arg), Path(early_arg), Path(directory)
    evidence.mkdir(parents=True)
    _write(evidence / 'process.json', {'pid': os.getpid(), 'python': sys.version,
           'executable': sys.executable, 'condition': condition, 'cwd': str(Path.cwd())})
    if condition == 'pdf':
        class MissingRenderer(importlib.abc.MetaPathFinder):
            def find_spec(self, fullname, path=None, target=None):
                if fullname == 'weasyprint' or fullname.startswith('weasyprint.'):
                    raise ModuleNotFoundError('PDF rendering dependency weasyprint is unavailable')
        sys.meta_path.insert(0, MissingRenderer())

    from src.application.jobs import JobRuntime
    from tests.common.delivery_support import web_client
    from tests.common.conversion_support import submit

    data = evidence / 'data'
    input_hashes = {str(path): digest(path) for path in (source, early)}
    with _fault(condition, evidence, source) as settings:
        with web_client(data, **settings) as client:
            old_state = submit(client, early)
            assert old_state['status'] in ('completed', 'completed_with_warnings'), old_state
            old_task = old_state['task_id']
            old_download = client.get('/api/download/' + old_task)
            assert old_download.status_code == 200
            (evidence / 'early-download.bin').write_bytes(old_download.content)
            before_files = {name: value for name, value in tree_hashes(data).items()
                            if not name.startswith('catalog.sqlite')}
            before_rows = _rows(data)
            _write(evidence / 'before.json', {'state': old_state, 'files': before_files, 'rows': before_rows,
                   'download_sha256': hashlib.sha256(old_download.content).hexdigest(), 'inputs': input_hashes})
            state = submit(client, source, 'pdf' if condition == 'pdf' else 'md_single')
            task = state['task_id']

        # Recreate the app lifespan and runtime: the failure must survive recovery/reopening.
        with web_client(data, **settings) as client:
            reopened = client.get('/api/status/' + task).json()
            download_status = client.get('/api/download/' + task).status_code
            old_after = client.get('/api/download/' + old_task)
            runtime = JobRuntime(data)
            job = runtime.get(task)
            diagnostic = json.loads(runtime.storage.resolve(job.report_key).read_text())
            after_rows = _rows(data)
            after_files = tree_hashes(data)
            stored_source = runtime.source_path(job)
            result = {'condition': condition, 'state': state, 'reopened_state': reopened,
                      'diagnostic': diagnostic, 'rows': after_rows, 'files': after_files,
                      'download_status': download_status, 'source_sha256': digest(stored_source),
                      'old_download_status': old_after.status_code,
                      'old_download_sha256': hashlib.sha256(old_after.content).hexdigest()}
            _write(evidence / 'failure.json', result)
            # These assertions apply to every Example, including the PDF dependency failure.
            assert state == reopened and state['status'] == 'failed', result
            assert state['download_url'] is None and download_status == 404, result
            assert job.status.value == 'failed' and diagnostic['status'] == 'failed', result
            assert job.download_relative is None and job.storage_key is None, job
            failed_exports = [row for row in after_rows['exports'] if row['job_id'] == task]
            assert failed_exports and all(row['status'] == 'failed' and row['storage_key'] is None
                                          and row['published_at'] is None for row in failed_exports), failed_exports
            export_ids = {row['export_id'] for row in failed_exports}
            assert not any(row['export_id'] in export_ids for row in after_rows['artifacts'])
            assert not any(row['export_id'] in export_ids for row in after_rows['export_locations'])
            assert all(not (data / 'exports' / export_id).exists() for export_id in export_ids)
            assert {name: after_files.get(name) for name in before_files} == before_files
            assert before_rows['revisions'], '早期成功资料必须包含持久修订'
            for table, rows in before_rows.items():
                assert all(row in after_rows[table] for row in rows), table
            assert after_rows['revisions'] == before_rows['revisions']
            assert old_after.status_code == 200 and old_after.content == old_download.content
            assert {str(path): digest(path) for path in (source, early)} == input_hashes
            assert digest(stored_source) == digest(source)

    # Keep all observed artifacts even when the stronger semantic assertions fail.
    chain = diagnostic.get('exception_chain', [])
    if condition == 'pdf':
        for message in (state['message'], diagnostic['error']):
            assert 'weasyprint' in message.lower(), message
            assert 'unavailable' in message.lower() or '缺少' in message, message
    else:
        expected_types = ('ConnectError', 'ConnectTimeout') if condition == 'connect' else ('ReadTimeout',)
        for message in (state['message'], diagnostic['error']):
            assert 'OCR' in message and '识别服务' in message, message
            assert any(name in message for name in expected_types), message
            assert ('连接' if condition == 'connect' else '读取超时') in message, message
        assert chain and chain[0]['type'] == 'src.adapters.ocr.OcrTransportError', chain
        assert chain[1]['type'] in tuple('httpx.' + name for name in expected_types), chain
        assert chain[1]['relation'] == 'cause' and chain[1]['message'], chain
        assert chain[1]['message'] in state['message'] and chain[1]['message'] in diagnostic['error']
        assert any(item['type'].startswith('httpcore.') for item in chain[2:]), chain
        assert len(chain) >= 4 and chain[-1]['message'], chain
        assert any(item['relation'] == 'context' for item in chain), chain


def run(workdir: Path) -> dict:
    samples = fixture_cases(workdir / 'inputs')
    paths = {case['id']: source for source, case in samples}
    worker = '''import runpy,sys
from pathlib import Path
scenario=Path(sys.argv[1])
runpy.run_path(str(scenario.parents[1]/'common'/'bootstrap.py'))
runpy.run_path(str(scenario),run_name='s5_worker')['_exercise'](*sys.argv[2:])
'''
    failures = []
    environment = subprocess_environment()
    # Only this subprocess bypasses inherited proxies for the controlled loopback faults.
    environment['NO_PROXY'] = ','.join(filter(None, [environment.get('NO_PROXY', environment.get('no_proxy', '')),
                                                    '127.0.0.1', 'localhost']))
    environment['no_proxy'] = environment['NO_PROXY']
    for condition, name in (('connect', 'image-ocr'), ('read-timeout', 'image-ocr'), ('pdf', 'epub-structured')):
        command = [sys.executable, '-c', worker, str(Path(__file__).resolve()), condition,
                   str(paths[name]), str(paths['epub-structured']), str(workdir / condition)]
        started = time.time()
        try:
            completed = subprocess.run(command, cwd=workdir, env=environment,
                                       capture_output=True, text=True, encoding='utf-8', timeout=60)
        except subprocess.TimeoutExpired as error:
            (workdir / f'{condition}-process.log').write_bytes((error.stdout or b'') + (error.stderr or b''))
            _write(workdir / f'{condition}-process.json', {'command': command, 'timeout_seconds': 60,
                   'started_at': started, 'outcome': 'process_timeout'})
            failures.append(condition + ': independent process exceeded 60 seconds')
            continue
        (workdir / f'{condition}-process.log').write_text(completed.stdout + completed.stderr, encoding='utf-8')
        _write(workdir / f'{condition}-process.json', {'command': command, 'cwd': str(workdir),
               'started_at': started, 'elapsed_seconds': time.time() - started, 'exit_code': completed.returncode})
        if completed.returncode:
            failures.append(condition + ': ' + completed.stdout + completed.stderr)
    assert not failures, '\n'.join(failures)
    return {'checks': ['真实 OCR 连接故障与已收到完整请求后的读取超时分别验证服务上下文、实际类别及底层异常链',
                       '独立进程缺少 WeasyPrint 时状态与持久诊断准确指出依赖',
                       '三例重新打开服务后仍 failed、下载 404，无有效发布或索引；原件和早期成功修订、导出及下载哈希不变'],
            'limitations': ['受控故障不代表真实 OCR 质量、PDF / 图片 Reader 迁移、CLI 或浏览器完整矩阵完成']}


if __name__ == "__main__":
    from tests.common.support import run_one

    raise SystemExit(run_one(__file__))
