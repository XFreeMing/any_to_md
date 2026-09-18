"""SQLite catalog for M01 book identity, jobs and exports."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterator

from ...domain.artifacts import ArtifactRecord, ExportPlan
from ...domain.book import BookRevision


def _now() -> str:
    return datetime.now(UTC).isoformat()


class SQLiteCatalog:
    def __init__(self, database_path: Path):
        database_path.parent.mkdir(parents=True, exist_ok=True)
        self.database_path = database_path
        self._migrate()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 30000")
        return connection

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _migrate(self) -> None:
        connection = self._connect()
        try:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version INTEGER PRIMARY KEY,
                    applied_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS books (
                    book_id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    authors_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS editions (
                    edition_id TEXT PRIMARY KEY,
                    book_id TEXT NOT NULL REFERENCES books(book_id),
                    source_sha256 TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(book_id, source_sha256)
                );
                CREATE TABLE IF NOT EXISTS sources (
                    source_id TEXT PRIMARY KEY,
                    edition_id TEXT NOT NULL REFERENCES editions(edition_id),
                    format TEXT NOT NULL,
                    sha256 TEXT NOT NULL,
                    storage_key TEXT NOT NULL,
                    original_filename TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(format, sha256)
                );
                CREATE TABLE IF NOT EXISTS revisions (
                    revision_id TEXT PRIMARY KEY,
                    edition_id TEXT NOT NULL REFERENCES editions(edition_id),
                    content_storage_key TEXT,
                    content_sha256 TEXT,
                    semantic_sha256 TEXT NOT NULL,
                    pipeline_version TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS section_identities (
                    node_id TEXT PRIMARY KEY,
                    edition_id TEXT NOT NULL REFERENCES editions(edition_id),
                    source_key TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(edition_id, source_key)
                );
                CREATE TABLE IF NOT EXISTS section_versions (
                    revision_id TEXT NOT NULL REFERENCES revisions(revision_id),
                    node_id TEXT NOT NULL REFERENCES section_identities(node_id),
                    parent_node_id TEXT,
                    sibling_order INTEGER NOT NULL,
                    depth INTEGER NOT NULL,
                    role TEXT NOT NULL,
                    title_original TEXT NOT NULL,
                    title_display TEXT NOT NULL,
                    ordinal_path TEXT NOT NULL,
                    source_locator_json TEXT NOT NULL,
                    PRIMARY KEY(revision_id, node_id)
                );
                CREATE INDEX IF NOT EXISTS idx_section_children
                    ON section_versions(revision_id, parent_node_id, sibling_order);
                CREATE TABLE IF NOT EXISTS jobs (
                    job_id TEXT PRIMARY KEY,
                    source_id TEXT NOT NULL REFERENCES sources(source_id),
                    status TEXT NOT NULL,
                    error_json TEXT,
                    created_at TEXT NOT NULL,
                    finished_at TEXT
                );
                CREATE TABLE IF NOT EXISTS exports (
                    export_id TEXT PRIMARY KEY,
                    revision_id TEXT NOT NULL REFERENCES revisions(revision_id),
                    job_id TEXT NOT NULL REFERENCES jobs(job_id),
                    config_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    storage_key TEXT,
                    created_at TEXT NOT NULL,
                    published_at TEXT
                );
                CREATE TABLE IF NOT EXISTS artifacts (
                    artifact_id TEXT PRIMARY KEY,
                    export_id TEXT NOT NULL REFERENCES exports(export_id),
                    role TEXT NOT NULL,
                    relative_path TEXT NOT NULL,
                    sha256 TEXT NOT NULL,
                    size_bytes INTEGER NOT NULL,
                    media_type TEXT NOT NULL,
                    UNIQUE(export_id, relative_path)
                );
                CREATE TABLE IF NOT EXISTS export_locations (
                    export_id TEXT NOT NULL REFERENCES exports(export_id),
                    node_id TEXT NOT NULL REFERENCES section_identities(node_id),
                    view TEXT NOT NULL,
                    unit_id TEXT NOT NULL DEFAULT '',
                    relative_path TEXT NOT NULL,
                    anchor TEXT NOT NULL,
                    PRIMARY KEY(export_id, node_id, view)
                );
                CREATE INDEX IF NOT EXISTS idx_export_location_lookup
                    ON export_locations(export_id, node_id, view);
                """
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO schema_migrations(version, applied_at)
                VALUES(1, ?)
                """,
                (_now(),),
            )
            revision_columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(revisions)").fetchall()
            }
            if "semantic_sha256" not in revision_columns:
                connection.execute(
                    "ALTER TABLE revisions ADD COLUMN semantic_sha256 TEXT NOT NULL DEFAULT ''"
                )
            connection.execute(
                """
                INSERT OR IGNORE INTO schema_migrations(version, applied_at)
                VALUES(2, ?)
                """,
                (_now(),),
            )
            connection.commit()
        finally:
            connection.close()

    def begin_export(
        self,
        *,
        book: BookRevision,
        source_storage_key: str,
        original_filename: str,
        semantic_sha256: str,
        job_id: str,
        plan: ExportPlan,
    ) -> None:
        created_at = _now()
        with self._transaction() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO books(book_id, title, authors_json, created_at)
                VALUES(?, ?, ?, ?)
                """,
                (
                    book.book_id,
                    book.metadata.title,
                    json.dumps(book.metadata.authors, ensure_ascii=False),
                    created_at,
                ),
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO editions(
                    edition_id, book_id, source_sha256, created_at
                ) VALUES(?, ?, ?, ?)
                """,
                (book.edition_id, book.book_id, book.source_sha256, created_at),
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO sources(
                    source_id, edition_id, format, sha256, storage_key,
                    original_filename, created_at
                ) VALUES(?, ?, 'epub', ?, ?, ?, ?)
                """,
                (
                    book.source_id,
                    book.edition_id,
                    book.source_sha256,
                    source_storage_key,
                    original_filename,
                    created_at,
                ),
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO revisions(
                    revision_id, edition_id, content_storage_key,
                    content_sha256, semantic_sha256, pipeline_version,
                    status, created_at
                ) VALUES(?, ?, NULL, ?, ?, ?, 'ready', ?)
                """,
                (
                    book.revision_id,
                    book.edition_id,
                    semantic_sha256,
                    semantic_sha256,
                    book.pipeline_version,
                    created_at,
                ),
            )
            existing_revision = connection.execute(
                "SELECT edition_id, semantic_sha256, pipeline_version FROM revisions WHERE revision_id = ?",
                (book.revision_id,),
            ).fetchone()
            if (
                existing_revision is None
                or existing_revision["edition_id"] != book.edition_id
                or existing_revision["semantic_sha256"] != semantic_sha256
                or existing_revision["pipeline_version"] != book.pipeline_version
            ):
                raise ValueError(f"revision 身份冲突：{book.revision_id}")
            for node_id in book.walk_ids():
                node = book.nodes[node_id]
                connection.execute(
                    """
                    INSERT OR IGNORE INTO section_identities(
                        node_id, edition_id, source_key, created_at
                    ) VALUES(?, ?, ?, ?)
                    """,
                    (node.node_id, book.edition_id, node.source_key, created_at),
                )
                values = (
                    book.revision_id,
                    node.node_id,
                    node.parent_id,
                    node.sibling_order,
                    node.depth,
                    node.role,
                    node.title_original,
                    node.title_display,
                    ".".join(str(value) for value in node.ordinal_path),
                    json.dumps(
                        node.source_documents_dict(),
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                )
                connection.execute(
                    """
                    INSERT OR IGNORE INTO section_versions(
                        revision_id, node_id, parent_node_id, sibling_order, depth,
                        role, title_original, title_display, ordinal_path,
                        source_locator_json
                    ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    values,
                )
                existing_section = connection.execute(
                    """
                    SELECT revision_id, node_id, parent_node_id, sibling_order, depth,
                           role, title_original, title_display, ordinal_path,
                           source_locator_json
                    FROM section_versions WHERE revision_id = ? AND node_id = ?
                    """,
                    (book.revision_id, node.node_id),
                ).fetchone()
                if existing_section is None or tuple(existing_section) != values:
                    raise ValueError(f"已发布 revision 的章节内容不一致：{node.node_id}")
            connection.execute(
                """
                INSERT INTO jobs(
                    job_id, source_id, status, error_json, created_at, finished_at
                ) VALUES(?, ?, 'processing', NULL, ?, NULL)
                """,
                (job_id, book.source_id, created_at),
            )
            connection.execute(
                """
                INSERT INTO exports(
                    export_id, revision_id, job_id, config_json, status,
                    storage_key, created_at, published_at
                ) VALUES(?, ?, ?, ?, 'staging', NULL, ?, NULL)
                """,
                (
                    plan.export_id,
                    book.revision_id,
                    job_id,
                    json.dumps({"view": "both", "split_policy": plan.split_policy}),
                    created_at,
                ),
            )

    def publish_export(
        self,
        *,
        book: BookRevision,
        job_id: str,
        plan: ExportPlan,
        storage_key: str,
        revision_storage_key: str,
        revision_content_sha256: str,
        final_status: str,
        artifacts: list[ArtifactRecord],
    ) -> None:
        published_at = _now()
        with self._transaction() as connection:
            existing_revision = connection.execute(
                "SELECT content_storage_key, content_sha256 FROM revisions WHERE revision_id = ?",
                (book.revision_id,),
            ).fetchone()
            if existing_revision is None:
                raise ValueError(f"revision 不存在：{book.revision_id}")
            existing_hash = existing_revision["content_sha256"]
            existing_storage_key = existing_revision["content_storage_key"]
            if (
                existing_storage_key
                and existing_hash
                and existing_hash != revision_content_sha256
            ):
                raise ValueError(f"revision 快照哈希冲突：{book.revision_id}")
            connection.execute(
                """
                UPDATE revisions
                SET content_storage_key = COALESCE(content_storage_key, ?),
                    content_sha256 = CASE
                        WHEN content_storage_key IS NULL THEN ?
                        ELSE content_sha256
                    END
                WHERE revision_id = ?
                """,
                (revision_storage_key, revision_content_sha256, book.revision_id),
            )
            for artifact in artifacts:
                artifact_id = f"{plan.export_id}:{artifact.relative_path}"
                connection.execute(
                    """
                    INSERT INTO artifacts(
                        artifact_id, export_id, role, relative_path, sha256,
                        size_bytes, media_type
                    ) VALUES(?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        artifact_id,
                        plan.export_id,
                        artifact.role,
                        artifact.relative_path,
                        artifact.sha256,
                        artifact.size_bytes,
                        artifact.media_type,
                    ),
                )
            for node_id in book.walk_ids():
                for view, location in (
                    ("single", plan.single_locations[node_id]),
                    ("split", plan.split_locations[node_id]),
                ):
                    connection.execute(
                        """
                        INSERT INTO export_locations(
                            export_id, node_id, view, unit_id, relative_path, anchor
                        ) VALUES(?, ?, ?, ?, ?, ?)
                        """,
                        (
                            plan.export_id,
                            node_id,
                            view,
                            location.unit_id or "",
                            location.path,
                            location.anchor,
                        ),
                    )
            connection.execute(
                "UPDATE exports SET status = ?, storage_key = ?, published_at = ? WHERE export_id = ?",
                (final_status, storage_key, published_at, plan.export_id),
            )
            connection.execute(
                "UPDATE jobs SET status = ?, finished_at = ? WHERE job_id = ?",
                (final_status, published_at, job_id),
            )

    def fail_export(self, *, job_id: str, export_id: str, error: str) -> None:
        finished_at = _now()
        payload = json.dumps({"message": error}, ensure_ascii=False)
        with self._transaction() as connection:
            connection.execute(
                "UPDATE jobs SET status = 'failed', error_json = ?, finished_at = ? WHERE job_id = ?",
                (payload, finished_at, job_id),
            )
            connection.execute(
                "UPDATE exports SET status = 'failed' WHERE export_id = ?",
                (export_id,),
            )
