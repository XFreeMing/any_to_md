"""UC-04-S2：浏览器使用者能理解上传被拒绝的原因（E2 / US-06）。

Given 文件类型不受支持，或文件超过配置的上传大小限制。
When 用户在真实浏览器上传文件。
Then 页面解释具体原因，不提供下载，响应不返回转换任务。

两个 Examples 及其预期由 run() 的 cases 表维护；本场景只验证上传拒绝。
"""

if __name__ == "__main__" and not __package__:
    import runpy
    from pathlib import Path

    runpy.run_path(str(Path(__file__).resolve().parents[1] / "common" / "bootstrap.py"))

from pathlib import Path

from tests.common.browser_support import browser_session, upload


def run(workdir: Path) -> dict:
    checks = []
    with browser_session(workdir, upload_limit=1024) as session:
        page = session.page
        cases = (
            ("unsupported.exe", b"unsupported", 400, "Unsupported file type"),
            ("oversized.html", b"x" * 1025, 413, "configured upload limit"),
        )
        for filename, content, expected_status, explanation in cases:
            if checks:
                page.get_by_role("button", name="Try Again", exact=True).click()
            source = workdir / filename
            source.write_bytes(content)
            status, response = upload(page, source)
            page.locator("#resultSection").wait_for(state="visible")
            assert status == expected_status, f"上传应被拒绝：{filename}，响应 {response}"
            assert page.locator("#resultError").is_visible(), "页面未呈现上传失败"
            assert explanation in page.locator("#errorMessage").inner_text(), "页面没有说明具体拒绝原因"
            assert not page.get_by_role("button", name="Download Result", exact=True).is_visible()
            assert "task_id" not in response, "被拒绝的上传仍创建了转换任务"
            checks.append(f"真实 Chrome：{filename} 被拒绝，页面解释原因且不提供下载")
    return {"checks": checks}


if __name__ == "__main__":
    from tests.common.support import run_one

    raise SystemExit(run_one(__file__))
