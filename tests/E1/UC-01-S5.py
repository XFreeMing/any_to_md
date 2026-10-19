"""UC-01-S5 · Epic E1 · US-01 / US-02，依赖 US-12：复用 PDF 正文且保留早期下载。

Given 原生文字 PDF 已通过 Web API TestClient 导出 Markdown。
When 从该任务再次导出 HTML，并禁止再次调用 PDF Reader。
Then 新产物引用同一正文修订，旧文件哈希和下载不变，整个过程不请求 OCR。
当前仅此 API / 原生 PDF / 输出组合；已 OCR 正文、资源可读性及其余入口输出仍见 TODO。
"""

if __name__ == "__main__" and not __package__:
    import runpy
    from pathlib import Path

    runpy.run_path(str(Path(__file__).resolve().parents[1] / "common" / "bootstrap.py"))

import json
import importlib
from pathlib import Path
from unittest.mock import patch

from tests.common.conversion_support import api, file_hashes, ocr_endpoint, pdf_source, published, submit


def run(workdir: Path) -> dict:
    source, _ = pdf_source(workdir / "native.pdf")
    with ocr_endpoint() as (url, requests), api(workdir / "data", url) as client:
        first = submit(client, source, "md_single")
        assert first["status"] in {"completed", "completed_with_warnings"}, first
        old_export = published(workdir / "data", first["task_id"])
        snapshot_path = old_export / "book.json"
        assert snapshot_path.is_file(), "PDF 尚未发布可复用的共同正文"
        snapshot = json.loads(snapshot_path.read_text())
        before = file_hashes(old_export)
        from src.application.capabilities import CAPABILITIES
        reader, _ = CAPABILITIES["pdf"].load_reader()
        with patch.object(importlib.import_module(reader.__module__), reader.__name__,
                          side_effect=AssertionError("再次导出不应重跑 PDF Reader")):
            second = client.post("/api/convert", json={"task_id": first["task_id"], "output_format": "html"})
        assert second.status_code == 200, second.text
        state = client.get(f"/api/status/{second.json()['task_id']}").json()
        assert state["status"] in {"completed", "completed_with_warnings"}, state
        new_export = published(workdir / "data", state["task_id"])
        assert json.loads((new_export / "book.json").read_text())["revision_id"] == snapshot["revision_id"]
        assert before == file_hashes(old_export)
        assert client.get(first["download_url"]).status_code == 200
        assert not requests
    return {"checks": ["再次导出引用相同正文修订", "历史文件及下载保持完整且未请求 OCR"]}


if __name__ == "__main__":
    from tests.common.support import run_one

    raise SystemExit(run_one(__file__))
