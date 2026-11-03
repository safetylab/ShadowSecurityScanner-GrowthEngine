-- Market research: collected items and per-source run history.

CREATE TABLE IF NOT EXISTS research_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    title TEXT NOT NULL,
    link TEXT NOT NULL,
    canonical_link TEXT NOT NULL UNIQUE,
    published TEXT,
    summary TEXT,
    topic TEXT,
    relevance INTEGER NOT NULL DEFAULT 0,
    fetched_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_research_published
ON research_items(published);

CREATE INDEX IF NOT EXISTS idx_research_relevance
ON research_items(relevance DESC);

CREATE INDEX IF NOT EXISTS idx_research_source
ON research_items(source);

CREATE TABLE IF NOT EXISTS source_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('ok', 'failed', 'skipped')),
    items_seen INTEGER NOT NULL DEFAULT 0,
    items_new INTEGER NOT NULL DEFAULT 0,
    duration_ms INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_source_runs_started
ON source_runs(started_at);
