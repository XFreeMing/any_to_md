"""UC-02-S1 · Epic E1 · US-01 / US-03：将支持的图片识别为可追溯阅读文档。

Given 能力表声明的每种图片后缀、真实 OCR 服务和对应人工参考。
When 经 Web API TestClient、模块 CLI、目录批处理分别导出 Markdown / HTML / PDF。
Then 发布产物含唯一正文标记和表格文字，保存共同正文及报告，并核对人工证据。
当前检查报告存在；损失诊断和来源追溯的具体断言仍见 TODO，不能据此推定识别质量。
"""

if __name__ == "__main__" and not __package__:
    import runpy
    from pathlib import Path

    runpy.run_path(str(Path(__file__).resolve().parents[1] / "common" / "bootstrap.py"))

import json
from pathlib import Path
from PIL import Image

from tests.common.conversion_support import body_text, export_matrix, image_source, real_ocr_url


def run(workdir: Path) -> dict:
    from src.application.capabilities import CAPABILITIES

    url = real_ocr_url()
    original = image_source(workdir / "source.png")
    sources, exports = [], []
    for suffix in CAPABILITIES["image"].suffixes:
        source = workdir / f"scan{suffix}"
        with Image.open(original) as image:
            image.save(source)
        sources.append(source)
        for entry, target, observed in export_matrix(workdir / suffix[1:], source, url):
            if entry == "web":
                assert observed["state"]["status"] in {"completed", "completed_with_warnings"}, observed["state"]
                assert observed["download_status"] == 200, observed
            else:
                process = observed["process"]
                assert process.returncode == 0, (entry, target, process.stdout, process.stderr)
            assert len(observed["exports"]) == 1, observed
            export = observed["exports"][0]
            text = body_text(export, target)
            assert text.count("IMAGE_OCR_601") == 1, text
            assert "ITEM" in text and "42" in text
            assert json.loads((export / "book.json").read_text())["schema_version"] == 2
            assert (export / "report.json").is_file()
            exports.append(export)
    from tests.common.delivery_support import require_visual_evidence
    require_visual_evidence("UC-02-S1", sources, exports, ["ocr_reference", "tables_images", "raw_response_trace", "readability"],
                            technical_checks=["声明的图片后缀经真实 OCR，通过 API / 模块 CLI / 目录批处理生成三种输出并保留正文标记及共同正文"])
    return {"checks": ["声明的图片后缀经真实 OCR 与 API / 模块 CLI / 目录批处理生成三种输出", "正文标记、报告存在及列明的 OCR 人工证据已核对"]}


if __name__ == "__main__":
    from tests.common.support import run_one

    raise SystemExit(run_one(__file__))
