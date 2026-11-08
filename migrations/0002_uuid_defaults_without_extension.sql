-- Databases created by 2.x default their ids to uuid_generate_v4() from the
-- uuid-ossp extension. Switch them to the built-in gen_random_uuid() so the
-- schema no longer depends on the extension; the extension itself is left
-- installed. SET DEFAULT only affects later inserts, so no row is rewritten.
-- Requires ownership of both tables.

ALTER TABLE work_queue ALTER COLUMN id SET DEFAULT gen_random_uuid();
ALTER TABLE activity_tracker ALTER COLUMN id SET DEFAULT gen_random_uuid();
