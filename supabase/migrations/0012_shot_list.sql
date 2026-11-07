-- Sentence-level shot list per scene: which photo/clip shows what the narrator is saying, plus an on-screen label.
alter table scenes add column if not exists shots jsonb;
alter table scenes add column if not exists shots_hash text;
