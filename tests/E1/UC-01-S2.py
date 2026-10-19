"""UC-01-S2 · Epic E1 · US-01 / US-02：保留混合页文字，避免为普通插图误用 OCR。

Given 含普通插图的原生 PDF，以及需要真实 OCR 的扫描 / 混合页 PDF。
When 经 Web API TestClient、模块 CLI、目录批处理分别导出 Markdown / HTML / PDF。
Then 普通插图不触发 OCR，混合输入的文字标记各出现一次，并核对混合页人工证据。
受控失败响应只监测普通插图是否误调用 OCR；混合页仍依赖真实服务与人工参考。
"""

if __name__ == "__main__" and not __package__:
    import runpy
    from pathlib import Path

    runpy.run_path(str(Path(__file__).resolve().parents[1] / "common" / "bootstrap.py"))

from pathlib import Path

from tests.common.conversion_support import body_text, export_matrix, ocr_endpoint, pdf_source, real_ocr_url


def run(workdir: Path) -> dict:
    ordinary, _ = pdf_source(workdir / "illustration.pdf", illustration=True)
    with ocr_endpoint() as (url, requests):
        for entry, target, observed in export_matrix(workdir / "ordinary", ordinary, url):
            if entry == "web":
                assert observed["state"]["status"] in {"completed", "completed_with_warnings"}, observed["state"]
                assert observed["download_status"] == 200, observed
            else:
                process = observed["process"]
                assert process.returncode == 0, (entry, target, process.stdout, process.stderr)
            assert len(observed["exports"]) == 1, observed
            export = observed["exports"][0]
            assert body_text(export, target).count("NATIVE_PAGE_1") == 1
        assert not requests, "普通插图触发了 OCR"
    source, markers = pdf_source(workdir / "mixed.pdf", mixed=True)
    exports = []
    for entry, target, observed in export_matrix(workdir / "mixed", source, real_ocr_url()):
        if entry == "web":
            assert observed["state"]["status"] in {"completed", "completed_with_warnings"}, observed["state"]
            assert observed["download_status"] == 200, observed
        else:
            process = observed["process"]
            assert process.returncode == 0, (entry, target, process.stdout, process.stderr)
        assert len(observed["exports"]) == 1, observed
        export = observed["exports"][0]
        text = body_text(export, target)
        assert all(text.count(marker) == 1 for marker in markers), text
        exports.append(export)
    from tests.common.delivery_support import require_visual_evidence
    require_visual_evidence("UC-01-S2", [source], exports, ["mixed_bitmap_text", "no_duplicate_body"],
                            technical_checks=["普通插图未调用 OCR；API / 模块 CLI / 目录批处理的三种输出保留全部混合页标记且不重复"])
    return {"checks": ["普通插图未调用 OCR", "混合页位图文字有覆盖且与原生正文不重复"]}


if __name__ == "__main__":
    from tests.common.support import run_one

    raise SystemExit(run_one(__file__))
