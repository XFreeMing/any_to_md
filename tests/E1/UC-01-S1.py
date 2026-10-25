"""UC-01-S1 · Epic E1 · US-01 / US-02：按源页序获得无遗漏、无重复的 PDF 正文。

Given 原生、扫描、混合和空白页组成的 PDF，真实 OCR 与匹配人工参考可用。
When 经 Web API TestClient、模块 CLI、目录批处理分别导出 Markdown / HTML / PDF。
Then 发布可下载产物和共同正文，源文字标记各出现一次且顺序一致，并核对人工证据。
当前编码范围为标记、正文版本及列明的人工检查项；逐页状态、空白页判定和链接样本仍见 TODO。
"""

if __name__ == "__main__" and not __package__:
    import runpy
    from pathlib import Path

    runpy.run_path(str(Path(__file__).resolve().parents[1] / "common" / "bootstrap.py"))

import json
from pathlib import Path

from tests.common.conversion_support import body_text, export_matrix, pdf_source, real_ocr_url


def run(workdir: Path) -> dict:
    source, markers = pdf_source(workdir / "all-pages.pdf", mixed=True)
    exports = []
    for entry, target, observed in export_matrix(workdir, source, real_ocr_url()):
        if entry == "web":
            assert observed["state"]["status"] in {"completed", "completed_with_warnings"}, observed["state"]
            assert observed["download_status"] == 200, observed
        else:
            process = observed["process"]
            assert process.returncode == 0, (entry, target, process.stdout, process.stderr)
        assert len(observed["exports"]) == 1, observed
        export = observed["exports"][0]
        text = body_text(export, target)
        assert all(text.count(marker) == 1 for marker in markers), (entry, target, text)
        assert [text.index(marker) for marker in markers] == sorted(text.index(marker) for marker in markers)
        snapshot = json.loads((export / "book.json").read_text())
        assert snapshot["schema_version"] == 2, "缺少持久共同正文"
        exports.append(export)
    from tests.common.delivery_support import require_visual_evidence
    require_visual_evidence("UC-01-S1", [source], exports, ["page_coverage", "blank_page", "tables_images_links", "readability"],
                            technical_checks=["API / 模块 CLI / 目录批处理的三种输出均保留唯一、顺序一致的文字标记及共同正文"])
    return {"checks": ["API / 模块 CLI / 目录批处理的三种输出均保留唯一、顺序一致的文字标记", "页覆盖、空白页、表格图片链接与可读性的指定人工证据已核对"]}


if __name__ == "__main__":
    from tests.common.support import run_one

    raise SystemExit(run_one(__file__))
