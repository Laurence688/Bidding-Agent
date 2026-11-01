CREATE TABLE IF NOT EXISTS session_message_order(
 sequence INTEGER PRIMARY KEY AUTOINCREMENT,
 message_id TEXT NOT NULL UNIQUE REFERENCES records(id)
);
INSERT OR IGNORE INTO session_message_order(message_id)
 SELECT id FROM records WHERE kind='session_message' ORDER BY rowid;
CREATE TRIGGER IF NOT EXISTS register_message_order AFTER INSERT ON records
 WHEN NEW.kind='session_message'
 BEGIN INSERT INTO session_message_order(message_id) VALUES(NEW.id); END;
