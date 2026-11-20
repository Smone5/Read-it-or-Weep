-- =============================================================================
-- Report quiz game – initial schema
--
-- Plain PostgreSQL 13+. No extensions, no vendor features, so it runs on any
-- managed Postgres (Azure, AWS, Google Cloud) or a container.
--
-- Only the backend connects to this database; browsers never do.
-- =============================================================================

-- Company-level settings (branding, host persona, game defaults)
create table settings (
  key         text primary key,
  value       jsonb not null default '{}'::jsonb,
  updated_at  timestamptz not null default now()
);

-- Admins sign in with email + password (Argon2 hash)
create table admin_users (
  id             uuid primary key default gen_random_uuid(),
  email          text not null,
  name           text,
  password_hash  text not null,
  created_at     timestamptz not null default now(),
  last_login_at  timestamptz
);
create unique index admin_users_email_key on admin_users (lower(email));

-- Players join with name + work email; no password
create table players (
  id                 uuid primary key default gen_random_uuid(),
  email              text not null,
  name               text not null,
  team               text,
  games_played_count int  not null default 0,
  created_at         timestamptz not null default now(),
  last_seen_at       timestamptz
);
create unique index players_email_key on players (lower(email));

-- One quiz per report / meeting
create table quizzes (
  id                    uuid primary key default gen_random_uuid(),
  slug                  text not null unique,
  title                 text not null,
  description           text,
  meeting_at            timestamptz,
  status                text not null default 'draft' check (status in ('draft', 'open', 'closed')),
  access_code           text,
  allowed_email_domains text[] not null default '{}',
  teams                 text[] not null default '{}',
  settings              jsonb  not null default '{}'::jsonb,
  created_by            uuid references admin_users (id) on delete set null,
  created_at            timestamptz not null default now(),
  updated_at            timestamptz not null default now()
);

-- Source documents and what is extracted from them
create table documents (
  id             uuid primary key default gen_random_uuid(),
  quiz_id        uuid not null references quizzes (id) on delete cascade,
  filename       text not null,
  storage_key    text not null,
  mime_type      text,
  size_bytes     bigint,
  role           text not null default 'primary' check (role in ('primary', 'supporting')),
  status         text not null default 'uploaded' check (status in ('uploaded', 'processing', 'ready', 'failed')),
  stage          text,
  progress       jsonb not null default '{}'::jsonb,
  error          text,
  warnings       jsonb not null default '[]'::jsonb,
  title          text,
  summary        text,
  word_count     int,
  section_count  int,
  image_count    int,
  chunk_count    int,
  question_count int,
  created_at     timestamptz not null default now(),
  processed_at   timestamptz
);
create index documents_quiz_idx on documents (quiz_id);

-- A "section" is a topic-sized slice of the report. Readiness is tracked per section.
create table doc_sections (
  id           uuid primary key default gen_random_uuid(),
  document_id  uuid not null references documents (id) on delete cascade,
  quiz_id      uuid not null references quizzes (id) on delete cascade,
  ordinal      int  not null,
  level        int  not null default 1,
  heading      text not null,
  word_count   int  not null default 0
);
create index doc_sections_quiz_idx on doc_sections (quiz_id, ordinal);

