-- A short spoken introduction from each host character, so players can hear the hosts before choosing one.
--
-- A sample is recorded the first time anyone asks for it and then kept: playing it again costs nothing.
-- "key" is a fingerprint of everything that shapes it (the character's name, voice, manner, style, the
-- sarcasm setting, the game's name), so changing any of those records a new one.

create table if not exists host_samples (
  key text primary key,
  host_id text not null,
  words text not null,
  mime text not null,
  audio bytea not null,
  created_at timestamptz not null default now()
);

create index if not exists host_samples_host_idx on host_samples (host_id);
