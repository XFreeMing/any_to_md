"""UC-09-S1 · E5 / US-13：重转一本书只保留一份当前 Markdown。

Given 同一来源连续转换，以及一次未发布的失败导出和暂存残留。
When 再次转换，并按书名、作者、原始文件名或 book_id 定位当前语料。
Then 当前指针只有一个；相同 Markdown 仍指向已有导出，失败或暂存目录不会成为当前。
And library/<书名> 在数据目录之外，打开 book.md、TOC.md、chapters/ 和 manifest.json。
历史导出、原件、修订和失败暂存都还在。
"""

if __name__ == "__main__" and not __package__:
    import runpy
    from pathlib import Path

    runpy.run_path(str(Path(__file__).resolve().parents[1] / "common" / "bootstrap.py"))

import json
import os
import shutil
import sqlite3
import subprocess
import sys
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from src.application.convert import ConversionRequest, convert
from src.application.jobs import ConversionFailure, JobRuntime
from src.application.library import list_current
from tests.common.delivery_support import subprocess_environment


def _html(title: str, author: str, heading: str) -> str:
    return (
        "<!DOCTYPE html><html><head><meta charset=\"utf-8\">"
        f"<title>{title}</title><meta name=\"author\" content=\"{author}\"></head>"
        f"<body><h1>{heading}</h1><p>{heading} 的正文。</p></body></html>"
    )


def _rows(data: Path, sql: str, params: tuple = ()) -> list[dict]:
    with closing(sqlite3.connect(data / "catalog.sqlite")) as connection:
        connection.row_factory = sqlite3.Row
        return [dict(row) for row in connection.execute(sql, params)]


def _convert(path: Path, data: Path):
    result = convert(ConversionRequest(path, data, ("markdown",)))
    assert result.status in {"completed", "completed_with_warnings"}, result.status
    for name in ("book.md", "TOC.md", "manifest.json"):
        assert (result.output_dir / name).is_file()
    assert any((result.output_dir / "chapters").glob("*.md"))
    return result


def _fail_once(path: Path, data: Path) -> str:
    with patch("src.application.convert.write_bundle", side_effect=PermissionError(13, "Access is denied")):
        try:
            convert(ConversionRequest(path, data, ("markdown",)))
        except ConversionFailure as exc:
            failed = JobRuntime(data).get(exc.job_id)
        else:
            raise AssertionError("未发布的失败转换不应成功")
    assert failed is not None and failed.status.value == "failed"
    assert failed.storage_key is None and failed.export_id
    assert (data / "staging" / failed.export_id).is_dir()
    assert not (data / "exports" / failed.export_id).exists()
    return failed.export_id


