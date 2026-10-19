"""UC-04-S4：浏览器重转已结束任务并保留历史下载（E2 / US-06）。

作为浏览器使用者，我希望为同一份文档重新选择格式，以便保留原结果并获得新结果。
Given HTML 文档已转换为单文件 Markdown，原下载内容的哈希已记录。
When 用户通过页面的重转入口选择 HTML 输出，不重新上传。
Then 页面使用新任务下载正确 HTML，原任务仍可下载且原文件哈希不变。

本场景执行上述一次格式切换；操作入口、任务身份、正文与历史保留均在 run() 断言。
"""

if __name__ == "__main__" and not __package__:
    import runpy
    from pathlib import Path

    runpy.run_path(str(Path(__file__).resolve().parents[1] / "common" / "bootstrap.py"))

import hashlib
import re
from pathlib import Path

from tests.common.browser_support import browser_session, convert, download, upload, write_html_sample


def run(workdir: Path) -> dict:
    with browser_session(workdir) as session:
        page = session.page
        status, uploaded = upload(page, write_html_sample(workdir))
        assert status == 200, f"有效样本上传失败：{uploaded}"
        _, original = convert(page, "Markdown (Single File)")
        page.locator("#resultSection").wait_for(state="visible", timeout=60000)
        assert page.locator("#resultSuccess").is_visible(), page.locator("#errorMessage").inner_text()
        original_path = download(page, workdir / "original")
        original_digest = hashlib.sha256(original_path.read_bytes()).hexdigest()

        repeat = page.get_by_role(
            "button",
            name=re.compile(r"Convert Again|Reconvert|Change (?:Output )?Format|重新转换|再次转换|更换.*格式", re.I),
        )
        assert repeat.count() == 1 and repeat.is_visible(), (
            "页面缺少对已结束任务重新选择输出并转换的入口；New Conversion 会清空任务，"
            "重新上传不能证明已结束任务重转。"
        )
        repeat.click()
        request, replacement = convert(page, "HTML")
        assert request["task_id"] == original["task_id"], "页面没有重新转换原来的已结束任务"
        assert replacement.get("task_id") and replacement["task_id"] != original["task_id"], (
            "重新转换未返回新的任务 ID"
        )
        page.locator("#resultSection").wait_for(state="visible", timeout=60000)
        assert page.locator("#resultSuccess").is_visible(), page.locator("#errorMessage").inner_text()
        new_artifact = download(page, workdir / "replacement")
        assert new_artifact.suffix == ".html", "页面未跟随新任务下载 HTML"
        assert "Paragraph alpha: one visible source statement." in new_artifact.read_text(encoding="utf-8")
        original_status = session.context.request.get(f"{session.base_url}/api/status/{original['task_id']}")
        assert original_status.status == 200 and original_status.json()["download_url"], "原任务或历史下载记录丢失"
        with page.expect_download() as event:
            page.evaluate("url => { window.location.href = url; }", f"{session.base_url}/api/download/{original['task_id']}")
        retained = workdir / "retained.md"
        event.value.save_as(str(retained))
        assert hashlib.sha256(retained.read_bytes()).hexdigest() == original_digest, "历史下载内容被新转换改写"
    return {"checks": ["真实浏览器重新转换已结束任务，页面使用新任务 ID，原任务和历史下载哈希保持不变"]}


if __name__ == "__main__":
    from tests.common.support import run_one

    raise SystemExit(run_one(__file__))
