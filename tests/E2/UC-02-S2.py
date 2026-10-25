"""UC-02-S2 · Epic E2 · US-01 / US-03：让图片使用者及时知道输入类型不受支持。

Given 一个不受支持的 TIFF 输入与可记录调用的 OCR 端点。
When 经 Web API TestClient 上传，并通过模块 CLI / 目录批处理请求三种输出。
Then Web 返回 400 且无任务编号，命令行退出 1 并说明不支持；无 OCR 请求、无发布产物。
当前只核对拒绝原因；提示中列明支持范围的断言仍见 TODO。
"""

if __name__ == "__main__" and not __package__:
    import runpy
    from pathlib import Path

    runpy.run_path(str(Path(__file__).resolve().parents[1] / "common" / "bootstrap.py"))

from pathlib import Path

from tests.common.conversion_support import api, cli_attempts, ocr_endpoint


def run(workdir: Path) -> dict:
    source = workdir / "unsupported.tiff"
    source.write_bytes(b"unsupported image")
    with ocr_endpoint() as (url, requests), api(workdir / "data", url) as client:
        response = client.post("/api/upload", files={"file": ("unsupported.tiff", b"unsupported image")})
        assert response.status_code == 400, response.text
        assert "Unsupported" in response.json()["detail"]
        assert "task_id" not in response.json()
        for entry, target, process, exports in cli_attempts(source, workdir / "command-line", url):
            message = process.stdout + process.stderr
            assert process.returncode == 1, (entry, target, message)
            expected = ("不支持", "未找到支持的输入")
            assert any(value.lower() in message.lower() for value in expected), (entry, target, message)
            assert not exports, (entry, target, exports)
        assert not requests
        assert not list((workdir / "data" / "exports").glob("*"))
    return {"checks": ["不支持的类型被入口拒绝并提示原因", "无 OCR 请求、Web 任务编号或发布产物"]}


if __name__ == "__main__":
    from tests.common.support import run_one

    raise SystemExit(run_one(__file__))
