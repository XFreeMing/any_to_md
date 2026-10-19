"""Resolve a canonical fragment for one planned output location."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from urllib.parse import quote

from bs4 import BeautifulSoup

from ..domain.artifacts import ExportPlan
from ..domain.book import BookRevision, ContentFragment


@dataclass
class RenderContext:
    book: BookRevision
    plan: ExportPlan
    heading_bases: dict[str, int] = field(init=False, default_factory=dict)

    def __post_init__(self):
        for fragment in self.book.content:
            soup = BeautifulSoup(fragment.html, "html.parser")
            levels = [int(tag.name[1]) for tag in soup.find_all(re.compile(r"^h[1-6]$"))]
            if levels:
                self.heading_bases[fragment.node_id] = min(self.heading_bases.get(fragment.node_id, 6), *levels)

    def resources(self, text: str, output_path: str) -> str:
        def replace(match: re.Match[str]) -> str:
            asset = self.book.assets.get(match.group(1))
            if asset is None:
                raise ValueError(f"规范内容引用未知资源：{match.group(1)}")
            target = f"assets/{asset.file_name}"
            relative = PurePosixPath(os.path.relpath(target, PurePosixPath(output_path).parent)).as_posix()
            return quote(relative, safe="/._-")
        return re.sub(r"asset://([0-9a-f]{64})", replace, text)

    def fragment(self, fragment: ContentFragment, output_path: str, view: str, heading_level: int) -> str:
        soup = BeautifulSoup(self.resources(fragment.html, output_path), "html.parser")
        for link in soup.find_all("a", href=True):
            href = str(link["href"])
            if not href.startswith("#src-"):
                continue
            anchor = href[1:]
            owner = self.book.anchor_owners.get(anchor)
            if owner is None:
                # Keep the source text without fabricating a valid destination.
                del link["href"]
                link["data-unresolved-reference"] = anchor
                continue
            target = self.plan.split_locations[owner].path if view == "split" else output_path
            relative = PurePosixPath(os.path.relpath(target, PurePosixPath(output_path).parent)).as_posix()
            link["href"] = ("" if target == output_path else quote(relative, safe="/._-")) + "#" + anchor
        for tag in soup.find_all(re.compile(r"^h[1-6]$")):
            relative = int(tag.name[1]) - self.heading_bases.get(fragment.node_id, 1) + 1
            tag.name = f"h{min(heading_level + relative, 6)}"
        return str(soup)
