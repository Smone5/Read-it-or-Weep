-- The side game is a style of play of its own, with two kinds of question of its own.
alter table runs drop constraint if exists runs_mode_check;
alter table runs add constraint runs_mode_check
    check (mode in ('show', 'sudden_death', 'speed', 'straight', 'jukebox'));
alter table run_items drop constraint if exists run_items_slot_check;
alter table run_items add constraint run_items_slot_check
    check (slot in ('report', 'image', 'music', 'fact_fake', 'second_chance', 'final', 'jargon', 'song_or_jargon'));
