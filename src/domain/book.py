"""Book domain objects shared by readers and writers."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class BookMetadata:
    title: str
    authors: tuple[str, ...] = ()
    language: str | None = None
    identifier: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "authors": list(self.authors),
            "language": self.language,
            "identifier": self.identifier,
        }


@dataclass(frozen=True)
class SourceLocator:
    path: str | None = None
    fragment: str | None = None
    spine_index: int | None = None
    format: str = "epub"
    page_index: int | None = None

    @property
    def key(self) -> str:
        if self.path:
            suffix = f"#{self.fragment}" if self.fragment else ""
            return f"{self.format}:{self.path}{suffix}"
        return ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "fragment": self.fragment,
            "spine_index": self.spine_index,
            "format": self.format,
            "page_index": self.page_index,
        }


@dataclass
class StructureNode:
    node_id: str
    parent_id: str | None
    children_ids: list[str]
    depth: int
    sibling_order: int
    ordinal_path: tuple[int, ...]
    title_original: str
    title_display: str
    role: str
    source_key: str
    source: SourceLocator
    continuation_sources: list[SourceLocator] = field(default_factory=list)
    content_ids: list[str] = field(default_factory=list)

    @property
    def anchor(self) -> str:
        return f"sec-{self.node_id.replace('-', '')}"

    def source_documents_dict(self) -> dict[str, Any]:
        return {
            "primary": self.source.to_dict(),
            "continuations": [
                source.to_dict() for source in self.continuation_sources
            ],
        }

    def to_dict(self) -> dict[str, Any]:
        source_documents = self.source_documents_dict()
        return {
            "node_id": self.node_id,
            "parent_id": self.parent_id,
            "children_ids": self.children_ids,
            "depth": self.depth,
            "sibling_order": self.sibling_order,
            "ordinal_path": list(self.ordinal_path),
            "title_original": self.title_original,
            "title_display": self.title_display,
            "role": self.role,
            "source_key": self.source_key,
            "source": source_documents["primary"],
            "continuation_sources": source_documents["continuations"],
            "anchor": self.anchor,
            "has_direct_content": bool(self.content_ids),
            "content_ids": self.content_ids,
        }


@dataclass(frozen=True)
class ContentFragment:
    """One owned fragment in source reading order; all views reference it."""

    fragment_id: str
    node_id: str
    source: SourceLocator
    html: str
    heading_before: bool = True

    def to_dict(self) -> dict[str, Any]:
        result = {
            "fragment_id": self.fragment_id,
            "node_id": self.node_id,
            "source": self.source.to_dict(),
            "html": self.html,
        }
        # Older snapshots imply True. A prelude belongs to the following node
        # but must be read before that node's source heading.
        if not self.heading_before:
            result['heading_before'] = False
        return result


@dataclass(frozen=True)
class AssetRef:
    asset_id: str
    source_path: str
    file_name: str
    media_type: str | None
    content: bytes = field(repr=False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "asset_id": self.asset_id,
            "source_path": self.source_path,
            "file_name": self.file_name,
            "media_type": self.media_type,
            "size_bytes": len(self.content),
        }


@dataclass(frozen=True)
class Diagnostic:
    code: str
    message: str
    severity: str = "warning"
    node_id: str | None = None
    source_path: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "severity": self.severity,
            "node_id": self.node_id,
            "source_path": self.source_path,
        }


@dataclass
class BookRevision:
    book_id: str
    edition_id: str
    revision_id: str
    source_id: str
    source_path: Path
    source_sha256: str
    metadata: BookMetadata
    roots: list[str]
    nodes: dict[str, StructureNode]
    reading_order_ids: list[str]
    assets: dict[str, AssetRef]
    diagnostics: list[Diagnostic]
    pipeline_version: str
    content: list[ContentFragment] = field(default_factory=list)
    styles: dict[str, str] = field(default_factory=dict)
    anchor_owners: dict[str, str] = field(default_factory=dict)
    source_format: str = "epub"

    def walk_ids(self, roots: list[str] | None = None) -> list[str]:
        ordered: list[str] = []

        def visit(node_id: str) -> None:
            ordered.append(node_id)
            for child_id in self.nodes[node_id].children_ids:
                visit(child_id)

        for root_id in roots or self.roots:
            visit(root_id)
        return ordered

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 2,
            "book_id": self.book_id,
            "edition_id": self.edition_id,
            "revision_id": self.revision_id,
            "source_id": self.source_id,
            "source_sha256": self.source_sha256,
            "pipeline_version": self.pipeline_version,
            "source_format": self.source_format,
            "content": [fragment.to_dict() for fragment in self.content],
            "styles": self.styles,
            "anchor_owners": self.anchor_owners,
            "metadata": self.metadata.to_dict(),
            "roots": self.roots,
            "reading_order_ids": self.reading_order_ids,
            "nodes": [self.nodes[node_id].to_dict() for node_id in self.walk_ids()],
            "assets": [self.assets[asset_id].to_dict() for asset_id in sorted(self.assets)],
            "diagnostics": [item.to_dict() for item in self.diagnostics],
        }
