"""UC-02-S3 · Epic E2 · US-01 / US-03：拒绝不安全的 OCR 资源，保护使用者的文件。

Given PNG 输入，受控 OCR HTTP 响应分别含越界路径或超过配额的资源包。
When Web API TestClient 转 Markdown，模块 CLI / 目录批处理请求三种输出。
Then Web 任务持久失败且无下载，命令行退出 1 并说明原因；不发布产物、不写出越界文件。
当前仅路径 / 配额两例及 PNG；具体原因、其他协议异常和适用组合仍见 TODO。
"""

if __name__ == "__main__" and not __package__:
    import runpy
    from pathlib import Path

    runpy.run_path(str(Path(__file__).resolve().parents[1] / "common" / "bootstrap.py"))

from pathlib import Path

from tests.common.conversion_support import api, archive, cli_attempts, diagnostic, image_source, ocr_endpoint, submit


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
            for entry, target, process, exports in cli_attempts(source, workdir / f"{name}-cli", url):
                message = process.stdout + process.stderr
                assert process.returncode == 1, (entry, target, message)
                expected = ("无效路径", "超出文件数", "展开大小限制")
                assert any(value.lower() in message.lower() for value in expected), (entry, target, message)
                assert not exports, (entry, target, exports)
        assert not list(workdir.rglob("escape.txt")), "非法资源被落盘"
        assert not list((data / "exports").glob("*")), "失败任务暴露了发布产物"
    return {"checks": ["非法路径与超配额两个受控 HTTP 响应均导致持久失败", "无越界写入或成功下载"]}


if __name__ == "__main__":
    from tests.common.support import run_one

    raise SystemExit(run_one(__file__))
