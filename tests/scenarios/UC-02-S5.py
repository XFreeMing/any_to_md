"""UC-02-S5: 已受理图片在 OCR 服务失败后有可查失败且无下载。"""

if __name__ == "__main__" and not __package__:
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from pathlib import Path

from tests.scenarios.conversion_support import api, diagnostic, image_source, ocr_endpoint, rejected_cli, submit


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
        rejected_cli(source, workdir / "command-line", url, ("503", "OCR 服务不可用", "识别服务不可用"))
    with api(workdir / "data") as restarted:
        saved = restarted.get(f"/api/status/{state['task_id']}").json()
        assert saved["status"] == "failed" and saved["download_url"] is None
    return {"checks": ["503 后已受理任务持久失败并说明原因", "重启后仍无伪成功下载"]}


if __name__ == "__main__":
    from tests.scenarios.support import run_one

    raise SystemExit(run_one(__file__))
