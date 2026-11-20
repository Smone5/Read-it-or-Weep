-- Office Jukebox, the music side game: each song's title rewritten as corporate jargon, written once and kept.
create table if not exists music_jargon (
    track_id uuid primary key references music_tracks (id) on delete cascade,
    jargon text not null,
    created_at timestamptz not null default now()
);

