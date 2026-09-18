"""Export plan and published artifact models."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ExportUnit:
    unit_id: str
    root_node_id: str
    relative_path: str
    node_ids: tuple[str, ...]
    is_index: bool = False


@dataclass(frozen=True)
class ViewLocation:
    path: str
    anchor: str
    unit_id: str | None = None


@dataclass
class ExportPlan:
    export_id: str
    revision_id: str
    units: list[ExportUnit]
    single_locations: dict[str, ViewLocation]
    split_locations: dict[str, ViewLocation]
    split_policy: str = "chapter-v1"


@dataclass(frozen=True)
class ArtifactRecord:
    role: str
    relative_path: str
    sha256: str
    size_bytes: int
    media_type: str


@dataclass(frozen=True)
class ExportResult:
    export_id: str
    output_dir: Path
    report_path: Path
    artifacts: tuple[ArtifactRecord, ...]