-- Text chunks with their embedding (a plain float array; similarity is computed in the app,
-- which is plenty for one report's worth of chunks and needs no database extension).
create table doc_chunks (
  id           uuid primary key default gen_random_uuid(),
  document_id  uuid not null references documents (id) on delete cascade,
  quiz_id      uuid not null references quizzes (id) on delete cascade,
  section_id   uuid references doc_sections (id) on delete set null,
  ordinal      int  not null,
  kind         text not null default 'text' check (kind in ('text', 'table', 'chart')),
  heading_path text,
  chunk_text   text not null,
  token_count  int,
  embedding    real[]
);
create index doc_chunks_quiz_idx on doc_chunks (quiz_id, ordinal);

create table doc_images (
  id            uuid primary key default gen_random_uuid(),
  document_id   uuid not null references documents (id) on delete cascade,
  quiz_id       uuid not null references quizzes (id) on delete cascade,
  section_id    uuid references doc_sections (id) on delete set null,
  ordinal       int  not null,
  storage_key   text not null,
  mime_type     text not null default 'image/png',
  width         int,
  height        int,
  sha256        text,
  origin        text not null default 'embedded' check (origin in ('embedded', 'chart', 'page')),
  caption       text,
  context_text  text,
  description   text,
  kind          text,
  usable        boolean not null default false
);
create index doc_images_quiz_idx on doc_images (quiz_id, ordinal);

-- Question bank
--   mcq       : question_text + options (4 strings) + correct_index
--   fact_fake : question_text is a statement, is_true says whether it is a fact
create table questions (
  id             uuid primary key default gen_random_uuid(),
  quiz_id        uuid not null references quizzes (id) on delete cascade,
  document_id    uuid references documents (id) on delete cascade,
  section_id     uuid references doc_sections (id) on delete set null,
  chunk_id       uuid references doc_chunks (id) on delete set null,
  image_id       uuid references doc_images (id) on delete set null,
  kind           text not null default 'mcq' check (kind in ('mcq', 'fact_fake')),
  source_type    text not null default 'text' check (source_type in ('text', 'table', 'chart', 'image')),
  skill          text not null default 'recall' check (skill in ('recall', 'understanding')),
  title          text,
  question_text  text not null,
  options        jsonb,
  correct_index  int,
  is_true        boolean,
  explanation    text,
  source_quote   text,
  difficulty     text not null default 'medium' check (difficulty in ('easy', 'medium', 'hard')),
  status         text not null default 'approved' check (status in ('pending', 'approved', 'rejected')),
  origin         text not null default 'generated' check (origin in ('generated', 'manual')),
  pinned         boolean not null default false,
  edited_at      timestamptz,
  edited_by      uuid references admin_users (id) on delete set null,
  quality        jsonb not null default '{}'::jsonb,
  flag_count     int  not null default 0,
  times_served   int  not null default 0,
  times_correct  int  not null default 0,
  embedding      real[],
  created_at     timestamptz not null default now(),
  updated_at     timestamptz not null default now(),
  constraint questions_shape check (
    (kind = 'mcq' and options is not null and correct_index is not null)
    or (kind = 'fact_fake' and is_true is not null)
  )
);
create index questions_quiz_status_idx on questions (quiz_id, status, kind);
create index questions_section_idx on questions (section_id);

create table question_flags (
  id           uuid primary key default gen_random_uuid(),
  question_id  uuid not null references questions (id) on delete cascade,
  player_id    uuid references players (id) on delete set null,
  reason       text,
  created_at   timestamptz not null default now()
);
create index question_flags_question_idx on question_flags (question_id);

-- Music: a cached catalogue of well-known tracks per decade
create table music_tracks (
  id            uuid primary key default gen_random_uuid(),
  decade        text not null,
  artist        text not null,
  title         text not null,
  preview_url   text not null,
  artwork_url   text,
  track_url     text,
  release_year  int,
  source        text not null default 'itunes',
  active        boolean not null default true,
  created_at    timestamptz not null default now(),
  unique (decade, artist, title)
);
create index music_tracks_decade_idx on music_tracks (decade) where active;

-- Solo play: a run is one game; run_items are the questions served in it.
-- The correct answer stays here on the server until the player has answered.
create table runs (
  id                  uuid primary key default gen_random_uuid(),
  player_id           uuid not null references players (id) on delete cascade,
  quiz_id             uuid not null references quizzes (id) on delete cascade,
  mode                text not null default 'show' check (mode in ('show', 'sudden_death', 'speed', 'straight')),
  decade              text,
  status              text not null default 'in_progress' check (status in ('in_progress', 'completed', 'abandoned')),
  deadline_at         timestamptz,
  passed              boolean,
  plan                jsonb not null default '[]'::jsonb,
  position            int  not null default 0,
  score               int  not null default 0,
  streak              int  not null default 0,
  streak_max          int  not null default 0,
  strikes             int  not null default 0,
  questions_answered  int  not null default 0,
  correct_answers     int  not null default 0,
  report_answered     int  not null default 0,
  report_correct      int  not null default 0,
  music_answered      int  not null default 0,
  music_correct       int  not null default 0,
  lifelines           jsonb not null default '{}'::jsonb,
  badge               text,
  started_at          timestamptz not null default now(),
  finished_at         timestamptz,
  duration_sec        int
);
create index runs_quiz_score_idx on runs (quiz_id, mode, score desc) where status = 'completed';
create index runs_player_idx on runs (player_id, quiz_id);

create table run_items (
  id                 uuid primary key default gen_random_uuid(),
  run_id             uuid not null references runs (id) on delete cascade,
  player_id          uuid not null references players (id) on delete cascade,
  quiz_id            uuid not null references quizzes (id) on delete cascade,
  position           int  not null,
  slot               text not null check (slot in ('report', 'image', 'music', 'fact_fake', 'second_chance', 'final')),
  round_no           int  not null default 1,
  round_name         text,
  question_id        uuid references questions (id) on delete set null,
  section_id         uuid references doc_sections (id) on delete set null,
  music_track_id     uuid references music_tracks (id) on delete set null,
  payload            jsonb not null default '{}'::jsonb,
  correct_index      int,
  time_limit_sec     int  not null default 30,
  points_multiplier  numeric not null default 1,
  wager              int,
  served_at          timestamptz not null default now(),
  answered_at        timestamptz,
  selected_index     int,
  is_correct         boolean,
  timed_out          boolean not null default false,
  time_taken_ms      int,
  points             int  not null default 0,
  lifelines_used     text[] not null default '{}',
  unique (run_id, position)
);
create index run_items_player_quiz_idx on run_items (player_id, quiz_id);
create index run_items_question_idx on run_items (question_id);
create index run_items_quiz_idx on run_items (quiz_id);

create table feedback (
  id          uuid primary key default gen_random_uuid(),
  player_id   uuid references players (id) on delete cascade,
  quiz_id     uuid references quizzes (id) on delete cascade,
  run_id      uuid references runs (id) on delete set null,
  stars       int check (stars between 1 and 5),
  comment     text,
  created_at  timestamptz not null default now()
);

-- Live group play (big screen + phones)
create table live_sessions (
  id               uuid primary key default gen_random_uuid(),
  quiz_id          uuid not null references quizzes (id) on delete cascade,
  code             text not null unique,
  host_key_hash    text not null,
  status           text not null default 'lobby'
                   check (status in ('lobby', 'question', 'reveal', 'scoreboard', 'finished')),
  settings         jsonb not null default '{}'::jsonb,
  items            jsonb not null default '[]'::jsonb,
  current_index    int  not null default -1,
  phase_started_at timestamptz,
  phase_ends_at    timestamptz,
  version          int  not null default 0,
  created_at       timestamptz not null default now(),
  started_at       timestamptz,
  finished_at      timestamptz
);
create index live_sessions_quiz_idx on live_sessions (quiz_id, created_at desc);

create table live_participants (
  id             uuid primary key default gen_random_uuid(),
  session_id     uuid not null references live_sessions (id) on delete cascade,
  player_id      uuid not null references players (id) on delete cascade,
  display_name   text not null,
  team           text,
  score          int  not null default 0,
  streak         int  not null default 0,
  correct_count  int  not null default 0,
  head_start     int  not null default 0,
  joined_at      timestamptz not null default now(),
  unique (session_id, player_id)
);
create index live_participants_session_idx on live_participants (session_id, score desc);

create table live_answers (
  id              uuid primary key default gen_random_uuid(),
  session_id      uuid not null references live_sessions (id) on delete cascade,
  participant_id  uuid not null references live_participants (id) on delete cascade,
  question_index  int  not null,
  selected_index  int,
  is_correct      boolean not null default false,
  time_ms         int,
  points          int  not null default 0,
  answered_at     timestamptz not null default now(),
  unique (session_id, participant_id, question_index)
);
create index live_answers_session_idx on live_answers (session_id, question_index);

-- ---------------------------------------------------------------------------
-- Views used for results
-- ---------------------------------------------------------------------------

-- Best completed solo game per player, per quiz, per style of play (what the leaderboard shows)
create view quiz_best_runs as
select distinct on (r.quiz_id, r.player_id, r.mode)
  r.quiz_id, r.player_id, r.mode, r.id as run_id, r.score, r.badge, r.decade,
  r.streak_max, r.correct_answers, r.questions_answered, r.passed, r.finished_at
from runs r
where r.status = 'completed'
order by r.quiz_id, r.player_id, r.mode, r.score desc, r.finished_at asc;

-- How much of the report each player has actually engaged with
create view player_quiz_stats as
select
  ri.quiz_id,
  ri.player_id,
  count(*) filter (where ri.answered_at is not null and ri.slot <> 'music')  as report_answered,
  count(*) filter (where ri.is_correct and ri.slot <> 'music')               as report_correct,
  count(distinct ri.question_id) filter (where ri.answered_at is not null)   as unique_questions,
  count(distinct ri.section_id)  filter (where ri.answered_at is not null)   as sections_seen,
  count(distinct ri.section_id)  filter (where ri.is_correct)                as sections_cleared,
  count(distinct ri.run_id)                                                  as games,
  count(*) filter (where 'peek' = any (ri.lifelines_used))                   as peeks_used,
  max(ri.answered_at)                                                        as last_played_at
from run_items ri
group by ri.quiz_id, ri.player_id;

-- Per-section accuracy across all players: which parts of the report did not land
create view section_stats as
select
  ri.quiz_id,
  ri.section_id,
  count(*) filter (where ri.answered_at is not null) as answered,
  count(*) filter (where ri.is_correct)              as correct,
  count(distinct ri.player_id)                       as players
from run_items ri
where ri.section_id is not null and ri.slot <> 'music'
group by ri.quiz_id, ri.section_id;
