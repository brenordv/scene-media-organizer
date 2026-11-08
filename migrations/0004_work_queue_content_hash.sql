-- The SHA-256 of a work item's content, compared with finished items to spot a
-- file that was already processed, whatever its name or folder. The index is not
-- unique: a backfill can find duplicates among items finished before hashes were
-- recorded. The partial index predicate must match the lookup's `status = 'DONE'`
-- exactly, which PostgreSQL needs to use it, so the query spells the status as a
-- literal and never as a parameter.

ALTER TABLE work_queue ADD COLUMN IF NOT EXISTS content_hash TEXT NULL;
CREATE INDEX IF NOT EXISTS ix_work_queue_done_content_hash ON work_queue (content_hash) WHERE status = 'DONE';
