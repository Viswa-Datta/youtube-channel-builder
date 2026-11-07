-- Publishing plan per channel (upload kit): fixed weekdays + time in the audience's time zone.
alter table channels add column if not exists publish_days text[] not null default '{tue,sat}';
alter table channels add column if not exists publish_time text not null default '13:00'
  check (publish_time ~ '^([01]\d|2[0-3]):[0-5]\d$');
alter table channels add column if not exists publish_tz text not null default 'America/Chicago';
alter table channels add column if not exists youtube_url text;   -- the channel page, for the subscribe link
-- planned_at: the slot the video is scheduled for in YouTube Studio; posted_at: when the user confirmed the link.
alter table publishes add column if not exists planned_at timestamptz;
alter table publishes add column if not exists posted_at timestamptz;
