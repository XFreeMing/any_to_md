"""UC-01-S3 · Epic E1 · US-01 / US-02：让无法解析 PDF 的使用者得到明确失败而非伪产物。

Given 损坏、加密 PDF 两个样本与可记录调用的 OCR 端点。
When 经 Web API TestClient、模块 CLI、目录批处理请求 Markdown / HTML / PDF。
Then Web 任务失败且无下载、保留错误诊断；两种命令行非零退出并提示原因、无产物、无 OCR 请求。
当前 Web 仅验证错误非空，明确解析原因的提示断言仍见 TODO。
"""

if __name__ == "__main__" and not __package__:
    import runpy
    from pathlib import Path

    runpy.run_path(str(Path(__file__).resolve().parents[1] / "common" / "bootstrap.py"))

from pathlib import Path
import pymupdf

from tests.common.conversion_support import api, cli_attempts, diagnostic, ocr_endpoint, submit


def run(workdir: Path) -> dict:
    damaged = workdir / "damaged.pdf"
    damaged.write_bytes(b"%PDF-1.7 broken input")
    encrypted = workdir / "encrypted.pdf"
    with pymupdf.open() as document:
        document.new_page().insert_text((30, 40), "PRIVATE_SOURCE")
        document.save(encrypted, encryption=pymupdf.PDF_ENCRYPT_AES_256, owner_pw="owner", user_pw="password")
    with ocr_endpoint() as (url, requests), api(workdir / "data", url) as client:
        for source in (damaged, encrypted):
            for output in ("md_single", "html", "pdf"):
                state = submit(client, source, output)
                assert state["status"] == "failed", state
                assert state["message"] and state["download_url"] is None
                assert diagnostic(workdir / "data", state["task_id"])["error"]
                assert client.get(f"/api/download/{state['task_id']}").status_code == 404
            for entry, target, process, exports in cli_attempts(source, workdir / source.stem, url):
                message = process.stdout + process.stderr
                assert process.returncode == 1, (entry, target, message)
                expected = ("损坏", "加密", "cannot open", "encrypted", "failed to open")
                assert any(value.lower() in message.lower() for value in expected), (entry, target, message)
                assert not exports, (entry, target, exports)
        assert not requests, "无法解析的 PDF 被送往 OCR"
    return {"checks": ["损坏和加密两例在 API / 模块 CLI / 目录批处理的三种输出请求中均失败", "无 OCR 请求或成功下载"]}


if __name__ == "__main__":
    from tests.common.support import run_one

    raise SystemExit(run_one(__file__))
