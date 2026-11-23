-- Corporate soundtrack governance: per-track admin approval so organizations can lock games
-- to vetted tracks only, plus fast filtering by active + approved state.

alter table music_tracks
  add column if not exists approved boolean not null default false,
  add column if not exists approved_at timestamptz,
  add column if not exists approved_by uuid references admin_users (id) on delete set null;

create index if not exists music_tracks_decade_approved_idx
  on music_tracks (decade)
  where active and approved;
