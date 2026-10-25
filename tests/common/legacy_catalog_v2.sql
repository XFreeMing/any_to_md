-- Independent S8 fixture: exact DDL from fc5b9a6193d25fb2f3f1efc7321bd7dfc7a124b4
-- src/any_to_md/adapters/persistence/sqlite.py
-- Historical source SHA-256: 6472789a544aea6ae583ee8a3ede7b059352e91b62665edf9809462f77d28cbc
-- Historical PRAGMA user_version defaulted to 0; schema_migrations contained 1 and 2.
-- Synthetic records are authored in UC-03-S8; this is not real user legacy data.
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
