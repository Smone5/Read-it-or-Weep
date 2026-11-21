-- Optional legal YouTube video clip metadata attached to a question (shown on reveal / review).
-- Never downloads or re-hosts video: stores only a verified YouTube link and optional label.

alter table questions add column if not exists video_url text;
alter table questions add column if not exists video_title text;
