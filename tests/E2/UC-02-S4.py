"""UC-02-S4 · Epic E2 · US-01 / US-03：让使用者知道 OCR 只返回 Markdown 时的结构损失。

Given PNG 输入，OCR HTTP 响应仅提供带固定正文标记的 Markdown。
When 经 Web API TestClient、模块 CLI、目录批处理分别导出 Markdown / HTML / PDF。
Then 正文标记各出现一次，共用同一正文修订，产物报告包含 ocr_markdown_only 损失诊断。
当前不含资源，仅验证协议、正文复用和诊断；真实识别质量与资源复用仍见 TODO。
"""

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
        for entry, target, observed in export_matrix(workdir, source, url):
            if entry == "web":
                assert observed["state"]["status"] in {"completed", "completed_with_warnings"}, observed["state"]
                assert observed["download_status"] == 200, observed
            else:
                process = observed["process"]
                assert process.returncode == 0, (entry, target, process.stdout, process.stderr)
            assert len(observed["exports"]) == 1, observed
            export = observed["exports"][0]
            assert body_text(export, target).count("OCR_BODY_701") == 1
            snapshot = json.loads((export / "book.json").read_text())
            revisions.add(snapshot["revision_id"])
            report = json.loads((export / "report.json").read_text())
            assert any(item["code"] == "ocr_markdown_only" for item in report["diagnostics"]), report
        assert requests
    assert len(revisions) == 1, "不同入口或输出没有共用相同正文修订"
    return {"checks": ["API / 模块 CLI / 目录批处理的三种输出使用同一正文修订", "上游 Markdown 损失进入持久产物诊断（不证明识别质量）"]}


if __name__ == "__main__":
    from tests.common.support import run_one

    raise SystemExit(run_one(__file__))
