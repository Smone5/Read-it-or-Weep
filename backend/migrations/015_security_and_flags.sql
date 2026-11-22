-- Prevent single-player duplicate flagging TOCTOU race, support access-code rotation,
-- and cascade-delete quiz-scoped host line banks when a quiz is deleted.

delete from question_flags a
using question_flags b
where a.ctid < b.ctid
  and a.question_id = b.question_id
  and a.player_id = b.player_id;

create unique index if not exists question_flags_unique_player
    on question_flags (question_id, player_id);

alter table quizzes
    add column if not exists access_version integer not null default 1;

alter table host_line_bank
    add column if not exists quiz_id uuid references quizzes(id) on delete cascade;
create index if not exists host_line_bank_quiz on host_line_bank (quiz_id);
