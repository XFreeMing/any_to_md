"""UC-01-S4 · Epic E1 · US-01 / US-02：让使用者识别 OCR 故障造成的具体缺页。

Given 含扫描 / 混合页的 PDF，OCR HTTP 服务统一返回 503。
When 经 Web API TestClient、模块 CLI、目录批处理请求 Markdown / HTML / PDF。
Then 任务只能失败或部分成功；失败 Web 任务无下载，扫描 / 混合页有未覆盖状态与原因。
当前仅验证整体服务故障；部分页面成功、按可用正文决定 partial 的样本仍见 TODO。
"""

if __name__ == "__main__" and not __package__:
    import runpy
    from pathlib import Path

    runpy.run_path(str(Path(__file__).resolve().parents[1] / "common" / "bootstrap.py"))

from pathlib import Path

from tests.common.conversion_support import failure_matrix, ocr_endpoint, pdf_source


def run(workdir: Path) -> dict:
    source, _ = pdf_source(workdir / "needs-ocr.pdf", mixed=True)
    with ocr_endpoint() as (url, requests):
        for entry, target, observed in failure_matrix(workdir, source, url):
            if entry == "web":
                state = observed["state"]
                assert state["status"] in {"failed", "partial"}, state
                if state["status"] == "failed":
                    assert state["download_url"] is None, state
            else:
                process = observed["process"]
                assert process.returncode in {1, 2}, (entry, target, process.stdout, process.stderr)
                assert len(observed["reports"]) == 1, "输入被静默跳过或未保留任务诊断"
                assert observed["job_statuses"][0] in {"failed", "partial"}, observed
            report = observed["reports"][0]
            # Indices 1 and 2 contain raster text in the independently authored PDF.
            pages = report.get("pages", [])
            missing = {page["page_index"]: page for page in pages if isinstance(page, dict)
                       and isinstance(page.get("page_index"), int)}
            for index in (1, 2):
                assert index in missing, (entry, target, "缺少具体未覆盖页", index, report)
                assert missing[index].get("status") in {"failed", "uncovered", "partial"}, missing[index]
                assert missing[index].get("error") or missing[index].get("reason"), missing[index]
        assert requests, "识别场景未触发外部服务"
    return {"checks": ["API / 模块 CLI / 目录批处理的三种输出请求未将 HTTP 故障报告为完整成功", "扫描与混合页的未覆盖状态和失败原因可核对"]}


if __name__ == "__main__":
    from tests.common.support import run_one

    raise SystemExit(run_one(__file__))
