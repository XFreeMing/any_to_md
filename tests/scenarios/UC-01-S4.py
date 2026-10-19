"""UC-01-S4: 识别服务失败时逐页缺口可核对，不报告完整成功。"""

if __name__ == "__main__" and not __package__:
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from pathlib import Path

from tests.scenarios.conversion_support import failure_matrix, ocr_endpoint, pdf_source


def run(workdir: Path) -> dict:
    source, _ = pdf_source(workdir / "needs-ocr.pdf", mixed=True)
    with ocr_endpoint() as (url, requests):
        for entry, target, report in failure_matrix(workdir, source, url):
            # Indices 1 and 2 contain raster text in the independently authored PDF.
            pages = report.get("pages", [])
            missing = {page["page_index"]: page for page in pages if isinstance(page, dict)
                       and isinstance(page.get("page_index"), int)}
            for index in (1, 2):
                assert index in missing, (entry, target, "缺少具体未覆盖页", index, report)
                assert missing[index].get("status") in {"failed", "uncovered", "partial"}, missing[index]
                assert missing[index].get("error") or missing[index].get("reason"), missing[index]
        assert requests, "识别场景未触发外部服务"
    return {"checks": ["全部入口与输出的 HTTP 故障未被报告为完整成功", "扫描与混合页的未覆盖状态和失败原因可核对"]}


if __name__ == "__main__":
    from tests.scenarios.support import run_one

    raise SystemExit(run_one(__file__))
