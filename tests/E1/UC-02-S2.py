"""UC-02-S2: 不支持的图片类型在入口拒绝，不触发识别。"""

if __name__ == "__main__" and not __package__:
    import runpy
    from pathlib import Path

    runpy.run_path(str(Path(__file__).resolve().parents[1] / "common" / "bootstrap.py"))

from pathlib import Path

from tests.common.conversion_support import api, ocr_endpoint, rejected_cli


def run(workdir: Path) -> dict:
    source = workdir / "unsupported.tiff"
    source.write_bytes(b"unsupported image")
    with ocr_endpoint() as (url, requests), api(workdir / "data", url) as client:
        response = client.post("/api/upload", files={"file": ("unsupported.tiff", b"unsupported image")})
        assert response.status_code == 400, response.text
        assert "Unsupported" in response.json()["detail"]
        assert "task_id" not in response.json()
        rejected_cli(source, workdir / "command-line", url, ("不支持", "未找到支持的输入"))
        assert not requests
        assert not list((workdir / "data" / "exports").glob("*"))
    return {"checks": ["不支持的类型被入口拒绝并提示原因", "没有 OCR 请求、转换任务或下载产物"]}


if __name__ == "__main__":
    from tests.common.support import run_one

    raise SystemExit(run_one(__file__))
