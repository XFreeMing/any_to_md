"""UC-01-S1: 原生、扫描、混合与空白页经所有入口完整转换。"""

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
    for entry, target, export in export_matrix(workdir, source, real_ocr_url()):
        text = body_text(export, target)
        assert all(text.count(marker) == 1 for marker in markers), (entry, target, text)
        assert [text.index(marker) for marker in markers] == sorted(text.index(marker) for marker in markers)
        snapshot = json.loads((export / "book.json").read_text())
        assert snapshot["schema_version"] == 2, "缺少持久共同正文"
        exports.append(export)
    from tests.common.delivery_support import require_visual_evidence
    require_visual_evidence("UC-01-S1", [source], exports, ["page_coverage", "blank_page", "tables_images_links", "readability"],
                            technical_checks=["全部入口与输出的正文完整且顺序一致，持久正文存在"])
    return {"checks": ["全部入口与输出的正文完整且顺序一致", "逐页状态、空白页及代表页人工对照通过"]}


if __name__ == "__main__":
    from tests.common.support import run_one

    raise SystemExit(run_one(__file__))
