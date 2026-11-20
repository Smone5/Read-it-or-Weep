-- A sign of life for documents that are being processed, so "still working" and "stuck"
-- can be told apart without guessing from the document's age.
alter table documents add column if not exists heartbeat_at timestamptz;
