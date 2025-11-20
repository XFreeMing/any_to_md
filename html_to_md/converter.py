"""HTML to Markdown conversion utilities supporting single and multi-file outputs."""

from __future__ import annotations

import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import List, Sequence
from urllib.parse import urlparse

from bs4 import BeautifulSoup, Tag
from markdownify import markdownify as to_markdown


@dataclass
class MarkdownDocument:
    """Represents a generated Markdown artifact."""

    index: int
    title: str
    content: str
    file_name: str
    output_path: Path


def _sanitize_text(value: str) -> str:
    cleaned = re.sub(r"\s+", " ", value or "").strip()
    return cleaned


def _slugify(value: str) -> str:
    text = _sanitize_text(value)
    slug = re.sub(r"[^0-9A-Za-z]+", "-", text).strip("-")
    return slug.lower()


def _read_html(path: Path) -> BeautifulSoup:
    html_text = path.read_text(encoding="utf-8")
    return BeautifulSoup(html_text, "html.parser")


def _section_nodes(body: Tag | BeautifulSoup) -> Sequence[Tag]:
    sections: List[Tag] = []
    for child in body.children:
        if isinstance(child, Tag) and child.name == "section":
            sections.append(child)
    if sections:
        return sections
    if isinstance(body, Tag):
        return [body]
    if body.body:
        return [body.body]
    return [body]


def _section_title(section: Tag, index: int) -> str:
    for level in ["h1", "h2", "h3", "h4", "h5", "h6"]:
        heading = section.find(level)
        if heading and heading.get_text(strip=True):
            return heading.get_text(strip=True)
    dataset_title = section.get("data-title") or section.get("data-source")
    if dataset_title:
        return _sanitize_text(dataset_title)
    return f"Section {index}"


def _ensure_output_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def _copy_assets(html_path: Path, output_root: Path) -> Path | None:
    source_assets = html_path.parent / f"{html_path.stem}_assets"
    if not source_assets.is_dir():
        return None
    destination = output_root / "assets"
    shutil.copytree(source_assets, destination, dirs_exist_ok=True)
    return destination


def _relativize_asset(
    url_value: str,
    html_dir: Path,
    source_assets: Path,
    destination_assets: Path,
    output_parent: Path,
) -> str:
    parsed = urlparse(url_value)
    if parsed.scheme and parsed.scheme not in ("", "data"):
        return url_value
    if parsed.scheme == "data" or parsed.netloc:
        return url_value
    path_part = parsed.path
    if not path_part:
        return url_value
    candidate = (html_dir / Path(path_part)).resolve()
    try:
        rel_inside = candidate.relative_to(source_assets.resolve())
    except ValueError:
        return url_value
    target = (destination_assets / rel_inside).resolve()
    relative_path = os.path.relpath(target, output_parent)
    posix_relative = PurePosixPath(relative_path).as_posix()
    rebuilt = posix_relative
    if parsed.query:
        rebuilt = f"{rebuilt}?{parsed.query}"
    if parsed.fragment:
        rebuilt = f"{rebuilt}#{parsed.fragment}"
    return rebuilt


def _rewrite_media_urls(
    soup: BeautifulSoup,
    html_path: Path,
    destination_assets: Path | None,
    output_parent: Path,
) -> None:
    html_dir = html_path.parent
    source_assets = html_dir / f"{html_path.stem}_assets"
    if not source_assets.is_dir() or destination_assets is None:
        return
    for img in soup.find_all("img"):
        src = img.get("src")
        if src:
            img["src"] = _relativize_asset(
                src,
                html_dir,
                source_assets,
                destination_assets,
                output_parent,
            )
        srcset = img.get("srcset")
        if srcset:
            rewritten: List[str] = []
            for candidate in srcset.split(","):
                chunk = candidate.strip()
                if not chunk:
                    continue
                if " " in chunk:
                    path_part, descriptor = chunk.split(" ", 1)
                else:
                    path_part, descriptor = chunk, ""
                new_path = _relativize_asset(
                    path_part,
                    html_dir,
                    source_assets,
                    destination_assets,
                    output_parent,
                )
                rewritten.append(f"{new_path} {descriptor}".strip())
            if rewritten:
                img["srcset"] = ", ".join(rewritten)
    for anchor in soup.find_all("a"):
        href = anchor.get("href")
        if not href or href.startswith("#"):
            continue
        parsed = urlparse(href)
        if parsed.scheme and parsed.scheme not in ("", "data"):
            continue
        if parsed.netloc:
            continue
        new_href = _relativize_asset(
            href,
            html_dir,
            source_assets,
            destination_assets,
            output_parent,
        )
        anchor["href"] = new_href


def _fragment_html(section: Tag) -> str:
    return str(section)


def _fragment_to_markdown(fragment_html: str) -> str:
    markdown = to_markdown(
        fragment_html,
        heading_style="ATX",
        bullets="-",
        escape_underscores=False,
        escape_asterisks=False,
    )
    return markdown.strip()


def _write_markdown(output_path: Path, content: str) -> None:
    output_path.write_text(content + "\n", encoding="utf-8")


def convert_html_to_markdown_chapters(
    html_path: Path,
    *,
    output_root: Path | None = None,
) -> List[MarkdownDocument]:
    html_path = html_path.resolve()
    soup = _read_html(html_path)
    body = soup.body or soup
    sections = _section_nodes(body)
    if output_root is None:
        output_root = html_path.parent / f"{html_path.stem}_md"
    chapters_dir = output_root / "chapters"
    _ensure_output_dir(chapters_dir)
    destination_assets = _copy_assets(html_path, output_root)

    documents: List[MarkdownDocument] = []
    for index, section in enumerate(sections, start=1):
        title = _section_title(section, index)
        slug = _slugify(title) or f"section-{index:04d}"
        file_name = f"{index:04d}-{slug}.md"
        output_path = chapters_dir / file_name
        fragment = BeautifulSoup(_fragment_html(section), "html.parser")
        _rewrite_media_urls(fragment, html_path, destination_assets, output_path.parent)
        markdown = _fragment_to_markdown(str(fragment))
        _write_markdown(output_path, markdown)
        documents.append(
            MarkdownDocument(
                index=index,
                title=title,
                content=markdown,
                file_name=file_name,
                output_path=output_path,
            )
        )
    return documents


def convert_html_to_markdown_single(
    html_path: Path,
    *,
    output_root: Path | None = None,
) -> MarkdownDocument:
    html_path = html_path.resolve()
    soup = _read_html(html_path)
    body = soup.body or soup
    if output_root is None:
        output_root = html_path.parent / f"{html_path.stem}_md"
    single_dir = output_root / "single"
    _ensure_output_dir(single_dir)
    destination_assets = _copy_assets(html_path, output_root)

    output_path = single_dir / f"{html_path.stem}.md"
    working = BeautifulSoup(str(body), "html.parser")
    _rewrite_media_urls(working, html_path, destination_assets, output_path.parent)
    markdown = _fragment_to_markdown(str(working))
    _write_markdown(output_path, markdown)
    return MarkdownDocument(
        index=1,
        title=_sanitize_text(soup.title.string if soup.title else html_path.stem),
        content=markdown,
        file_name=output_path.name,
        output_path=output_path,
    )


__all__ = [
    "MarkdownDocument",
    "convert_html_to_markdown_chapters",
    "convert_html_to_markdown_single",
]
