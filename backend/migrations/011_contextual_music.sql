-- Contextual Apple Music / iTunes soundtrack moods and topic themes.
alter table music_tracks add column if not exists mood text;
alter table music_tracks add column if not exists themes text[] not null default '{}';
