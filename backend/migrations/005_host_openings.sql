-- What the host said to open each game, so the next game's opening can be different.
--
-- The opening is the show's big moment - the host introduces the show, themselves and the contestant, and
-- explains how it works - and a player hears it every game. The host is shown the player's last few
-- openings and told not to repeat them.

alter table runs add column if not exists host_opening text;
