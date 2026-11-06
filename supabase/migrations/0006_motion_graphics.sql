-- Motion graphics planned per scene (route maps, place zooms, counters, bars, timelines, key points, quotes).
-- null = not planned yet; [] = planned, none fit.
alter table scenes add column if not exists graphics jsonb;
