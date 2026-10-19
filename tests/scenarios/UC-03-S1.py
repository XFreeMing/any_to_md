"""UC-03-S1：离开源码目录使用最终 wheel 的全部入口和输出。"""

if __name__ == "__main__" and not __package__:
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import json
import os
import subprocess
from pathlib import Path

from tests.scenarios.delivery_support import require_visual_evidence
from tests.scenarios.support import Blocked


def run(workdir: Path) -> dict:
    python = os.environ.get('ANY_TO_MD_INSTALLED_PYTHON')
    wheel = os.environ.get('ANY_TO_MD_INSTALLED_WHEEL')
    if not python or not wheel:
        raise Blocked('需要 ANY_TO_MD_INSTALLED_PYTHON 与 ANY_TO_MD_INSTALLED_WHEEL，使用最终 wheel 的独立安装环境。')
    assert Path(python).is_file() and Path(wheel).is_file(), '安装解释器或 wheel 不存在'
    project = Path(__file__).resolve().parents[2]
    result_path = workdir / 'installed-matrix.json'
    bootstrap = '''import json,sys,types
from pathlib import Path
tests = types.ModuleType('tests'); tests.__path__ = [sys.argv[1]]; sys.modules['tests'] = tests
from tests.scenarios.delivery_support import installed_matrix
from tests.scenarios.support import Blocked
try:
    result = installed_matrix(Path(sys.argv[2]), Path(sys.argv[3]), Path(sys.argv[4]))
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
                            technical_checks=['最终源码、wheel 与安装一致；能力表全部扩展名、入口和输出的产物检查通过'])
    return {'checks': ['最终源码、wheel、安装文件一致，生产模块仅从独立环境加载',
                       '能力表全部扩展名、入口与输出的真实转换及产物检查通过',
                       '人工阅读结论逐项关联当前样本和产物哈希'], 'evidence': str(result_path)}


if __name__ == "__main__":
    from tests.scenarios.support import run_one

    raise SystemExit(run_one(__file__))
