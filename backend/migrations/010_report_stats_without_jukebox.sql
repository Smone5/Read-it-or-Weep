-- The admin's per-player report numbers counted Office Jukebox's jargon questions as report questions
-- (they are music, not the report). Same view, with every music slot left out.
create or replace view player_quiz_stats as
select
  ri.quiz_id,
  ri.player_id,
  count(*) filter (where ri.answered_at is not null and ri.slot not in ('music', 'jargon', 'song_or_jargon')) as report_answered,
  count(*) filter (where ri.is_correct and ri.slot not in ('music', 'jargon', 'song_or_jargon'))              as report_correct,
  count(distinct ri.question_id) filter (where ri.answered_at is not null)   as unique_questions,
  count(distinct ri.section_id)  filter (where ri.answered_at is not null)   as sections_seen,
  count(distinct ri.section_id)  filter (where ri.is_correct)                as sections_cleared,
  count(distinct ri.run_id)                                                  as games,
  count(*) filter (where 'peek' = any (ri.lifelines_used))                   as peeks_used,
  max(ri.answered_at)                                                        as last_played_at
from run_items ri
group by ri.quiz_id, ri.player_id;
