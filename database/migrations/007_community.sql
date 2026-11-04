-- Community radar: public threads worth a human reply.

CREATE TABLE IF NOT EXISTS community_signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    platform TEXT NOT NULL,
    external_id TEXT NOT NULL,
    url TEXT NOT NULL,
    canonical_url TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    author TEXT,
    excerpt TEXT,
    created_at_source TEXT,
    points INTEGER NOT NULL DEFAULT 0,
    comments INTEGER NOT NULL DEFAULT 0,
    relevance INTEGER NOT NULL DEFAULT 0,
    intent TEXT NOT NULL DEFAULT 'unknown'
        CHECK(intent IN ('asking', 'discussing', 'mentioning', 'unknown')),
    advice TEXT NOT NULL DEFAULT 'skip'
        CHECK(advice IN ('reply', 'consider', 'skip')),
    reasons TEXT NOT NULL DEFAULT '[]',
    query TEXT,
    found_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    reviewed INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_community_advice
ON community_signals(advice, relevance DESC);

CREATE INDEX IF NOT EXISTS idx_community_found
ON community_signals(found_at);
