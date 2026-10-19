"""Create one shared plan for whole-book and split Markdown views."""

from __future__ import annotations

import uuid

from ..domain.artifacts import ExportPlan, ExportUnit, ViewLocation
from ..domain.book import BookRevision, StructureNode
from .naming import chapter_file_name, unique_short_ids


def _subtree_ids(book: BookRevision, root_id: str) -> list[str]:
    ordered: list[str] = []

    def visit(node_id: str) -> None:
        ordered.append(node_id)
        for child_id in book.nodes[node_id].children_ids:
            visit(child_id)

    visit(root_id)
    return ordered


def _is_group(node: StructureNode) -> bool:
    return bool(node.children_ids) and (
        not node.source.path or node.role in {"volume", "part"}
    )


def create_export_plan(book: BookRevision, export_id: str | None = None) -> ExportPlan:
    export_id = export_id or str(uuid.uuid4())
    ordered_ids = book.walk_ids()
    short_ids = unique_short_ids(ordered_ids)
    units: list[ExportUnit] = []

    def add_unit(root_id: str, *, is_index: bool, node_ids: list[str]) -> None:
        node = book.nodes[root_id]
        filename = chapter_file_name(
            ordinal_path=node.ordinal_path,
            title=node.title_display,
            short_id=short_ids[root_id],
            is_index=is_index,
        )
        units.append(
            ExportUnit(
                unit_id=f"unit-{root_id}",
                root_node_id=root_id,
                relative_path=f"chapters/{filename}",
                node_ids=tuple(node_ids),
                is_index=is_index,
            )
        )

    def add_structure(node_id: str) -> None:
        node = book.nodes[node_id]
        if _is_group(node):
            add_unit(node_id, is_index=True, node_ids=[node_id])
            for child_id in node.children_ids:
                add_structure(child_id)
        else:
            add_unit(node_id, is_index=False, node_ids=_subtree_ids(book, node_id))

    for root_id in book.roots:
        add_structure(root_id)

    reading_position = {
        node_id: index for index, node_id in enumerate(book.reading_order_ids)
    }
    units = [
        ExportUnit(
            unit_id=unit.unit_id,
            root_node_id=unit.root_node_id,
            relative_path=unit.relative_path,
            node_ids=tuple(
                sorted(
                    unit.node_ids,
                    key=lambda node_id: reading_position.get(
                        node_id, len(reading_position)
                    ),
                )
            ),
            is_index=unit.is_index,
        )
        for unit in units
    ]
    units.sort(
        key=lambda unit: min(
            reading_position.get(node_id, len(reading_position))
            for node_id in unit.node_ids
        )
    )

    node_to_unit: dict[str, ExportUnit] = {}
    for unit in units:
        for node_id in unit.node_ids:
            if node_id in node_to_unit:
                raise ValueError(f"章节重复分配到多个文件：{node_id}")
            node_to_unit[node_id] = unit

    missing = set(book.nodes) - set(node_to_unit)
    if missing:
        raise ValueError(f"以下章节没有分章输出位置：{', '.join(sorted(missing))}")

    single_locations = {
        node_id: ViewLocation(path="book.md", anchor=book.nodes[node_id].anchor)
        for node_id in ordered_ids
    }
    split_locations = {
        node_id: ViewLocation(
            path=node_to_unit[node_id].relative_path,
            anchor=book.nodes[node_id].anchor,
            unit_id=node_to_unit[node_id].unit_id,
        )
        for node_id in ordered_ids
    }
    return ExportPlan(
        export_id=export_id,
        revision_id=book.revision_id,
        units=units,
        single_locations=single_locations,
        split_locations=split_locations,
    )
