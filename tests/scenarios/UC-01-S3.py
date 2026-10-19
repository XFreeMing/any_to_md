"""UC-01-S3: 损坏和加密 PDF 在受理后明确失败，不调用 OCR。"""

if __name__ == "__main__" and not __package__:
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from pathlib import Path
import pymupdf

from tests.scenarios.conversion_support import api, diagnostic, ocr_endpoint, rejected_cli, submit


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
            rejected_cli(source, workdir / source.stem, url, ("损坏", "加密", "cannot open", "encrypted", "failed to open"))
        assert not requests, "无法解析的 PDF 被送往 OCR"
    return {"checks": ["损坏和加密两个 Examples 在所有入口均明确失败", "无 OCR 请求或成功下载"]}


if __name__ == "__main__":
    from tests.scenarios.support import run_one

    raise SystemExit(run_one(__file__))
