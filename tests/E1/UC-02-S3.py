"""UC-02-S3: 外部 OCR 返回非法路径或超配额资源时完整任务失败。"""

if __name__ == "__main__" and not __package__:
    import runpy
    from pathlib import Path

    runpy.run_path(str(Path(__file__).resolve().parents[1] / "common" / "bootstrap.py"))

from pathlib import Path

from tests.common.conversion_support import api, archive, diagnostic, image_source, ocr_endpoint, rejected_cli, submit


def run(workdir: Path) -> dict:
    source = image_source(workdir / "scan.png")
    examples = {
        "path": archive({"result.md": "body", "../../../../escape.txt": "must not be written"}),
        "quota": archive({"result.md": "body", **{f"assets/{index}.txt": "x" for index in range(10001)}}),
    }
    for name, response in examples.items():
        # Keep even the four-parent malicious target inside this disposable workspace.
        data = workdir / name / "isolated" / "data" / "root"
        with ocr_endpoint(response, 200) as (url, requests), api(data, url) as client:
            state = submit(client, source)
            assert requests, "未经过 OCR HTTP 边界"
            assert state["status"] == "failed" and state["download_url"] is None, state
            report = diagnostic(data, state["task_id"])
            assert report["error"]
            assert client.get(f"/api/download/{state['task_id']}").status_code == 404
            rejected_cli(source, workdir / f"{name}-cli", url, ("无效路径", "超出文件数", "展开大小限制"))
        assert not list(workdir.rglob("escape.txt")), "非法资源被落盘"
        assert not list((data / "exports").glob("*")), "失败任务暴露了发布产物"
    return {"checks": ["非法路径与超配额两个真实 HTTP 响应均导致持久失败", "无越界写入或成功下载"]}


if __name__ == "__main__":
    from tests.common.support import run_one

    raise SystemExit(run_one(__file__))
