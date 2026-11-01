CREATE TABLE IF NOT EXISTS node_timings(
 run_id TEXT NOT NULL, clause_id TEXT NOT NULL, node_name TEXT NOT NULL, input_hash TEXT NOT NULL,
 elapsed_seconds REAL NOT NULL, created_at TEXT NOT NULL,
 PRIMARY KEY(run_id,clause_id,node_name,input_hash)
);
CREATE INDEX IF NOT EXISTS attempts_by_run ON model_attempts(run_id,logical_node_name);
CREATE INDEX IF NOT EXISTS events_by_job ON job_events(job_id,event_id);
INSERT OR IGNORE INTO schema_migrations VALUES(2);
