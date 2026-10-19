"""UC-04-S1：在浏览器完成转换并获得可阅读文件（E2 / US-06；PDF / 图片关联 US-02 / US-03）。

作为浏览器使用者，我希望提交文档后下载所选格式，以便无需命令行就能阅读结果。
Given 独立源样本覆盖能力表中的输入后缀，真实 OCR、浏览器与 PDF 依赖可用。
When 在真实浏览器逐一提交样本、选择每种支持的输出并等待转换后下载。
Then 页面与任务一致，下载正文和资源符合源样本预期，并有匹配产物的人工阅读结论。

本场景验证本机 Web 全矩阵；源预期来自 common/cases.json，补充后缀在 run() 展开。
PDF 表格、链接与布局依赖人工判断；部署环境不在本场景中验证。
"""

if __name__ == "__main__" and not __package__:
    import runpy
    from pathlib import Path

    runpy.run_path(str(Path(__file__).resolve().parents[1] / "common" / "bootstrap.py"))

import json
import os
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from tests.common.browser_support import browser_session, convert, download, upload, write_html_sample
from tests.common.delivery_support import extract_download, fixture_cases, require_visual_evidence, source_server
from tests.common.support import Blocked
from tests.common.fixtures import _archive


def run(workdir: Path) -> dict:
    try:
        import weasyprint  # noqa: F401 — Given: the selected PDF renderer is available.
    except (ImportError, OSError) as error:
        raise Blocked(f"PDF 输出的运行依赖不可用：{error}") from error
    from PIL import Image
    from src.application.capabilities import CAPABILITIES
    from tests.common.inspection import inspect_output, inspect_pdf

    labels = {"md_single": "Markdown (Single File)", "md_chapters": "Markdown (Chapters)",
              "html": "HTML", "pdf": "PDF"}
    # 共用独立样本预期；浏览器特有的后缀覆盖、任务状态和下载契约在下文断言。
    samples = fixture_cases(workdir / "inputs")
    # 浏览器不能同时上传兄弟文件，因此富 HTML 与依赖资源打包提交。
    rich_source, _ = next((source, case) for source, case in samples if case["kind"] == "html")
    bundle = rich_source.with_suffix(".zip")
    members = {rich_source.name: rich_source.read_bytes()}
    members.update({resource.relative_to(rich_source.parent).as_posix(): resource.read_bytes()
                    for resource in sorted((rich_source.parent / "assets").rglob("*"))
                    if resource.is_file()})
    _archive(bundle, members)
    samples = [(bundle, case) if source == rich_source else (source, case) for source, case in samples]
    simple = write_html_sample(workdir)
    simple_case = {"id": "html-standalone", "kind": "html", "requires_ocr": False,
                   "markers": ["Paragraph alpha: one visible source statement.",
                               "Paragraph beta: the final source statement."],
                   "visual_checks": ["Standalone HTML headings and paragraphs are legible"]}
    samples.append((simple, simple_case))
    alternate_html = simple.with_suffix(".htm")
    alternate_html.write_bytes(simple.read_bytes())
    samples.append((alternate_html, dict(simple_case, id="htm-standalone")))
    image_source, image_case = next((source, case) for source, case in samples if case["kind"] == "image")
    for suffix in CAPABILITIES["image"].suffixes:
        if suffix == image_source.suffix:
            continue
        variant = image_source.with_suffix(suffix)
        with Image.open(image_source) as picture:
            picture.convert("RGB").save(variant)
        samples.append((variant, dict(image_case, id=f"image-{suffix[1:]}")))

    ocr_reason = None
    ocr_url = os.environ.get("ANY_TO_MD_SCENARIO_OCR_URL")
    if not ocr_url:
        ocr_reason = "缺少 ANY_TO_MD_SCENARIO_OCR_URL 指向的真实 OCR 服务"
    else:
        try:
            with urlopen(Request(ocr_url, method="OPTIONS"), timeout=3):
                pass
        except HTTPError as error:
            if error.code not in (404, 405):
                ocr_reason = f"真实 OCR 服务尚不可用：HTTP {error.code}"
        except (URLError, TimeoutError) as error:
            ocr_reason = f"真实 OCR 服务不可连接：{error}"

    checks, observations, exports, sources, visual_checks, blocked_cases = [], [], [], [], set(), []
    with source_server(workdir / "inputs", int(os.environ.get("ANY_TO_MD_FIXTURE_PORT", "18763"))) as source_url, browser_session(workdir, upload_limit=10 * 1024 * 1024) as session:
        page = session.page
        article, article_case = next((source, case) for source, case in samples if case["kind"] == "url")
        pointer = article.with_suffix(".url")
        pointer.write_text(f"{source_url}/{article.name}", encoding="utf-8")
        samples.append((pointer, dict(article_case, id="url-file")))
        for kind, capability in CAPABILITIES.items():
            covered = {source.suffix for source, case in samples if case["kind"] == kind}
            assert set(capability.suffixes) <= covered, f"浏览器矩阵缺少 {kind} 样本：{set(capability.suffixes) - covered}"
        for source, case in samples:
            if case.get("requires_ocr") and ocr_reason:
                blocked_cases.append(case["id"])
                continue
            sources.append(source)
            visual_checks.update(case.get("visual_checks", []))
            capability = CAPABILITIES[case["kind"]]
            for view in capability.outputs:
                if checks:
                    page.get_by_role("button", name="New Conversion", exact=True).click()
                if case["kind"] == "url" and source.suffix != ".url":
                    page.get_by_placeholder("Enter webpage URL").fill(f"{source_url}/{source.name}")
                    with page.expect_response(lambda response: response.url.endswith("/api/fetch")) as event:
                        page.get_by_role("button", name="Fetch", exact=True).click()
                    upload_status, uploaded = event.value.status, event.value.json()
                else:
                    upload_status, uploaded = upload(page, source)
                assert upload_status == 200, f"{case['id']} 输入未受理：{uploaded}"
                assert uploaded.get("task_id"), "输入没有返回持久任务 ID"
                assert set(uploaded["available_outputs"]) == set(capability.outputs), "页面输出选项与能力表不一致"
                request, queued = convert(page, labels[view])
                assert request["task_id"] == uploaded["task_id"], "页面没有转换刚受理的输入"
                assert queued.get("task_id"), f"转换没有返回供页面查询的任务 ID：{queued}"
                page.locator("#resultSection").wait_for(state="visible", timeout=180000)
                assert page.locator("#resultSuccess").is_visible(), f"{case['id']}/{view}: {page.locator('#errorMessage').inner_text()}"
                assert page.get_by_role("button", name="Download Result", exact=True).is_visible()
                destination = workdir / "downloads" / case["id"] / view
                artifact = download(page, destination)
                suffix = ".pdf" if view == "pdf" else ".html" if view == "html" else ".md"
                assert artifact.suffix in (suffix, ".zip"), f"所选输出 {view} 与下载 {artifact.name} 不对应"
                assert artifact.stat().st_size > 0, "浏览器下载的产物为空"
                readable = extract_download(artifact.read_bytes(), destination / "readable", artifact.name)
                if view == "md_chapters":
                    paths = sorted((readable / "chapters").glob("*.md"))
                else:
                    paths = sorted(readable.rglob(f"*{suffix}"))
                    assert len(paths) == 1, f"{case['id']}/{view} 没有唯一主阅读文件：{paths}"
                # 共用检查器按 case 核对正文顺序、图片及适用的表格/链接/代码预期。
                # PDF 自动检查只覆盖正文和图片，阅读证据在全部组合执行后统一核对。
                result = inspect_pdf(paths[0], case) if view == "pdf" else inspect_output(paths, case, readable)
                assert result["passed"], {"sample": case["id"], "view": view, "failures": result["failures"]}
                if view == "html":
                    preview = session.context.new_page()
                    try:
                        preview.goto(paths[0].as_uri(), wait_until="load")
                        assert preview.locator("body").inner_text().strip(), "HTML 在真实浏览器中无法阅读"
                        preview.screenshot(path=str(destination / "reading.png"), full_page=True)
                    finally:
                        preview.close()
                elif view == "pdf":
                    import pymupdf

                    with pymupdf.open(paths[0]) as document:
                        assert document.page_count > 0, "PDF 没有可阅读页面"
                        document[0].get_pixmap().save(str(destination / "first-page.png"))
                checks.append(f"真实 Chrome：{case['id']}→{view}→轮询终态→下载；独立预期正文、资源与引用检查通过")
                observations.append({"case": case["id"], "view": view, "inspection": result})
                exports.append(readable)
                (workdir / "technical-checks.json").write_text(
                    json.dumps(observations, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
                )

    limitations = []
    if blocked_cases:
        limitations.append(f"{ocr_reason}；未执行：{', '.join(blocked_cases)}")
    try:
        require_visual_evidence("UC-04-S1", sources, exports, sorted(visual_checks), technical_checks=checks)
    except Blocked as error:
        if not blocked_cases:
            raise
        limitations.append(str(error))
    if limitations:
        error = Blocked("；".join(limitations))
        error.technical_checks = checks
        error.limitations = limitations
        raise error
    return {"checks": checks + ["全部受支持 Web 输入/输出组合通过，当前样本与产物已有署名的人工阅读结论"]}


if __name__ == "__main__":
    from tests.common.support import run_one

    raise SystemExit(run_one(__file__))
