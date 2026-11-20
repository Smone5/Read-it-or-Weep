-- Host lines recorded once and replayed after, to spend less on the paid voice (2026-11-20).

-- The audio of a line whose exact words can come round again (the final question read out, a round introduction from
-- its rotating set), as the game streams it: 24 kHz 16-bit mono PCM. Keyed by the voice, the model, the pause setting
-- and the words themselves, so any change records afresh.
create table if not exists host_line_audio (
    key text primary key,
    voice text not null,
    line text not null,
    audio bytea not null,
    seconds real not null,
    created_at timestamptz not null default now(),
    used_count integer not null default 0,
    last_used_at timestamptz
);

-- Each host's set of round introductions: written (and recorded) the first few times a round comes up, then rotated.
-- "bank" is a hash of the host's whole brief, the voice and the round, so a change to the host starts a new set.
create table if not exists host_line_bank (
    id bigserial primary key,
    bank text not null,
    line text not null,
    used_count integer not null default 0,
    created_at timestamptz not null default now()
);
create index if not exists host_line_bank_bank on host_line_bank (bank);
