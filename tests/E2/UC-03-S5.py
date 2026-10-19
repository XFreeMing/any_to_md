"""UC-03-S5：OCR 不可用及 PDF 运行依赖缺失都不能伪成功。"""

if __name__ == "__main__" and not __package__:
    import runpy
    from pathlib import Path

    runpy.run_path(str(Path(__file__).resolve().parents[1] / "common" / "bootstrap.py"))

import json
import subprocess
import sys
from pathlib import Path

from tests.common.delivery_support import fixture_cases, subprocess_environment
from tests.common.support import Blocked


def run(workdir: Path) -> dict:
    samples = fixture_cases(workdir / 'inputs')
    script = '''import importlib.abc,json,runpy,socket,sys
from pathlib import Path
condition,source,data,report=sys.argv[1:5]
runpy.run_path(str(Path(sys.argv[5]) / "common" / "bootstrap.py"))
if condition == 'pdf':
    class MissingRenderer(importlib.abc.MetaPathFinder):
        def find_spec(self,fullname,path=None,target=None):
            if fullname == 'weasyprint' or fullname.startswith('weasyprint.'):
                raise ModuleNotFoundError("PDF rendering dependency weasyprint is unavailable")
    sys.meta_path.insert(0,MissingRenderer())
from tests.common.delivery_support import web_client
from any_to_md.application.jobs import JobRuntime
settings={}
guard=None
if condition == 'ocr':
    guard=socket.socket()
    try:
        guard.bind(('127.0.0.1',0))
    except PermissionError:
        raise SystemExit(78)
    settings={'OCR_API_URL':f'http://127.0.0.1:{guard.getsockname()[1]}/api/v1/ocr/process','OCR_TIMEOUT':1}
with web_client(Path(data),**settings) as client:
    uploaded=client.post('/api/upload',files={'file':(Path(source).name,Path(source).read_bytes())})
    assert uploaded.status_code==200,uploaded.text
    task=uploaded.json()['task_id']
    converted=client.post('/api/convert',json={'task_id':task,'output_format':'pdf' if condition=='pdf' else 'md_single'})
    assert converted.status_code==200,converted.text
    state=client.get('/api/status/'+task).json()
    assert state['status']=='failed',state
    assert state['message'] and state['download_url'] is None,state
    assert client.get('/api/download/'+task).status_code==404
    runtime=JobRuntime(data);job=runtime.get(task)
    diagnostic=json.loads(runtime.storage.resolve(job.report_key).read_text())
    assert diagnostic['status']=='failed' and diagnostic.get('error'),diagnostic
    Path(report).write_text(json.dumps({'state':state,'diagnostic':diagnostic},ensure_ascii=False,indent=2))
    if condition=='pdf':
        assert 'weasyprint' in state['message'],state
    else:
        assert any(word in state['message'].lower() for word in ('connect','connection','refused','ocr','识别')),state
if guard:guard.close()
'''
    failures, blockers = [], []
    for condition, name in (('ocr', 'image-ocr'), ('pdf', 'epub-structured')):
        source = next(path for path, case in samples if case['id'] == name)
        report = workdir / f'{condition}-failure.json'
        completed = subprocess.run([sys.executable, '-c', script, condition, str(source),
                                    str(workdir / condition), str(report), str(Path(__file__).resolve().parents[1])], cwd=workdir,
                                   env=subprocess_environment(), capture_output=True, text=True, timeout=60)
        (workdir / f'{condition}-process.log').write_text(completed.stdout + completed.stderr)
        if completed.returncode == 78:
            blockers.append('环境禁止绑定回环端口，无法建立真实 OCR 不可连接条件')
            continue
        if completed.returncode:
            failures.append(condition + ': ' + completed.stdout + completed.stderr)
            continue
        assert json.loads(report.read_text())['state']['status'] == 'failed'
    assert not failures, '\n'.join(failures)
    if blockers:
        raise Blocked('; '.join(blockers))
    return {'checks': ['真实图片转换遭遇不可连接的 OCR 服务时持久失败、错误可见且禁止下载',
                       '缺少 WeasyPrint 运行依赖的独立进程从 Web 入口失败，未发布伪成功产物']}


if __name__ == "__main__":
    from tests.common.support import run_one

    raise SystemExit(run_one(__file__))
