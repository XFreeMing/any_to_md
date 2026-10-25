"""Book domain objects shared by readers and writers."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import re
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

    @classmethod
    def from_dict(cls, data: dict, *, source_path: Path, load_asset) -> BookRevision:
        """Restore the complete schema-2 model; storage supplies verified bytes."""
        if type(data.get('schema_version')) is not int or data['schema_version'] != 2:
            raise ValueError('不支持的正文 schema')
        try:
            nodes = {}
            for value in data['nodes']:
                node = StructureNode(**{key:value[key] for key in (
                    'node_id','parent_id','children_ids','depth','sibling_order','title_original',
                    'title_display','role','source_key','content_ids')},
                    ordinal_path=tuple(value['ordinal_path']), source=SourceLocator(**value['source']),
                    continuation_sources=[SourceLocator(**v) for v in value['continuation_sources']])
                if node.node_id in nodes:
                    raise ValueError('正文节点重复')
                nodes[node.node_id] = node
            assets = {}
            filenames = set()
            for value in data['assets']:
                identifier, name = value['asset_id'], value['file_name']
                if not re.fullmatch(r'[0-9a-f]{64}', identifier) or not re.fullmatch(r'[0-9a-f]{64}(?:\.[a-z0-9]+)?', name) or not name.startswith(identifier):
                    raise ValueError('正文资源路径或身份无效')
                if identifier in assets or name in filenames or type(value['size_bytes']) is not int or value['size_bytes'] < 0:
                    raise ValueError('正文资源重复或大小无效')
                assets[identifier] = AssetRef(identifier,value['source_path'],name,value['media_type'],load_asset(name,value['size_bytes'],identifier))
                filenames.add(name)
            book = cls(**{key:data[key] for key in ('book_id','edition_id','revision_id','source_id','source_sha256','pipeline_version','source_format','roots','reading_order_ids','styles','anchor_owners')},
                source_path=source_path,metadata=BookMetadata(**{**data['metadata'],'authors':tuple(data['metadata']['authors'])}),
                nodes=nodes,assets=assets,diagnostics=[Diagnostic(**value) for value in data['diagnostics']],
                content=[ContentFragment(**{**v,'source':SourceLocator(**v['source'])}) for v in data['content']])
            book.validate()
            if book.to_dict() != data:
                raise ValueError('正文 schema 字段、类型或派生结构不一致')
            return book
        except (KeyError,TypeError,AttributeError,RecursionError) as error:
            raise ValueError('正文 schema 或结构无效') from error

    def validate(self) -> None:
        """Validate references and ownership before traversing or rendering."""
        from bs4 import BeautifulSoup

        def strings(values):
            return isinstance(values,(list,tuple)) and all(isinstance(value,str) for value in values)
        if not isinstance(self.metadata.title,str) or not strings(self.metadata.authors) or any(value is not None and not isinstance(value,str) for value in (self.metadata.language,self.metadata.identifier)):
            raise ValueError('正文元数据 schema 无效')
        if not strings(self.roots) or not strings(self.reading_order_ids) or not isinstance(self.styles,dict) or not isinstance(self.anchor_owners,dict):
            raise ValueError('正文结构或样式 schema 无效')
        if any(not isinstance(value,str) or not value for value in (self.book_id,self.edition_id,self.revision_id,self.source_id,self.pipeline_version)):
            raise ValueError('正文身份类型无效')
        if not re.fullmatch(r'[0-9a-f]{64}',self.source_sha256):
            raise ValueError('正文来源 SHA 无效')
        locators = [v.source for v in self.content]
        for node in self.nodes.values():
            locators.extend([node.source,*node.continuation_sources])
        for locator in locators:
            if locator.format != self.source_format or any(value is not None and not isinstance(value,str) for value in (locator.path,locator.fragment)):
                raise ValueError('正文源定位格式或路径类型无效')
            if any(value is not None and (type(value) is not int or value < 0) for value in (locator.spine_index,locator.page_index)):
                raise ValueError('正文源定位序号无效')
        if not self.nodes or len(self.roots) != len(set(self.roots)):
            raise ValueError('正文根节点为空或重复')
        visited = set()
        def visit(identifier, parent):
            if identifier not in self.nodes or identifier in visited:
                raise ValueError('正文结构引用缺失、重复或成环')
            visited.add(identifier)
            node = self.nodes[identifier]
            if not strings(node.children_ids) or not strings(node.content_ids) or any(not isinstance(value,str) for value in (node.node_id,node.title_original,node.title_display,node.role,node.source_key)):
                raise ValueError('正文节点 schema 无效')
            if node.node_id != identifier or node.parent_id != parent:
                raise ValueError('正文节点父子引用不一致')
            if type(node.depth) is not int or node.depth < 1 or type(node.sibling_order) is not int or node.sibling_order < 0:
                raise ValueError('正文节点深度或顺序无效')
            if not node.ordinal_path or any(type(n) is not int or n < 1 for n in node.ordinal_path):
                raise ValueError('正文节点序号路径无效')
            if parent is not None:
                ancestor = self.nodes[parent]
                if node.depth <= ancestor.depth or node.ordinal_path[:-1] != ancestor.ordinal_path:
                    raise ValueError('正文节点深度或序号父路径不一致')
            if len(node.children_ids) != len(set(node.children_ids)):
                raise ValueError('正文子节点重复')
            for child in node.children_ids:
                visit(child,identifier)
        for identifier in self.roots:
            visit(identifier,None)
        if visited != set(self.nodes) or len(self.reading_order_ids) != len(set(self.reading_order_ids)) or set(self.reading_order_ids) != visited:
            raise ValueError('正文存在游离节点或无效阅读顺序')
        ids = [v.fragment_id for v in self.content]
        assigned = [identifier for n in self.nodes.values() for identifier in n.content_ids]
        if len(ids) != len(set(ids)) or sorted(ids) != sorted(assigned):
            raise ValueError('规范正文片段缺少或重复归属')
        anchors = {}
        for fragment in self.content:
            if fragment.node_id not in self.nodes or fragment.fragment_id not in self.nodes[fragment.node_id].content_ids:
                raise ValueError('规范正文归属与章节不一致')
            if type(fragment.heading_before) is not bool or not isinstance(fragment.html,str):
                raise ValueError('正文片段类型无效')
            for anchor in BeautifulSoup(fragment.html,'html.parser').find_all(id=True):
                identifier = str(anchor['id'])
                if identifier in anchors:
                    raise ValueError('规范锚点重复')
                anchors[identifier] = fragment.node_id
        if anchors != self.anchor_owners:
            raise ValueError('规范锚点归属冲突')
        for node in self.nodes.values():
            if node.content_ids != [v.fragment_id for v in self.content if v.node_id == node.node_id]:
                raise ValueError('正文归属片段顺序不一致')
        if any(d.node_id is not None and d.node_id not in self.nodes for d in self.diagnostics):
            raise ValueError('正文诊断引用未知节点')
        strings = [v.html for v in self.content]+list(self.styles.values())
        for text in strings:
            if not isinstance(text,str):
                raise ValueError('正文或样式类型无效')
            for identifier in re.findall(r'asset://([^\s\"\'<>\)]+)',text):
                if identifier not in self.assets:
                    raise ValueError('正文或样式引用未知资源')

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
