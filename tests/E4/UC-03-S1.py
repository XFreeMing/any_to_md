"""UC-03-S1：安装后在源码目录外转换资料（E4 / US-04、US-05）。

作为安装使用者，我希望交付包独立运行，以便不依赖开发目录取得正确产物。
Given 最终源码构建的 wheel、独立安装解释器、能力表样本和所需运行依赖。
When 隔离解释器通过 Web API、模块 CLI 和目录批处理转换各输出。
Then 源码 / wheel / 安装内容一致，正文、资源及可下载结果符合独立预期，
     人工阅读证据匹配本次样本与产物。

实际命令启动、Web 启动 / static 和迁移后的旧包拒绝仍见 TODO；本文件不替代那些入口。
"""

if __name__ == "__main__" and not __package__:
    import runpy
    from pathlib import Path

    runpy.run_path(str(Path(__file__).resolve().parents[1] / "common" / "bootstrap.py"))

import base64
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from zipfile import ZipFile

from tests.common.delivery_support import (
    cli, digest, fixture_cases, inspect_export, require_visual_evidence, source_server, web_client,
)
from tests.common.fixtures import _archive
from tests.common.support import Blocked


def run_installed_matrix(workdir: Path, wheel: Path, source_root: Path) -> dict:
    """Called only by an isolated interpreter; never re-enters the scenario runner."""
    from src.application.capabilities import CAPABILITIES, OUTPUT_TARGET
    from src.application.jobs import JobRuntime
    import src

    prefix = Path(sys.prefix).resolve()
    package = Path(src.__file__).resolve().parent
    assert package.is_relative_to(prefix), '运行的核心不来自独立安装环境'
    source_dirs = {'src': source_root / 'src',
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
    from tests.common.docx_fixtures import structured_docx
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
                from tests.common.support import Blocked
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
                            from tests.common.support import Blocked
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
                    observed = inspect_export(export, case, (target,))
                    assert observed, '所选输出没有可检查的阅读视图'
                    for observation in observed:
                        content = observation['inspection']
                        assert content['passed'], {
                            'case': case['id'], 'entry': entry,
                            'view': observation['view'], 'failures': content['failures'],
                        }
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


def run(workdir: Path) -> dict:
    python = os.environ.get('ANY_TO_MD_INSTALLED_PYTHON')
    wheel = os.environ.get('ANY_TO_MD_INSTALLED_WHEEL')
    if not python or not wheel:
        raise Blocked('需要 ANY_TO_MD_INSTALLED_PYTHON 与 ANY_TO_MD_INSTALLED_WHEEL，使用最终 wheel 的独立安装环境。')
    assert Path(python).is_file() and Path(wheel).is_file(), '安装解释器或 wheel 不存在'
    project = Path(__file__).resolve().parents[2]
    result_path = workdir / 'installed-matrix.json'
    bootstrap = '''import json,runpy,sys
from pathlib import Path
runpy.run_path(str(Path(sys.argv[1]) / "common" / "bootstrap.py"))
scenario = runpy.run_path(
    str(Path(sys.argv[1]) / "E4" / "UC-03-S1.py"), run_name="installed_uc_03_s1",
)
from tests.common.support import Blocked
try:
    result = scenario["run_installed_matrix"](Path(sys.argv[2]), Path(sys.argv[3]), Path(sys.argv[4]))
except Blocked as error:
    Path(sys.argv[5]).write_text(json.dumps({'blocked': str(error)}))
    raise SystemExit(78)
Path(sys.argv[5]).write_text(json.dumps(result, ensure_ascii=False, indent=2))
'''
    environment = {key: value for key, value in os.environ.items() if key not in {'PYTHONPATH', 'PYTHONHOME'}}
    completed = subprocess.run([str(Path(python).absolute()), '-I', '-c', bootstrap,
                                str(project / 'tests'), str(workdir), str(Path(wheel).absolute()),
                                str(project), str(result_path)], cwd=workdir, env=environment,
                               capture_output=True, text=True, timeout=1800)
    (workdir / 'installed-process.log').write_text(completed.stdout + completed.stderr)
    if completed.returncode == 78:
        raise Blocked(json.loads(result_path.read_text())['blocked'])
    assert completed.returncode == 0, completed.stdout + completed.stderr
    result = json.loads(result_path.read_text())
    assert result['observations']
    require_visual_evidence('UC-03-S1', [Path(item) for item in result['sources']],
                            [Path(item) for item in result['exports']],
                            ['各入口和输出的代表页内容、资源与链接可阅读', '表格、代码、中文与分页符合独立源参考'],
                            technical_checks=['最终源码、wheel 与安装一致；能力表全部扩展名的 Web API、模块 CLI / 批处理及输出检查通过'])
    return {'checks': ['最终源码、wheel、安装文件一致，生产模块仅从独立环境加载',
                       '能力表全部扩展名经 Web API、模块 CLI / 批处理转换并核对产物',
                       '人工阅读结论逐项关联当前样本和产物哈希'], 'evidence': str(result_path)}


if __name__ == "__main__":
    from tests.common.support import run_one

    raise SystemExit(run_one(__file__))
