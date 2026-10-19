"""UC-02-S1: 声明支持的图片经真实 OCR 和所有入口生成共同产物。"""

if __name__ == "__main__" and not __package__:
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import json
from pathlib import Path
from PIL import Image

from tests.scenarios.conversion_support import body_text, export_matrix, image_source, real_ocr_url


def run(workdir: Path) -> dict:
    from any_to_md.application.capabilities import CAPABILITIES

    url = real_ocr_url()
    original = image_source(workdir / "source.png")
    sources, exports = [], []
    for suffix in CAPABILITIES["image"].suffixes:
        source = workdir / f"scan{suffix}"
        with Image.open(original) as image:
            image.save(source)
        sources.append(source)
        for _, target, export in export_matrix(workdir / suffix[1:], source, url):
            text = body_text(export, target)
            assert text.count("IMAGE_OCR_601") == 1, text
            assert "ITEM" in text and "42" in text
            assert json.loads((export / "book.json").read_text())["schema_version"] == 2
            assert (export / "report.json").is_file()
            exports.append(export)
    from tests.scenarios.delivery_support import require_visual_evidence
    require_visual_evidence("UC-02-S1", sources, exports, ["ocr_reference", "tables_images", "raw_response_trace", "readability"],
                            technical_checks=["全部图片类型、入口与输出经过真实识别，正文标记与共同修订检查通过"])
    return {"checks": ["全部声明图片类型、入口与输出经过真实识别", "共同正文与人工参考、原响应追溯一致"]}


if __name__ == "__main__":
    from tests.scenarios.support import run_one

    raise SystemExit(run_one(__file__))
