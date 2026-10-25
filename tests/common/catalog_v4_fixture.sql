-- Independently frozen schema-4 jobs/exports and revision lineage, as at 2528a87.
-- Applied ONLY to the hand-authored S8 historical fixture to construct v4 input.
-- This never runs the production migration or downgrades a current catalog.
PRAGMA foreign_keys=OFF;
BEGIN;
CREATE TABLE jobs_v4 (
 job_id TEXT PRIMARY KEY, source_id TEXT REFERENCES sources(source_id),
 status TEXT NOT NULL, error_json TEXT, created_at TEXT NOT NULL, finished_at TEXT,
 filename TEXT NOT NULL DEFAULT '', input_format TEXT NOT NULL DEFAULT 'unknown',
 targets_json TEXT NOT NULL DEFAULT '[]', output_format TEXT,
 progress INTEGER NOT NULL DEFAULT 0, message TEXT NOT NULL DEFAULT '',
 report_key TEXT, download_relative TEXT, owner_token TEXT, owner_pid INTEGER, lease_expires REAL
);
INSERT INTO jobs_v4(job_id,source_id,status,error_json,created_at,finished_at,filename,input_format,targets_json,output_format,progress,download_relative)
 SELECT job_id,source_id,status,error_json,created_at,finished_at,'reference.epub','epub','["markdown"]','md_single',100,'book.zip' FROM jobs;
DROP TABLE jobs;
ALTER TABLE jobs_v4 RENAME TO jobs;
CREATE TABLE exports_v4 (
 export_id TEXT PRIMARY KEY, revision_id TEXT REFERENCES revisions(revision_id),
 job_id TEXT NOT NULL REFERENCES jobs(job_id), config_json TEXT NOT NULL,
 status TEXT NOT NULL, storage_key TEXT, created_at TEXT NOT NULL, published_at TEXT
);
INSERT INTO exports_v4 SELECT * FROM exports;
DROP TABLE exports;
ALTER TABLE exports_v4 RENAME TO exports;
CREATE TABLE revision_migrations (
 old_revision_id TEXT NOT NULL REFERENCES revisions(revision_id),
 new_revision_id TEXT NOT NULL REFERENCES revisions(revision_id),
 from_schema INTEGER NOT NULL, to_schema INTEGER NOT NULL,
 reason TEXT NOT NULL, created_at TEXT NOT NULL,
 PRIMARY KEY(old_revision_id,new_revision_id)
);
INSERT INTO schema_migrations VALUES(3,'2026-01-02T03:04:05+00:00');
INSERT INTO schema_migrations VALUES(4,'2026-01-02T03:04:05+00:00');
PRAGMA user_version=4;
COMMIT;
