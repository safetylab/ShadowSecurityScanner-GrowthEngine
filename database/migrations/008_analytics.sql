-- Daily metrics, one row per metric per day, so a trend survives even
-- when the source of a number goes away.

CREATE TABLE IF NOT EXISTS metrics_daily (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    day TEXT NOT NULL,
    metric TEXT NOT NULL,
    value REAL NOT NULL,
    source TEXT NOT NULL,
    recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(day, metric)
);

CREATE INDEX IF NOT EXISTS idx_metrics_metric
ON metrics_daily(metric, day);

-- Recommendations from the optimisation agent. Advisory only: nothing
-- in the engine acts on a row here without a human.
CREATE TABLE IF NOT EXISTS recommendations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    area TEXT NOT NULL,
    priority TEXT NOT NULL
        CHECK(priority IN ('high', 'medium', 'low')),
    title TEXT NOT NULL,
    detail TEXT NOT NULL,
    evidence TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_recommendations_run
ON recommendations(run_id, priority);
