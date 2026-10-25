"""UC-03-S6：发布中断后恢复且保留历史下载（E5 / US-05）。

作为工具使用者，我希望中断任务有可核对结果，以便继续读取早期成功资料。
Given 已有成功 EPUB 导出，另一次转换在产物落盘、成功状态提交前进程终止。
When 重启实际 Web 运行时执行恢复并查询两个任务。
Then 中断任务失败且诊断完整，未提交产物被隔离，早期下载及文件哈希不变。
And 另一仍持有有效租约的转换进程不被探测终止，恢复后可继续提交并下载。

恢复再次中断分别覆盖失败事务提交后、隔离后、主诊断写入后；成功提交后注入异常。
"""

if __name__ == "__main__" and not __package__:
    import runpy
    from pathlib import Path

    runpy.run_path(str(Path(__file__).resolve().parents[1] / "common" / "bootstrap.py"))

import json
import hashlib
import os
import sqlite3
import subprocess
import sys
import time
import traceback
from contextlib import closing
from pathlib import Path

from src.application.convert import ConversionRequest, convert
from tests.common.delivery_support import fixture_cases, subprocess_environment, tree_hashes, web_client


def save_evidence(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def snapshot(data: Path) -> dict:
    with closing(sqlite3.connect(data / 'catalog.sqlite')) as connection:
        connection.row_factory = sqlite3.Row
        database = {table: [dict(row) for row in connection.execute(f'SELECT * FROM {table} ORDER BY rowid')]
                    for table in ('jobs', 'exports', 'artifacts')}
    return {'database': database,
            'hashes': {name: tree_hashes(data / name)
                       for name in ('sources', 'revisions', 'exports', 'staging', 'jobs')},
            'reports': {path.relative_to(data).as_posix(): json.loads(path.read_text(encoding='utf-8'))
                        for name in ('exports', 'staging', 'jobs')
                        for path in sorted((data / name).rglob('report.json'))}}


def web_restart(data: Path, directory: Path, label: str) -> dict:
    """Observe actual startup recovery, HTTP status and downloaded bytes."""
    downloads = directory / label
    downloads.mkdir()
    with web_client(data) as client:
        with closing(sqlite3.connect(data / 'catalog.sqlite')) as connection:
            job_ids = [row[0] for row in connection.execute('SELECT job_id FROM jobs ORDER BY rowid')]
        responses = {}
        for job_id in job_ids:
            state = client.get('/api/status/' + job_id)
            download = client.get('/api/download/' + job_id)
            record = {'http_status': state.status_code, 'state': state.json(),
                      'download_status': download.status_code}
            if download.status_code == 200:
                path = downloads / (job_id + '.zip')
                path.write_bytes(download.content)
                record.update(download_file=str(path), download_sha256=hashlib.sha256(download.content).hexdigest())
            responses[job_id] = record
    return {**snapshot(data), 'http': responses}


def boundary_process(script: str, arguments: list[str], directory: Path, label: str) -> dict:
    completed = subprocess.run([sys.executable, '-c', script, *arguments], cwd=directory,
                               env=subprocess_environment(), capture_output=True, text=True, timeout=60)
    log = directory / (label + '.log')
    log.write_text(completed.stdout + completed.stderr, encoding='utf-8')
    record = {'exit_code': completed.returncode, 'log': str(log), 'arguments': arguments}
    save_evidence(directory / (label + '-process.json'), record)
    return record


def check_exception_window(source: Path, directory: Path, window: str) -> None:
    directory.mkdir(parents=True)
    data = directory / 'data'
    previous = convert(ConversionRequest(source, data, ('markdown', 'html')))
    before = snapshot(data)
    evidence = {'window': window, 'previous_job_id': previous.job_id, 'before': before}
    evidence_path = directory / 'evidence.json'
    save_evidence(evidence_path, evidence)
    # Both paths run the real converter. The post-commit path calls the original
    # transaction before raising; the other path exits only after publication.
    conversion = '''import hashlib,json,os,sqlite3,sys
from pathlib import Path
from src.adapters.persistence.sqlite import SQLiteCatalog
from src.application.convert import ConversionRequest,convert

original = SQLiteCatalog.publish_export
def boundary(self,**kwargs):
    if sys.argv[3] == 'post-commit':
        original(self,**kwargs)
    marker = {'window':sys.argv[3],'job_id':kwargs['job_id'],'export_id':kwargs['export_id'],'pid':os.getpid()}
    if sys.argv[3] == 'post-commit':
        data=Path(sys.argv[2])
        connection=sqlite3.connect(data/'catalog.sqlite')
        connection.row_factory=sqlite3.Row
        try:
            marker['committed_database']={table:[dict(row) for row in connection.execute(f'SELECT * FROM {table} ORDER BY rowid')]
                                          for table in ('jobs','exports','artifacts')}
        finally:
            connection.close()
        marker['committed_hashes']={path.relative_to(data).as_posix():hashlib.sha256(path.read_bytes()).hexdigest()
                                    for name in ('sources','revisions','exports','staging','jobs')
                                    for path in sorted((data/name).rglob('*')) if path.is_file()}
    Path(sys.argv[4]).write_text(json.dumps(marker),encoding='utf-8')
    print('INJECTED',json.dumps(marker),flush=True)
    if sys.argv[3] == 'post-commit':
        raise RuntimeError('UC-03-S6: exception after real publish_export commit')
    os._exit(73)
SQLiteCatalog.publish_export=boundary
result=convert(ConversionRequest(sys.argv[1],sys.argv[2],('markdown','html')))
print('CONVERSION_RETURNED',result.job_id,flush=True)
'''
    marker = directory / 'conversion-marker.json'
    evidence['conversion_process'] = boundary_process(
        conversion, [str(source), str(data), window, str(marker)], directory, 'conversion')
    evidence['conversion_marker'] = json.loads(marker.read_text(encoding='utf-8'))
    current = evidence['conversion_marker']['job_id']
    export = evidence['conversion_marker']['export_id']
    evidence['after_conversion'] = snapshot(data)
    save_evidence(evidence_path, evidence)
    assert evidence['conversion_process']['exit_code'] == (0 if window == 'post-commit' else 73)
    assert (data / 'exports' / export).is_dir(), '转换没有真正发布落盘'
    if window == 'post-commit':
        committed = evidence['conversion_marker']
        assert committed['committed_database'] == evidence['after_conversion']['database']
        assert committed['committed_hashes'] == {
            f'{name}/{path}': digest for name, files in evidence['after_conversion']['hashes'].items()
            for path, digest in files.items()}

    if window != 'post-commit':
        recovery = '''import json,os,sys
from pathlib import Path
from src.adapters.persistence.sqlite import SQLiteCatalog
from src.adapters.storage import LocalStorage
from src.application import jobs
from fastapi.testclient import TestClient
from web.app import app
from web.config import settings

def stop():
    marker={'window':sys.argv[2],'pid':os.getpid()}
    Path(sys.argv[3]).write_text(json.dumps(marker),encoding='utf-8')
    print('INJECTED_RECOVERY',json.dumps(marker),flush=True)
    os._exit(74)
if sys.argv[2] == 'failed-before-quarantine':
    original=SQLiteCatalog.interrupt_job
    def interrupt(self,*args,**kwargs):
        changed=original(self,*args,**kwargs)
        if changed:
            stop()
        return changed
    SQLiteCatalog.interrupt_job=interrupt
elif sys.argv[2] == 'quarantined-before-diagnostics':
    original=LocalStorage.rollback_publish
    def rollback(self,*args,**kwargs):
        retained=original(self,*args,**kwargs)
        stop()
        return retained
    LocalStorage.rollback_publish=rollback
elif sys.argv[2] == 'main-before-staging-diagnostic':
    original=jobs.write_json
    def write(path,value):
        original(path,value)
        if Path(path).parent.parent == Path(sys.argv[1])/'jobs':
            stop()
    jobs.write_json=write
else:
    raise AssertionError('未知恢复窗口')
settings.DATA_DIR=sys.argv[1]
with TestClient(app):
    raise AssertionError('未命中恢复中断窗口')
'''
        marker = directory / 'recovery-marker.json'
        evidence['recovery_process'] = boundary_process(
            recovery, [str(data), window, str(marker)], directory, 'recovery')
        evidence['recovery_marker'] = json.loads(marker.read_text(encoding='utf-8'))
        evidence['after_interruption'] = snapshot(data)
        save_evidence(evidence_path, evidence)
        assert evidence['recovery_process']['exit_code'] == 74
        interrupted = evidence['after_interruption']
        job = next(row for row in interrupted['database']['jobs'] if row['job_id'] == current)
        assert job['status'] == 'failed' and job['download_relative'] is None
        published = data / 'exports' / export
        retained = data / 'staging' / export
        main_report = f'jobs/{current}/report.json'
        if window == 'failed-before-quarantine':
            assert published.is_dir() and not retained.exists()
            assert main_report not in interrupted['reports']
        else:
            assert not published.exists() and retained.is_dir()
            assert interrupted['reports'][f'staging/{export}/report.json']['status'] != 'failed'
            assert (main_report in interrupted['reports']) == (window == 'main-before-staging-diagnostic')

    # Restart twice even if the first startup exposes a stale diagnostic, so
    # the failed report preserves both outcomes rather than hiding a window.
    evidence['restart_1'] = web_restart(data, directory, 'restart-1-downloads')
    evidence['restart_2'] = web_restart(data, directory, 'restart-2-downloads')
    save_evidence(evidence_path, evidence)
    first, second = evidence['restart_1'], evidence['restart_2']
    assert first == {**second, 'http': first['http']}, '重复恢复改变数据库、文件或诊断'
    assert {key: value['state'] for key, value in first['http'].items()} == {
        key: value['state'] for key, value in second['http'].items()}
    for state in (first, second):
        assert state['hashes']['sources'] == before['hashes']['sources']
        assert state['hashes']['revisions'] == before['hashes']['revisions']
        for name, digest in before['hashes']['exports'].items():
            assert state['hashes']['exports'][name] == digest, '历史文件被改变'
        earlier = state['http'][previous.job_id]
        assert earlier['http_status'] == 200 and earlier['download_status'] == 200
        old_job = next(row for row in before['database']['jobs'] if row['job_id'] == previous.job_id)
        assert earlier['download_sha256'] == before['hashes']['exports'][
            f'{previous.export_id}/{old_job["download_relative"]}']
        current_response = state['http'][current]
        assert current_response['http_status'] == 200
        if window == 'post-commit':
            assert current_response['state']['status'] in ('completed', 'completed_with_warnings')
            assert current_response['download_status'] == 200 and current_response['state']['download_url']
            assert state['database'] == evidence['after_conversion']['database']
            assert state['hashes'] == evidence['after_conversion']['hashes']
            job = next(row for row in state['database']['jobs'] if row['job_id'] == current)
            assert current_response['download_sha256'] == state['hashes']['exports'][
                f'{export}/{job["download_relative"]}']
            assert not (data / 'staging' / export).exists()
            assert f'jobs/{current}/report.json' not in state['reports']
        else:
            assert current_response['state']['status'] == 'failed' and current_response['state']['message']
            assert current_response['state']['download_url'] is None and current_response['download_status'] == 404
            main = state['reports'][f'jobs/{current}/report.json']
            isolated = state['reports'][f'staging/{export}/report.json']
            assert main == isolated, f'{window}: 主诊断与隔离目录诊断不一致'
            assert main['status'] == 'failed' and main['error'] and main['job_id'] == current
            assert main['export_id'] == export and main['rollback_error'] is None
            assert Path(main['retained_dir']) == data / 'staging' / export
            assert not (data / 'exports' / export).exists()
            for name, digest in evidence['after_conversion']['hashes']['exports'].items():
                if name.startswith(export + '/') and not name.endswith('/report.json'):
                    assert state['hashes']['staging'][name] == digest, '隔离丢失或修改原有产物'
    if window == 'post-commit':
        log = Path(evidence['conversion_process']['log']).read_text(encoding='utf-8')
        assert 'UC-03-S6: exception after real publish_export commit' in log
        assert 'Publication committed despite a subsequent error' in log
        assert 'CONVERSION_RETURNED ' + current in log


def check_live_owner(source: Path, data: Path, workdir: Path) -> dict:
    ready = workdir / 'live-owner-ready.json'
    script = '''import json,sys
from pathlib import Path
from src.adapters.persistence.sqlite import SQLiteCatalog
from src.application.convert import ConversionRequest,convert

publish = SQLiteCatalog.publish_export
def pause_before_success_commit(self,**kwargs):
    ready = Path(sys.argv[3])
    ready.with_suffix('.tmp').write_text(json.dumps({'job_id':kwargs['job_id'],'export_id':kwargs['export_id']}))
    ready.with_suffix('.tmp').replace(ready)
    if sys.stdin.readline().strip() != 'publish':
        raise RuntimeError('未收到继续发布指令')
    return publish(self,**kwargs)
SQLiteCatalog.publish_export=pause_before_success_commit
convert(ConversionRequest(sys.argv[1],sys.argv[2],('markdown','html')))
'''
    with (workdir / 'live-owner.log').open('w', encoding='utf-8') as log:
        process = subprocess.Popen([sys.executable, '-c', script, str(source), str(data), str(ready)],
                                   cwd=workdir, env=subprocess_environment(), stdin=subprocess.PIPE,
                                   stdout=log, stderr=subprocess.STDOUT, text=True,
                                   creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == 'nt' else 0)
        try:
            deadline = time.monotonic() + 30
            while not ready.exists():
                assert process.poll() is None, '转换进程在发布边界前退出，见 live-owner.log'
                assert time.monotonic() < deadline, '未到达发布边界'
                time.sleep(0.05)
            identity = json.loads(ready.read_text())
            with web_client(data) as client:
                assert process.poll() is None, '恢复的存活探测终止了任务进程'
                state = client.get('/api/status/' + identity['job_id']).json()
                assert process.poll() is None, '恢复的存活探测终止了任务进程'
                assert state['status'] == 'processing', state
                assert state['download_url'] is None
                assert client.get('/api/download/' + identity['job_id']).status_code == 404
                assert (data / 'exports' / identity['export_id']).is_dir()
                process.communicate('publish\n', timeout=60)
                assert process.returncode == 0, '恢复后发布失败，见 live-owner.log'
                completed = client.get('/api/status/' + identity['job_id']).json()
                assert completed['status'] in ('completed', 'completed_with_warnings'), completed
                assert client.get(completed['download_url']).status_code == 200
            return {'pid': process.pid, **identity, 'status_after_recovery': state,
                    'status_after_publication': completed, 'exit_code': process.returncode}
        finally:
            # This scenario owns this child; never clean up processes by a catalog PID.
            if process.poll() is None:
                process.terminate()
            process.wait(timeout=10)
            if process.stdin is not None:
                process.stdin.close()


def run(workdir: Path) -> dict:
    samples = fixture_cases(workdir / 'inputs')
    source = next(path for path, case in samples if case['id'] == 'epub-structured')
    data = workdir / 'data'
    previous = convert(ConversionRequest(source, data, ('markdown', 'html')))
    old_hashes = tree_hashes(previous.output_dir)
    live_owner = check_live_owner(source, workdir / 'live-data', workdir)
    script = '''import os,sys
from src.adapters.persistence.sqlite import SQLiteCatalog
from src.application.convert import ConversionRequest,convert
def crash_before_success_commit(self,**kwargs):
    os._exit(73)
SQLiteCatalog.publish_export=crash_before_success_commit
convert(ConversionRequest(sys.argv[1],sys.argv[2],('markdown','html')))
'''
    interrupted = subprocess.run([sys.executable, '-c', script, str(source), str(data)],
                                 cwd=workdir, env=subprocess_environment(), capture_output=True,
                                 text=True, timeout=60)
    (workdir / 'pre-commit-conversion.log').write_text(interrupted.stdout + interrupted.stderr, encoding='utf-8')
    assert interrupted.returncode == 73, interrupted.stdout + interrupted.stderr
    with sqlite3.connect(data / 'catalog.sqlite') as connection:
        pending = connection.execute('SELECT job_id,export_id FROM exports WHERE job_id != ?', (previous.job_id,)).fetchall()
    assert len(pending) == 1 and pending[0][1]
    task, export_id = pending[0]
    assert (data / 'exports' / export_id).is_dir(), '未到达真实发布落盘阶段'
    before_recovery = snapshot(data)
    # Application startup performs recovery, as it does after a real service restart.
    with web_client(data) as client:
        state = client.get('/api/status/' + task).json()
        assert state['status'] == 'failed' and state['message'], state
        assert state['download_url'] is None
        assert client.get('/api/download/' + task).status_code == 404
        earlier = client.get('/api/status/' + previous.job_id).json()
        assert earlier['status'] in ('completed', 'completed_with_warnings'), earlier
        assert client.get(earlier['download_url']).status_code == 200
    assert not (data / 'exports' / export_id).exists()
    retained = data / 'staging' / export_id
    assert retained.is_dir() and (retained / 'book.md').is_file()
    report = json.loads((retained / 'report.json').read_text())
    assert report['status'] == 'failed' and report['error'] and report['job_id'] == task
    assert tree_hashes(previous.output_dir) == old_hashes
    evidence = {'live_owner': live_owner, 'interrupted_job_id': task, 'export_id': export_id,
                'conversion_exit_code': interrupted.returncode, 'before_recovery': before_recovery,
                'after_recovery': snapshot(data),
                'recovered_status': state, 'diagnostic': report,
                'previous_job_id': previous.job_id, 'previous_hashes_before': old_hashes,
                'previous_hashes_after': tree_hashes(previous.output_dir)}
    (workdir / 'recovery-evidence.json').write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding='utf-8')
    windows = ('main-before-staging-diagnostic', 'failed-before-quarantine',
               'quarantined-before-diagnostics', 'post-commit')
    failures = {}
    for window in windows:
        try:
            check_exception_window(source, workdir / 'examples' / window, window)
        except Exception:
            failures[window] = traceback.format_exc()
    save_evidence(workdir / 'example-results.json', {'windows': list(windows), 'failures': failures})
    assert not failures, failures
    return {'checks': ['真实转换进程在发布成功提交前终止，启动恢复将任务改为可查询失败',
                       '未提交产物移回隔离目录且不可下载，先前下载可用且文件哈希不变',
                       '实际 Web 启动不终止有效租约的存活 owner，原进程可继续提交且下载可用',
                       '恢复在失败提交后、隔离后、主诊断写入后三窗口真实退出，再次启动补齐且重复恢复稳定',
                       '真实成功事务提交后注入异常，当前及历史状态、下载、文件哈希不变'],
            'evidence': {'exception_windows': list(windows), 'results': str(workdir / 'example-results.json')}}


if __name__ == "__main__":
    from tests.common.support import run_one

    raise SystemExit(run_one(__file__))
