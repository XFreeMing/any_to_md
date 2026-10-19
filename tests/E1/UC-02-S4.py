"""UC-02-S4: Markdown 上游的损失经完整转换进入持久诊断。"""

if __name__ == "__main__" and not __package__:
    import runpy
    from pathlib import Path

    runpy.run_path(str(Path(__file__).resolve().parents[1] / "common" / "bootstrap.py"))

import json
from pathlib import Path

from tests.common.conversion_support import archive, body_text, export_matrix, image_source, ocr_endpoint


def run(workdir: Path) -> dict:
    source = image_source(workdir / "scan.png")
    response = archive({"result.md": "# OCR_BODY_701\n\nUpstream omitted the original layout.\n"})
    revisions = set()
    with ocr_endpoint(response, 200) as (url, requests):
        for _, target, export in export_matrix(workdir, source, url):
            assert body_text(export, target).count("OCR_BODY_701") == 1
            snapshot = json.loads((export / "book.json").read_text())
            revisions.add(snapshot["revision_id"])
            report = json.loads((export / "report.json").read_text())
            assert any(item["code"] == "ocr_markdown_only" for item in report["diagnostics"]), report
        assert requests
    assert len(revisions) == 1, "不同入口或输出没有共用相同正文修订"
    return {"checks": ["所有入口和输出使用共同正文", "上游 Markdown 损失进入持久产物诊断（不证明识别质量）"]}


if __name__ == "__main__":
    from tests.common.support import run_one

    raise SystemExit(run_one(__file__))
