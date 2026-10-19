"""UC-04-S3: a corrupt document reaches an understandable browser failure."""

if __name__ == "__main__" and not __package__:
    import runpy
    from pathlib import Path

    runpy.run_path(str(Path(__file__).resolve().parents[1] / "common" / "bootstrap.py"))

from pathlib import Path

from tests.common.browser_support import browser_session, convert, upload


def run(workdir: Path) -> dict:
    with browser_session(workdir) as session:
        page = session.page
        source = workdir / "corrupt.docx"
        source.write_bytes(b"This is deliberately not a valid Word document package.")
        status, uploaded = upload(page, source)
        assert status == 200 and uploaded.get("task_id"), "损坏文档未进入已受理任务，未覆盖转换失败路径"
        _, queued = convert(page, "Markdown (Single File)")
        assert queued.get("task_id"), f"转换未受理：{queued}"
        page.locator("#resultSection").wait_for(state="visible", timeout=60000)
        assert page.locator("#resultError").is_visible(), "损坏 DOCX 被报告为成功"
        message = page.locator("#errorMessage").inner_text().strip()
        assert message, "页面没有提供失败原因"
        assert any(term in message.lower() for term in ("docx", "zip", "word", "损坏", "解析", "无效")), (
            f"页面未提供与损坏文档有关的原因：{message}"
        )
        assert not page.get_by_role("button", name="Download Result", exact=True).is_visible()
        result = session.context.request.get(f"{session.base_url}/api/status/{queued['task_id']}")
        assert result.status == 200
        task = result.json()
        assert task["status"] == "failed", f"页面错误与持久任务终态不一致：{task}"
        assert task["download_url"] is None, "失败任务仍暴露成功下载"
    return {"checks": ["真实 Chrome：已上传的损坏 DOCX 转换失败，页面说明原因，持久任务失败且无下载"]}


if __name__ == "__main__":
    from tests.common.support import run_one

    raise SystemExit(run_one(__file__))
