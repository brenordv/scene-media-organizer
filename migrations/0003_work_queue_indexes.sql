-- Indexes for the queries the service runs on every polling cycle and batch:
-- claiming PENDING items and sweeping WORKING ones (status), the catch-up and
-- `missing` lookups by file name (filename), batch membership lookups
-- (batch_id, work_queue_id), and the search for open batches, which otherwise
-- scans every batch ever run. The partial index predicate matches the queries'
-- `in_progress = TRUE` exactly, which PostgreSQL needs to use it.

CREATE INDEX IF NOT EXISTS ix_work_queue_status ON work_queue (status);
CREATE INDEX IF NOT EXISTS ix_work_queue_filename ON work_queue (filename);
CREATE INDEX IF NOT EXISTS ix_batch_control_batch_id_work_queue_id ON batch_control (batch_id, work_queue_id);
CREATE INDEX IF NOT EXISTS ix_batch_control_open ON batch_control (batch_id) WHERE in_progress = TRUE;
