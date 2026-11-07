-- Captions are a separate file viewers can switch on (YouTube CC button), not burned into the picture.
alter table projects alter column caption_mode set default 'sidecar';
update projects set caption_mode = 'sidecar' where caption_mode in ('burned', 'both');
