-- A player's favourite artist, and what the music catalogue says about each artist asked for.
--
-- Now and then a song from the player's favourite artist, or from artists like them, plays behind their game
-- (services/favorites.py). "favorite_music" keeps each lookup - the artist as the catalogue names them, similar
-- artists, and their songs with 30-second previews - for a week, so a game never waits on the catalogue.

alter table players add column if not exists favorite_artist text;

create table if not exists favorite_music (
  artist_key text primary key,        -- the name as asked for, lower-cased with only letters and digits
  artist text not null,               -- the name as the catalogue has it ('' when nobody was found)
  similar_artists jsonb not null default '[]',   -- ("similar" alone is a reserved word)
  songs jsonb not null default '[]',
  fetched_at timestamptz not null default now()
);
