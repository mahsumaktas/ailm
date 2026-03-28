-- ailm v2 schema

CREATE TABLE IF NOT EXISTS events (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp    TEXT    NOT NULL,
    type         TEXT    NOT NULL,
    severity     TEXT    NOT NULL DEFAULT 'info',
    summary      TEXT,
    summary_hash TEXT,
    raw_data     TEXT,
    source       TEXT    NOT NULL DEFAULT '',
    user_action  TEXT,
    embedding    BLOB
);

CREATE INDEX IF NOT EXISTS idx_events_timestamp    ON events(timestamp);
CREATE INDEX IF NOT EXISTS idx_events_type         ON events(type);
CREATE INDEX IF NOT EXISTS idx_events_severity     ON events(severity);
-- idx_events_summary_hash created by migration v2 (avoids error on pre-v2 tables)

CREATE TABLE IF NOT EXISTS preferences (
    pattern      TEXT UNIQUE NOT NULL,
    learned_pref TEXT,
    confidence   REAL    DEFAULT 0.0,
    sample_count INTEGER DEFAULT 0,
    last_updated TEXT
);

CREATE TABLE IF NOT EXISTS skills (
    trigger       TEXT UNIQUE NOT NULL,
    solution      TEXT,
    success_count INTEGER DEFAULT 0,
    failure_count INTEGER DEFAULT 0,
    last_used     TEXT
);

CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER NOT NULL
);
