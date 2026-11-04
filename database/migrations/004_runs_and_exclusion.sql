-- Two corrections found by the first CI runs:
--
-- run_id groups a collection run, so a report can say what *this* run
-- added instead of everything fetched in a time window.
--
-- excluded separates "off topic for us" from "not for publication": a
-- real advisory should keep its relevance even when its text also
-- contains a word like "webinar".

ALTER TABLE source_runs ADD COLUMN run_id TEXT;

CREATE INDEX IF NOT EXISTS idx_source_runs_run ON source_runs(run_id);

ALTER TABLE research_items ADD COLUMN excluded INTEGER NOT NULL DEFAULT 0;

CREATE INDEX IF NOT EXISTS idx_research_excluded
ON research_items(excluded, relevance DESC);
