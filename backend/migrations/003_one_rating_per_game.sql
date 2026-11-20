-- One rating per player per game.
--
-- Tapping a different star on the results screen used to save a second rating, so a player who
-- changed their mind was counted twice. Keep only the newest rating for each game, carrying over a
-- written comment from an older one so that no comment is lost, then make sure each player can
-- only ever have one rating per game (rating it again replaces the first).

with ranked as (
  select id,
         row_number() over (partition by player_id, run_id order by created_at desc, id desc) as newest,
         first_value(nullif(comment, '')) over (
           partition by player_id, run_id
           order by (nullif(comment, '') is null), created_at desc, id desc
         ) as last_comment
  from feedback
  where run_id is not null and player_id is not null
)
update feedback f
   set comment = ranked.last_comment
  from ranked
 where f.id = ranked.id
   and ranked.newest = 1
   and nullif(f.comment, '') is null
   and ranked.last_comment is not null;

delete from feedback f
 using (
   select id, row_number() over (partition by player_id, run_id order by created_at desc, id desc) as newest
   from feedback
   where run_id is not null and player_id is not null
 ) older
 where f.id = older.id
   and older.newest > 1;

create unique index if not exists feedback_player_run_key on feedback (player_id, run_id) where run_id is not null;
