-- Baseline schema: the tables 2.x created at startup.
--
-- IF NOT EXISTS lets a database created by 2.x adopt the migration ledger
-- unchanged: an existing table is kept as it is, columns and defaults included.
-- A fresh database gets gen_random_uuid() defaults (core PostgreSQL since 13),
-- so no extension is needed. The application role must own these tables (see
-- sql/grant_existing_database.sql) because the ALTER below needs ownership.

CREATE TABLE IF NOT EXISTS work_queue (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    full_path TEXT NOT NULL,
    filename TEXT NOT NULL,
    parent TEXT NOT NULL,
    target_path TEXT NULL,
    status TEXT NOT NULL,
    is_archive BOOLEAN NOT NULL DEFAULT FALSE,
    is_main_archive_file BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    modified_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    media_info_cache_id UUID NULL,
    attempts INTEGER NOT NULL DEFAULT 0
);

-- 2.x added this column after the table existed; databases older than that lack it.
ALTER TABLE work_queue ADD COLUMN IF NOT EXISTS attempts INTEGER NOT NULL DEFAULT 0;

CREATE TABLE IF NOT EXISTS batch_control (
    batch_id UUID NOT NULL,
    work_queue_id UUID NOT NULL,
    in_progress BOOLEAN NOT NULL DEFAULT FALSE,
    verified BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    modified_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS activity_tracker (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    activity TEXT NOT NULL
);
