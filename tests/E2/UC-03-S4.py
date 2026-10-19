"""UC-03-S4：实际下载包移出原工作目录后仍可阅读。"""

if __name__ == "__main__" and not __package__:
    import runpy
    from pathlib import Path

    runpy.run_path(str(Path(__file__).resolve().parents[1] / "common" / "bootstrap.py"))

import shutil
from pathlib import Path

from src.application.convert import ConversionRequest, convert
from src.application.jobs import JobRuntime
from tests.common.inspection import inspect_output
from tests.common.delivery_support import extract_download, fixture_cases, source_server


def run(workdir: Path) -> dict:
    samples = fixture_cases(workdir / 'inputs')
    selected = [pair for pair in samples if pair[1]['kind'] in ('epub', 'html', 'docx', 'url')]
    checks = []
    with source_server(workdir / 'inputs') as base:
        for index, (source, case) in enumerate(selected):
            source_input = base + '/' + source.name if case['kind'] == 'url' else source
            data = workdir / f'data-{index}'
            result = convert(ConversionRequest(source_input, data, ('markdown', 'html')))
            runtime = JobRuntime(data)
            for view in ('md_single', 'md_chapters', 'html'):
                # URL has one whole-document view; other formats expose split navigation.
                if case['kind'] == 'url' and view == 'md_chapters':
                    continue
                job = runtime.queue(result.job_id, view)
                convert(ConversionRequest(data_dir=data, job_id=job.job_id))
                download = runtime.download(job.job_id)
                moved = extract_download(download.read_bytes(), workdir / 'relocated' / f'{index}-{view}',
                                         'book.html' if view == 'html' else 'book.md')
                checks.append((moved, view, case))
            shutil.rmtree(data)
    # No source or original publication is available to resolve accidental dependencies.
    shutil.rmtree(workdir / 'inputs')
    for moved, view, case in checks:
        paths = sorted((moved / 'chapters').glob('*.md')) if view == 'md_chapters' else [moved / ('book.html' if view == 'html' else 'book.md')]
        report = inspect_output(paths, case, moved)
        assert report['passed'], {'case': case['id'], 'view': view, 'failures': report['failures']}
        if view == 'md_chapters':
            assert (moved / 'TOC.md').is_file()
            navigation = inspect_output([moved / 'TOC.md'], {'markers': [], 'minimum_images': 0}, moved)
            assert navigation['passed'], navigation['failures']
    return {'checks': ['EPUB、HTML、DOCX、URL 的实际下载包迁移后正文、图片和本地链接有效',
                       '删除源素材和原发布目录后，整体与分章导航仍在导出包内解析']}


if __name__ == "__main__":
    from tests.common.support import run_one

    raise SystemExit(run_one(__file__))
