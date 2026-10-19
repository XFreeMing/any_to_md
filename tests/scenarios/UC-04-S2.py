"""UC-04-S2: browser feedback for unsupported and oversized uploads."""

if __name__ == "__main__" and not __package__:
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from pathlib import Path

from tests.scenarios.browser_support import browser_session, upload


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
    from tests.scenarios.support import run_one

    raise SystemExit(run_one(__file__))
