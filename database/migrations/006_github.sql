-- GitHub repository health: one findings table plus a snapshot of the
-- counters, so trends can be read later without a second API call.

CREATE TABLE IF NOT EXISTS github_findings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    repository TEXT NOT NULL,
    severity TEXT NOT NULL
        CHECK(severity IN ('critical', 'high', 'medium', 'low')),
    code TEXT NOT NULL,
    message TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_github_findings_run
ON github_findings(run_id, severity);

CREATE TABLE IF NOT EXISTS github_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    repository TEXT NOT NULL,
    stars INTEGER NOT NULL DEFAULT 0,
    forks INTEGER NOT NULL DEFAULT 0,
    watchers INTEGER NOT NULL DEFAULT 0,
    open_issues INTEGER NOT NULL DEFAULT 0,
    open_pull_requests INTEGER NOT NULL DEFAULT 0,
    releases INTEGER NOT NULL DEFAULT 0,
    latest_release TEXT,
    latest_release_at TEXT,
    pushed_at TEXT,
    contributors INTEGER NOT NULL DEFAULT 0,
    taken_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_github_snapshots_taken
ON github_snapshots(repository, taken_at);
