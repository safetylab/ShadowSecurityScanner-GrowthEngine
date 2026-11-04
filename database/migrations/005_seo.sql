-- SEO audits: one row per page per run, plus the findings for it.

CREATE TABLE IF NOT EXISTS seo_pages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    url TEXT NOT NULL,
    status INTEGER NOT NULL,
    title TEXT,
    description TEXT,
    canonical TEXT,
    internal_links INTEGER NOT NULL DEFAULT 0,
    external_links INTEGER NOT NULL DEFAULT 0,
    bytes INTEGER NOT NULL DEFAULT 0,
    duration_ms INTEGER NOT NULL DEFAULT 0,
    checked_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(run_id, url)
);

CREATE INDEX IF NOT EXISTS idx_seo_pages_run ON seo_pages(run_id);

CREATE TABLE IF NOT EXISTS seo_findings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    url TEXT NOT NULL,
    severity TEXT NOT NULL
        CHECK(severity IN ('critical', 'high', 'medium', 'low')),
    code TEXT NOT NULL,
    message TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_seo_findings_run
ON seo_findings(run_id, severity);

CREATE TABLE IF NOT EXISTS seo_links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    url TEXT NOT NULL,
    found_on TEXT NOT NULL,
    kind TEXT NOT NULL CHECK(kind IN ('internal', 'external')),
    status INTEGER,
    error TEXT,
    checked_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(run_id, url, found_on)
);

CREATE INDEX IF NOT EXISTS idx_seo_links_run ON seo_links(run_id, status);
