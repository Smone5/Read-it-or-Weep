-- AI-verified visual exhibit to display on Answer Reveal for a text/table question.
-- Only populated when a chart, map, or diagram genuinely illustrates or proves the answer
-- (never via loose keyword overlap).

alter table questions add column if not exists reveal_image_id uuid references doc_images(id) on delete set null;
