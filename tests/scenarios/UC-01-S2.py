"""UC-01-S2: 混合页文字完整，普通插图不触发整本 OCR。"""

if __name__ == "__main__" and not __package__:
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from pathlib import Path

from tests.scenarios.conversion_support import body_text, export_matrix, ocr_endpoint, pdf_source, real_ocr_url


def run(workdir: Path) -> dict:
    ordinary, _ = pdf_source(workdir / "illustration.pdf", illustration=True)
    with ocr_endpoint() as (url, requests):
        for _, target, export in export_matrix(workdir / "ordinary", ordinary, url):
            assert body_text(export, target).count("NATIVE_PAGE_1") == 1
        assert not requests, "普通插图触发了 OCR"
    source, markers = pdf_source(workdir / "mixed.pdf", mixed=True)
    exports = []
    for _, target, export in export_matrix(workdir / "mixed", source, real_ocr_url()):
        text = body_text(export, target)
        assert all(text.count(marker) == 1 for marker in markers), text
        exports.append(export)
    from tests.scenarios.delivery_support import require_visual_evidence
    require_visual_evidence("UC-01-S2", [source], exports, ["mixed_bitmap_text", "no_duplicate_body"],
                            technical_checks=["普通插图未调用 OCR，全部入口的混合页标记无遗漏或重复"])
    return {"checks": ["普通插图未调用 OCR", "混合页位图文字有覆盖且与原生正文不重复"]}


if __name__ == "__main__":
    from tests.scenarios.support import run_one

    raise SystemExit(run_one(__file__))
