"""UC-02-S5 · Epic E1 · US-01 / US-03：让图片使用者在 OCR 故障后查到失败原因。

Given PNG 输入和返回 HTTP 503 的受控 OCR 服务。
When Web API TestClient 转 Markdown，模块 CLI / 目录批处理请求三种输出，再重新打开 TestClient。
Then Web 状态与诊断保留 503、无下载且重新打开 TestClient 后仍可查询失败；命令行退出 1、说明原因且无产物。
当前仅 PNG / Web Markdown；明确识别服务不可用的提示及其他适用组合仍见 TODO。
"""

if __name__ == "__main__" and not __package__:
    import runpy
    from pathlib import Path

    runpy.run_path(str(Path(__file__).resolve().parents[1] / "common" / "bootstrap.py"))

from pathlib import Path

from tests.common.conversion_support import api, cli_attempts, diagnostic, image_source, ocr_endpoint, submit


def run(workdir: Path) -> dict:
    source = image_source(workdir / "scan.png")
    with ocr_endpoint() as (url, requests), api(workdir / "data", url) as client:
        state = submit(client, source)
        assert requests, "识别未实际请求外部 HTTP 服务"
        assert state["status"] == "failed", state
        assert state["download_url"] is None
        assert "503" in state["message"], state
        assert "503" in diagnostic(workdir / "data", state["task_id"])["error"]
        assert client.get(f"/api/download/{state['task_id']}").status_code == 404
        for entry, target, process, exports in cli_attempts(source, workdir / "command-line", url):
            message = process.stdout + process.stderr
            assert process.returncode == 1, (entry, target, message)
            expected = ("503", "OCR 服务不可用", "识别服务不可用")
            assert any(value.lower() in message.lower() for value in expected), (entry, target, message)
            assert not exports, (entry, target, exports)
    with api(workdir / "data") as restarted:
        saved = restarted.get(f"/api/status/{state['task_id']}").json()
        assert saved["status"] == "failed" and saved["download_url"] is None
    return {"checks": ["503 后已受理任务持久失败并说明原因", "重新打开 TestClient 后仍可查询失败且无下载"]}


if __name__ == "__main__":
    from tests.common.support import run_one

    raise SystemExit(run_one(__file__))
