"""UC-03-S6：发布中断后恢复且保留历史下载（E5 / US-05）。

作为工具使用者，我希望中断任务有可核对结果，以便继续读取早期成功资料。
Given 已有成功 EPUB 导出，另一次转换在产物落盘、成功状态提交前进程终止。
When 重启实际 Web 运行时执行恢复并查询两个任务。
Then 中断任务失败且诊断完整，未提交产物被隔离，早期下载及文件哈希不变。
And 另一仍持有有效租约的转换进程不被探测终止，恢复后可继续提交并下载。

本文件目前只有提交前中断；恢复再次中断和提交后异常的 Examples 仍在 TODO。
"""

if __name__ == "__main__" and not __package__:
    import runpy
    from pathlib import Path

    runpy.run_path(str(Path(__file__).resolve().parents[1] / "common" / "bootstrap.py"))

import json
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

from src.application.convert import ConversionRequest, convert
from tests.common.delivery_support import fixture_cases, subprocess_environment, tree_hashes, web_client


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
    assert interrupted.returncode == 73, interrupted.stdout + interrupted.stderr
    with sqlite3.connect(data / 'catalog.sqlite') as connection:
        pending = connection.execute('SELECT job_id,export_id FROM exports WHERE job_id != ?', (previous.job_id,)).fetchall()
    assert len(pending) == 1 and pending[0][1]
    task, export_id = pending[0]
    assert (data / 'exports' / export_id).is_dir(), '未到达真实发布落盘阶段'
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
                'recovered_status': state, 'diagnostic': report,
                'previous_job_id': previous.job_id, 'previous_hashes_before': old_hashes,
                'previous_hashes_after': tree_hashes(previous.output_dir)}
    (workdir / 'recovery-evidence.json').write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding='utf-8')
    return {'checks': ['真实转换进程在发布成功提交前终止，启动恢复将任务改为可查询失败',
                       '未提交产物移回隔离目录且不可下载，先前下载可用且文件哈希不变',
                       '实际 Web 启动不终止有效租约的存活 owner，原进程可继续提交且下载可用']}


if __name__ == "__main__":
    from tests.common.support import run_one

    raise SystemExit(run_one(__file__))
