"""UC-03-S6：发布落盘但成功提交前进程终止，重启后隔离未提交产物。"""

if __name__ == "__main__" and not __package__:
    import runpy
    from pathlib import Path

    runpy.run_path(str(Path(__file__).resolve().parents[1] / "common" / "bootstrap.py"))

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

from any_to_md.application.convert import ConversionRequest, convert
from tests.common.delivery_support import fixture_cases, subprocess_environment, tree_hashes, web_client


def run(workdir: Path) -> dict:
    samples = fixture_cases(workdir / 'inputs')
    source = next(path for path, case in samples if case['id'] == 'epub-structured')
    data = workdir / 'data'
    previous = convert(ConversionRequest(source, data, ('markdown', 'html')))
    old_hashes = tree_hashes(previous.output_dir)
    script = '''import os,sys
from any_to_md.adapters.persistence.sqlite import SQLiteCatalog
from any_to_md.application.convert import ConversionRequest,convert
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
    return {'checks': ['真实转换进程在发布成功提交前终止，启动恢复将任务改为可查询失败',
                       '未提交产物移回隔离目录且不可下载，先前下载可用且文件哈希不变']}


if __name__ == "__main__":
    from tests.common.support import run_one

    raise SystemExit(run_one(__file__))