def _cli(workdir: Path, data: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    environment = subprocess_environment()
    environment["PYTHONUTF8"] = "1"
    environment["PYTHONIOENCODING"] = "utf-8"
    return subprocess.run(
        [sys.executable, "-m", "src.interfaces.cli.library", "--data-dir", str(data), *arguments],
        cwd=workdir,
        env=environment,
        capture_output=True,
        text=True,
        timeout=60,
    )


def run(workdir: Path) -> dict:
    data = workdir / "var"
    sources = workdir / "sources"
    sources.mkdir()
    wired_input = sources / "wired-for-story.html"
    bencao_input = sources / "本草纲目（全四册）.html"
    shanhai_input = sources / "山海经.html"
    wired_input.write_text(_html("Wired for Story", "Lisa Cron", "Opening"), encoding="utf-8")
    bencao_input.write_text(_html("本草纲目（全四册）", "李时珍", "草部"), encoding="utf-8")
    shanhai_input.write_text(_html("山海经(全译插图典藏版)【中国上古奇幻巨著】", "小岩井", "南山经"), encoding="utf-8")

    first = _convert(wired_input, data)
    second = _convert(wired_input, data)
    assert first.export_id != second.export_id
    assert first.revision_id == second.revision_id
    assert (first.output_dir / "book.md").read_bytes() == (second.output_dir / "book.md").read_bytes()
    assert (first.output_dir / "TOC.md").read_bytes() == (second.output_dir / "TOC.md").read_bytes()
    first_chapters = {path.name: path.read_bytes() for path in (first.output_dir / "chapters").glob("*.md")}
    second_chapters = {path.name: path.read_bytes() for path in (second.output_dir / "chapters").glob("*.md")}
    assert first_chapters == second_chapters
    assert json.loads((first.output_dir / "manifest.json").read_text(encoding="utf-8"))["export_id"] != second.export_id
    wired_pointer = _rows(data, "SELECT export_id FROM current_exports WHERE book_id=?", (first.book_id,))
    assert wired_pointer == [{"export_id": first.export_id}]
    assert first.output_dir.is_dir() and second.output_dir.is_dir()

    failed_id = _fail_once(bencao_input, data)
    failed_row = _rows(data, "SELECT status, storage_key, published_at FROM exports WHERE export_id=?", (failed_id,))
    assert failed_row == [{"status": "failed", "storage_key": None, "published_at": None}]
    assert not _rows(data, "SELECT export_id FROM current_exports WHERE export_id=?", (failed_id,))
    bencao = _convert(bencao_input, data)
    assert bencao.export_id != failed_id
    assert _rows(data, "SELECT export_id FROM current_exports WHERE book_id=?", (bencao.book_id,)) == [
        {"export_id": bencao.export_id}
    ]
    assert (data / "staging" / failed_id).is_dir()
    later_failure = _fail_once(bencao_input, data)
    assert later_failure != bencao.export_id
    assert _rows(data, "SELECT export_id FROM current_exports WHERE book_id=?", (bencao.book_id,)) == [
        {"export_id": bencao.export_id}
    ]

    shanhai = _convert(shanhai_input, data)
    listed, missing = list_current(data)
    by_id = {book.book_id: book for book in listed}
    assert not missing
    assert by_id[first.book_id].export_id == first.export_id
    assert by_id[bencao.book_id].export_id == bencao.export_id
    assert by_id[shanhai.book_id].export_id == shanhai.export_id
    assert {book.library_name for book in listed} == {"Wired-for-Story", "本草纲目", "山海经"}

    linked = _cli(workdir, data, "--link")
    assert linked.returncode == 0, linked.stderr
    library = workdir / "library"
    assert library == data.parent / "library" and not (data / "library").exists()
    for result, name in ((first, "Wired-for-Story"), (bencao, "本草纲目"), (shanhai, "山海经")):
        link = library / name
        assert link.is_symlink()
        assert link.resolve() == result.output_dir.resolve()
        assert os.readlink(link).endswith(result.export_id)
        for entry in ("book.md", "TOC.md", "manifest.json"):
            assert (link / entry).is_file()
        assert any((link / "chapters").glob("*.md"))
        assert result.export_id in linked.stdout and failed_id not in linked.stdout
    for query in (first.book_id, "Lisa Cron", "wired-for-story.html", "Wired for Story"):
        found = _cli(workdir, data, "--query", query)
        assert found.returncode == 0, (query, found.stderr)
        assert first.export_id in found.stdout and second.export_id not in found.stdout
    by_author = _cli(workdir, data, "--query", "李时珍")
    assert by_author.returncode == 0 and bencao.export_id in by_author.stdout and failed_id not in by_author.stdout
    rejected = _cli(workdir, data, "--link", "--library-dir", str(data / "library"))
    assert rejected.returncode != 0 and not (data / "library").exists()

    with closing(sqlite3.connect(data / "catalog.sqlite")) as connection:
        connection.execute("DELETE FROM current_exports WHERE book_id=?", (first.book_id,))
        decoy = "decoy-staging-export"
        shutil.copytree(second.output_dir, data / "staging" / decoy)
        connection.execute(
            """INSERT INTO jobs(job_id, status, created_at, filename, input_format, targets_json, progress, message)
               VALUES('decoy-job', 'failed', '2099-01-01T00:00:00+00:00', 'decoy.html', 'html', '[]', 0, 'decoy')"""
        )
        connection.execute(
            """INSERT INTO exports(export_id, revision_id, job_id, config_json, status, storage_key, created_at, published_at)
               VALUES(?, ?, 'decoy-job', '{}', 'completed', ?, '2099-01-01T00:00:00+00:00', '2099-01-01T00:00:00+00:00')""",
            (decoy, first.revision_id, f"staging/{decoy}"),
        )
        connection.commit()
    legacy, _missing = list_current(data)
    legacy_wired = next(book for book in legacy if book.book_id == first.book_id)
    assert legacy_wired.export_id == second.export_id
    assert decoy != legacy_wired.export_id and failed_id != legacy_wired.export_id
    refreshed = _cli(workdir, data, "--link")
    assert refreshed.returncode == 0, refreshed.stderr
    assert (library / "Wired-for-Story").resolve() == second.output_dir.resolve()
    assert first.output_dir.is_dir() and (data / "staging" / failed_id).is_dir() and (data / "staging" / decoy).is_dir()
    assert any((data / "sources").glob("*")) and any((data / "revisions").glob("*"))
    return {
        "checks": [
            "同一修订连续两次完成导出只保留一个当前指针，相同 Markdown 仍指向第一次的目录",
            "失败且未发布的导出和暂存目录不会成为当前，成功重试后暂存残留仍在",
            "没有指针时选择最新的已发布完成导出，并忽略失败导出和暂存路径",
            "library/<书名> 位于数据目录之外，能打开 book.md、TOC.md、chapters/ 和 manifest.json",
        ]
    }


if __name__ == "__main__":
    from tests.common.support import run_one

    raise SystemExit(run_one(__file__))
