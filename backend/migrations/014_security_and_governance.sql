-- Per-quiz authorization memberships & quiz-scoped voice cache deletion.

create table if not exists quiz_memberships (
    quiz_id uuid not null references quizzes(id) on delete cascade,
    player_id uuid not null references players(id) on delete cascade,
    joined_at timestamptz not null default now(),
    primary key (quiz_id, player_id)
);
create index if not exists quiz_memberships_player on quiz_memberships (player_id);

-- Backfill memberships for existing players who already played or joined a live room on a quiz.
insert into quiz_memberships (quiz_id, player_id)
select distinct quiz_id, player_id from runs
on conflict do nothing;

insert into quiz_memberships (quiz_id, player_id)
select distinct ls.quiz_id, lp.player_id
from live_participants lp
join live_sessions ls on ls.id = lp.session_id
on conflict do nothing;

alter table host_line_audio
    add column if not exists quiz_id uuid references quizzes(id) on delete cascade;
create index if not exists host_line_audio_quiz on host_line_audio (quiz_id);
