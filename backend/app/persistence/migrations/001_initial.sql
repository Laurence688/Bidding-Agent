CREATE TABLE IF NOT EXISTS schema_migrations(version INTEGER PRIMARY KEY);
CREATE TABLE IF NOT EXISTS local_users(id TEXT PRIMARY KEY, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS records(
 id TEXT PRIMARY KEY, kind TEXT NOT NULL, user_id TEXT NOT NULL REFERENCES local_users(id),
 project_id TEXT REFERENCES records(id), parent_id TEXT REFERENCES records(id),
 version INTEGER NOT NULL DEFAULT 1, data TEXT NOT NULL CHECK(json_valid(data)),
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL, deleted INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS records_scope ON records(kind,user_id,project_id,parent_id,created_at,id);
CREATE TABLE IF NOT EXISTS idempotency_records(
 user_id TEXT NOT NULL, project_id TEXT NOT NULL, operation TEXT NOT NULL,
 key TEXT NOT NULL, body_hash TEXT NOT NULL, response TEXT NOT NULL,
 PRIMARY KEY(user_id,project_id,operation,key)
);
CREATE TABLE IF NOT EXISTS job_events(
 event_id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT NOT NULL REFERENCES records(id),
 type TEXT NOT NULL, data TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS node_commits(
 run_id TEXT NOT NULL REFERENCES records(id), clause_id TEXT NOT NULL, node_name TEXT NOT NULL,
 input_hash TEXT NOT NULL, output TEXT NOT NULL, created_at TEXT NOT NULL,
 PRIMARY KEY(run_id,clause_id,node_name,input_hash)
);
CREATE TABLE IF NOT EXISTS model_attempts(
 attempt_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, clause_id TEXT NOT NULL, claim_id TEXT NOT NULL,
 logical_node_name TEXT NOT NULL, attempt_index INTEGER NOT NULL, provider TEXT NOT NULL,
 model TEXT NOT NULL, request_hash TEXT NOT NULL, status TEXT NOT NULL, data TEXT NOT NULL,
 UNIQUE(run_id,clause_id,claim_id,logical_node_name,attempt_index)
);
CREATE TABLE IF NOT EXISTS worker_leases(
 name TEXT PRIMARY KEY, owner TEXT NOT NULL, pid INTEGER NOT NULL, started REAL NOT NULL, heartbeat REAL NOT NULL
);
CREATE VIRTUAL TABLE IF NOT EXISTS chunk_fts USING fts5(chunk_id UNINDEXED, product_id UNINDEXED, snapshot_id UNINDEXED, tokens);
INSERT OR IGNORE INTO schema_migrations VALUES(1);
