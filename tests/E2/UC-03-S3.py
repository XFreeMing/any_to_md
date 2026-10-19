"""UC-03-S3：同时转换的任务和产物互不覆盖。"""

if __name__ == "__main__" and not __package__:
    import runpy
    from pathlib import Path

    runpy.run_path(str(Path(__file__).resolve().parents[1] / "common" / "bootstrap.py"))

import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from any_to_md.application.convert import ConversionRequest, convert
from any_to_md.application.jobs import JobRuntime
from tests.common.delivery_support import assert_export, fixture_cases, tree_hashes


def run(workdir: Path) -> dict:
    samples = fixture_cases(workdir / 'inputs')
    selected = [next(pair for pair in samples if pair[1]['id'] == name)
                for name in ('epub-structured', 'epub-structured', 'html-rich', 'docx-rich')]
    data = workdir / 'data'
    JobRuntime(data)
    barrier = threading.Barrier(len(selected), timeout=15)

    def perform(sample):
        barrier.wait()
        return convert(ConversionRequest(sample[0], data, ('markdown', 'html')))

    with ThreadPoolExecutor(max_workers=len(selected)) as pool:
        results = list(pool.map(perform, selected))
    assert len({item.job_id for item in results}) == len(selected)
    assert len({item.export_id for item in results}) == len(selected)
    assert len({item.output_dir for item in results}) == len(selected)
    assert results[0].revision_id == results[1].revision_id
    snapshots = {result.job_id: tree_hashes(result.output_dir) for result in results}
    runtime = JobRuntime(data)
    runtime.recover()
    for result, (_, case) in zip(results, selected):
        job = runtime.get(result.job_id)
        assert job and job.status.downloadable and job.export_id == result.export_id
        assert runtime.download(job.job_id).is_file()
        assert_export(result.output_dir, case)
        assert tree_hashes(result.output_dir) == snapshots[result.job_id]
    return {'checks': ['四个并发真实转换各有独立任务、导出目录和正确正文资源',
                       '相同原件复用修订但导出互不覆盖，恢复后下载及哈希保持一致']}


if __name__ == "__main__":
    from tests.common.support import run_one

    raise SystemExit(run_one(__file__))
