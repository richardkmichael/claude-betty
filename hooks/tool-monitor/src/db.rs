//! Database module for tool monitoring capture system.
//!
//! This module provides a SQLite-based storage system optimized for capturing
//! tool usage events with generated columns for efficient analysis by separate tools.

use rusqlite::Connection;
use serde_json::Value;
use std::error::Error;
use std::fmt;

const SCHEMA_V3: &str = include_str!("../db_schema_v3.sql");

/// Errors that can occur during database operations.
#[derive(Debug)]
pub enum DatabaseError {
    /// SQLite database error.
    Sqlite(rusqlite::Error),
    /// JSON parsing error.
    Json(serde_json::Error),
    /// Invalid data provided to database operations.
    InvalidData(String),
    /// Database schema version is incompatible; migration required.
    SchemaMigrationRequired(String),
}

impl fmt::Display for DatabaseError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            DatabaseError::Sqlite(e) => write!(f, "SQLite error: {}", e),
            DatabaseError::Json(e) => write!(f, "JSON error: {}", e),
            DatabaseError::InvalidData(msg) => write!(f, "Invalid data: {}", msg),
            DatabaseError::SchemaMigrationRequired(msg) => write!(f, "{}", msg),
        }
    }
}

impl Error for DatabaseError {}

impl From<rusqlite::Error> for DatabaseError {
    fn from(error: rusqlite::Error) -> Self {
        DatabaseError::Sqlite(error)
    }
}

impl From<serde_json::Error> for DatabaseError {
    fn from(error: serde_json::Error) -> Self {
        DatabaseError::Json(error)
    }
}

/// Database handle for tool monitoring events.
#[derive(Debug)]
pub struct Database {
    conn: Connection,
}

impl Database {
    /// Create a new database instance with the given connection.
    ///
    /// On a fresh (empty) database, initialises it with the V3 schema embedded in this binary.
    /// On an existing database, verifies the schema is V3; returns
    /// `SchemaMigrationRequired` if not.
    pub fn new(conn: Connection, db_path: &str) -> Result<Self, DatabaseError> {
        let db = Database { conn };
        db.check_and_initialize(db_path)?;
        Ok(db)
    }

    /// Check schema version and initialise if this is a fresh database.
    fn check_and_initialize(&self, db_path: &str) -> Result<(), DatabaseError> {
        let schema_info_exists: bool = self
            .conn
            .query_row(
                "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='schema_info'",
                [],
                |row| row.get::<_, i64>(0),
            )
            .map(|n| n > 0)?;

        if !schema_info_exists {
            // Fresh database — initialise with the embedded V3 schema.
            self.conn.execute_batch(SCHEMA_V3)?;
            return Ok(());
        }

        let version: String = self
            .conn
            .query_row(
                "SELECT value FROM schema_info WHERE key='version'",
                [],
                |row| row.get(0),
            )
            .unwrap_or_else(|_| String::from("unknown"));

        if version.starts_with("3.") {
            Ok(())
        } else {
            Err(DatabaseError::SchemaMigrationRequired(format!(
                "database schema v{} requires migration to v3\n\
                 run: python tools/migration/migrate_to_v3.py {}",
                version, db_path
            )))
        }
    }

    /// Insert a tool event from a JSON payload.
    ///
    /// # Arguments
    ///
    /// * `json_payload` - A JSON string containing the tool event data. Must include
    ///   a `session_id` field as a string.
    pub fn insert_event(&self, json_payload: &str) -> Result<(), DatabaseError> {
        // Validate JSON first
        let parsed: Value = serde_json::from_str(json_payload)?;

        if parsed.get("session_id").is_none() {
            return Err(DatabaseError::InvalidData("Missing session_id".to_string()));
        }

        let session_id = parsed["session_id"]
            .as_str()
            .ok_or_else(|| DatabaseError::InvalidData("session_id must be a string".to_string()))?;

        self.conn.execute(
            "INSERT INTO tool_events (session_id, payload) VALUES (?1, ?2)",
            [session_id, json_payload],
        )?;

        Ok(())
    }
}

/// Create a production database with optimal configuration for concurrent access.
///
/// Opens the SQLite database at `db_path` (creating it with V3 schema if it does not
/// exist) and configures it for production use with WAL mode.
pub fn create_database(db_path: &str) -> Result<Database, DatabaseError> {
    let conn = Connection::open(db_path)?;

    let _: String = conn.query_row("PRAGMA journal_mode=WAL", [], |row| row.get(0))?;
    let _: i32 = conn.query_row("PRAGMA busy_timeout=5000", [], |row| row.get(0))?;
    conn.execute("PRAGMA synchronous=NORMAL", [])?;
    conn.execute("PRAGMA cache_size=10000", [])?;

    Database::new(conn, db_path)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn create_test_database() -> Result<Database, DatabaseError> {
        let conn = Connection::open(":memory:")?;
        Database::new(conn, ":memory:")
    }

    fn create_test_db() -> Database {
        create_test_database().unwrap()
    }

    #[test]
    fn test_database_creation() {
        let _db = create_test_db();
    }

    #[test]
    fn test_production_database_factory() {
        let temp_path = "/tmp/test_production.db";
        std::fs::remove_file(temp_path).ok();

        let db = create_database(temp_path).unwrap();

        let journal_mode: String = db
            .conn
            .query_row("PRAGMA journal_mode", [], |row| row.get(0))
            .unwrap();
        assert_eq!(journal_mode, "wal");

        let busy_timeout: i32 = db
            .conn
            .query_row("PRAGMA busy_timeout", [], |row| row.get(0))
            .unwrap();
        assert_eq!(busy_timeout, 5000);

        std::fs::remove_file(temp_path).ok();
        std::fs::remove_file(format!("{}-wal", temp_path)).ok();
        std::fs::remove_file(format!("{}-shm", temp_path)).ok();
    }

    #[test]
    fn test_schema_version_is_v3() {
        let db = create_test_db();
        let version: String = db
            .conn
            .query_row(
                "SELECT value FROM schema_info WHERE key='version'",
                [],
                |row| row.get(0),
            )
            .unwrap();
        assert!(version.starts_with("3."), "expected v3.x, got {}", version);
    }

    #[test]
    fn test_v2_database_returns_migration_error() {
        let conn = Connection::open(":memory:").unwrap();
        // Manually create a V2-style schema_info
        conn.execute_batch(
            "CREATE TABLE schema_info (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at DATETIME);
             INSERT INTO schema_info (key, value) VALUES ('version', '2.0.0');",
        )
        .unwrap();
        let result = Database::new(conn, "/tmp/test.db");
        assert!(matches!(
            result,
            Err(DatabaseError::SchemaMigrationRequired(_))
        ));
        if let Err(DatabaseError::SchemaMigrationRequired(msg)) = result {
            assert!(msg.contains("2.0.0"));
            assert!(msg.contains("migrate_to_v3.py"));
        }
    }

    #[test]
    fn test_factories_create_different_configurations() {
        let temp_path = "/tmp/test_factory_diff.db";
        std::fs::remove_file(temp_path).ok();

        let prod_db = create_database(temp_path).unwrap();
        let test_db = create_test_database().unwrap();

        let prod_journal: String = prod_db
            .conn
            .query_row("PRAGMA journal_mode", [], |row| row.get(0))
            .unwrap();
        assert_eq!(prod_journal, "wal");

        let test_journal: String = test_db
            .conn
            .query_row("PRAGMA journal_mode", [], |row| row.get(0))
            .unwrap();
        assert_eq!(test_journal, "memory");

        std::fs::remove_file(temp_path).ok();
        std::fs::remove_file(format!("{}-wal", temp_path)).ok();
        std::fs::remove_file(format!("{}-shm", temp_path)).ok();
    }

    #[test]
    fn test_insert_valid_json() {
        let db = create_test_db();
        let json_payload = r#"{"session_id":"test123","hook_event_name":"PreToolUse","tool_name":"Bash","tool_input":{"command":"ls","description":"test command"}}"#;
        assert!(db.insert_event(json_payload).is_ok());
    }

    #[test]
    fn test_insert_invalid_json() {
        let db = create_test_db();
        let result = db.insert_event(r#"{"invalid": json}"#);
        assert!(result.is_err());
        assert!(matches!(result.unwrap_err(), DatabaseError::Json(_)));
    }

    #[test]
    fn test_missing_session_id() {
        let db = create_test_db();
        let result = db.insert_event(r#"{"hook_event_name":"PreToolUse","tool_name":"Bash"}"#);
        assert!(result.is_err());
        match result.unwrap_err() {
            DatabaseError::InvalidData(msg) => assert_eq!(msg, "Missing session_id"),
            _ => panic!("Expected InvalidData error"),
        }
    }

    #[test]
    fn test_generated_columns() {
        let db = create_test_db();
        let json_payload = r#"{"session_id":"test123","hook_event_name":"PostToolUse","tool_name":"Bash","cwd":"/home/user","transcript_path":"/path/to/transcript.jsonl","tool_input":{"command":"ls -la","description":"list files"}}"#;
        db.insert_event(json_payload).unwrap();

        let mut stmt = db
            .conn
            .prepare("SELECT hook_event_name, tool_name, cwd, transcript_path FROM tool_events WHERE session_id = ?1")
            .unwrap();
        let row = stmt
            .query_row(["test123"], |row| {
                Ok((
                    row.get::<_, String>(0)?,
                    row.get::<_, String>(1)?,
                    row.get::<_, String>(2)?,
                    row.get::<_, String>(3)?,
                ))
            })
            .unwrap();

        assert_eq!(row.0, "PostToolUse");
        assert_eq!(row.1, "Bash");
        assert_eq!(row.2, "/home/user");
        assert_eq!(row.3, "/path/to/transcript.jsonl");
    }

    #[test]
    fn test_json_extraction_at_runtime() {
        let db = create_test_db();

        db.insert_event(r#"{"session_id":"test1","tool_name":"Bash","hook_event_name":"PreToolUse","cwd":"/home","transcript_path":"/path","tool_input":{"command":"ls -la"}}"#).unwrap();
        db.insert_event(r#"{"session_id":"test2","tool_name":"Edit","hook_event_name":"PreToolUse","cwd":"/home","transcript_path":"/path","tool_input":{"file_path":"/test.txt"}}"#).unwrap();

        let bash_command: String = db
            .conn
            .query_row(
                "SELECT json_extract(payload, '$.tool_input.command') FROM tool_events WHERE session_id = 'test1'",
                [],
                |row| row.get(0),
            )
            .unwrap();
        assert_eq!(bash_command, "ls -la");

        let file_path: String = db
            .conn
            .query_row(
                "SELECT json_extract(payload, '$.tool_input.file_path') FROM tool_events WHERE session_id = 'test2'",
                [],
                |row| row.get(0),
            )
            .unwrap();
        assert_eq!(file_path, "/test.txt");
    }

    #[test]
    fn test_multiple_inserts() {
        let db = create_test_db();

        for payload in &[
            r#"{"session_id":"session1","hook_event_name":"PreToolUse","tool_name":"Bash","tool_input":{"command":"ls"}}"#,
            r#"{"session_id":"session1","hook_event_name":"PostToolUse","tool_name":"Bash","tool_response":{"stdout":"output","stderr":""}}"#,
            r#"{"session_id":"session2","hook_event_name":"PreToolUse","tool_name":"Read","tool_input":{"file_path":"/test"}}"#,
        ] {
            db.insert_event(payload).unwrap();
        }

        let count: i32 = db
            .conn
            .query_row("SELECT COUNT(*) FROM tool_events", [], |row| row.get(0))
            .unwrap();
        assert_eq!(count, 3);

        let session_count: i32 = db
            .conn
            .query_row(
                "SELECT COUNT(DISTINCT session_id) FROM tool_events",
                [],
                |row| row.get(0),
            )
            .unwrap();
        assert_eq!(session_count, 2);
    }

    #[test]
    fn test_indexes_exist() {
        let db = create_test_db();
        let mut stmt = db
            .conn
            .prepare("SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'idx_%'")
            .unwrap();
        let index_names: Vec<String> = stmt
            .query_map([], |row| row.get(0))
            .unwrap()
            .collect::<Result<Vec<_>, _>>()
            .unwrap();

        for expected in &[
            "idx_session_id",
            "idx_tool_name",
            "idx_hook_event_name",
            "idx_created_at",
            "idx_cwd",
        ] {
            assert!(
                index_names.contains(&expected.to_string()),
                "Missing index: {}",
                expected
            );
        }
    }

    #[test]
    fn test_fts5_tables_exist() {
        let db = create_test_db();
        let mut stmt = db
            .conn
            .prepare("SELECT name FROM sqlite_master WHERE sql LIKE 'CREATE VIRTUAL TABLE%' AND name LIKE 'fts_%'")
            .unwrap();
        let fts_tables: Vec<String> = stmt
            .query_map([], |row| row.get(0))
            .unwrap()
            .collect::<Result<Vec<_>, _>>()
            .unwrap();

        for expected in &[
            "fts_bash_command",
            "fts_file_path",
            "fts_grep_pattern",
            "fts_glob_pattern",
            "fts_url",
            "fts_search_query",
        ] {
            assert!(
                fts_tables.contains(&expected.to_string()),
                "Missing FTS5 table: {}",
                expected
            );
        }
    }

    #[test]
    fn test_fts5_triggers_fire_on_insert() {
        let db = create_test_db();
        db.insert_event(r#"{"session_id":"fts_test","tool_name":"Bash","hook_event_name":"PreToolUse","cwd":"/","transcript_path":"/t","tool_input":{"command":"git status","description":"check status"}}"#).unwrap();

        let count: i32 = db
            .conn
            .query_row("SELECT COUNT(*) FROM fts_bash_command", [], |row| {
                row.get(0)
            })
            .unwrap();
        assert_eq!(count, 1);

        // FTS5 MATCH search works, and the FTS rowid is the tool_events id
        let event_id: i64 = db
            .conn
            .query_row("SELECT id FROM tool_events WHERE session_id = 'fts_test'", [], |row| {
                row.get(0)
            })
            .unwrap();
        let found: i64 = db
            .conn
            .query_row(
                "SELECT rowid FROM fts_bash_command WHERE command MATCH 'git'",
                [],
                |row| row.get(0),
            )
            .unwrap();
        assert_eq!(found, event_id);

        // The FTS table stores the text, so LIKE on its column works
        let liked: i64 = db
            .conn
            .query_row(
                "SELECT rowid FROM fts_bash_command WHERE command LIKE '%t st%'",
                [],
                |row| row.get(0),
            )
            .unwrap();
        assert_eq!(liked, event_id);
    }

    fn fts_rowids(db: &Database, table: &str, term: &str) -> Vec<i64> {
        let sql = format!("SELECT rowid FROM {table} WHERE {table} MATCH ?1 ORDER BY rowid");
        let mut stmt = db.conn.prepare(&sql).unwrap();
        stmt.query_map([term], |row| row.get(0))
            .unwrap()
            .collect::<Result<Vec<_>, _>>()
            .unwrap()
    }

    #[test]
    fn test_fts5_delete_trigger_removes_entry() {
        let db = create_test_db();
        db.insert_event(r#"{"session_id":"keep","tool_name":"Bash","hook_event_name":"PreToolUse","tool_input":{"command":"git status"}}"#).unwrap();
        db.insert_event(r#"{"session_id":"drop","tool_name":"Bash","hook_event_name":"PreToolUse","tool_input":{"command":"git log"}}"#).unwrap();
        db.insert_event(r#"{"session_id":"drop","tool_name":"Read","hook_event_name":"PreToolUse","tool_input":{"file_path":"/repo/.git/config"}}"#).unwrap();

        db.conn
            .execute("DELETE FROM tool_events WHERE session_id = 'drop'", [])
            .unwrap();

        let kept_id: i64 = db
            .conn
            .query_row("SELECT id FROM tool_events WHERE session_id = 'keep'", [], |row| {
                row.get(0)
            })
            .unwrap();
        assert_eq!(fts_rowids(&db, "fts_bash_command", "\"git\""), vec![kept_id]);
        assert!(fts_rowids(&db, "fts_file_path", "\"git\"").is_empty());
    }

    #[test]
    fn test_fts5_update_trigger_reindexes_entry() {
        let db = create_test_db();
        db.insert_event(r#"{"session_id":"upd","tool_name":"Bash","hook_event_name":"PreToolUse","tool_input":{"command":"cargo build"}}"#).unwrap();
        let id: i64 = db
            .conn
            .query_row("SELECT id FROM tool_events WHERE session_id = 'upd'", [], |row| {
                row.get(0)
            })
            .unwrap();

        // New text replaces the old in the index
        db.conn
            .execute(
                r#"UPDATE tool_events SET payload = '{"session_id":"upd","tool_name":"Bash","hook_event_name":"PreToolUse","tool_input":{"command":"cargo test"}}' WHERE id = ?1"#,
                [id],
            )
            .unwrap();
        assert!(fts_rowids(&db, "fts_bash_command", "\"build\"").is_empty());
        assert_eq!(fts_rowids(&db, "fts_bash_command", "\"test\""), vec![id]);

        // Changing the tool moves the entry to the other tool's table
        db.conn
            .execute(
                r#"UPDATE tool_events SET payload = '{"session_id":"upd","tool_name":"Read","hook_event_name":"PreToolUse","tool_input":{"file_path":"/tmp/test.txt"}}' WHERE id = ?1"#,
                [id],
            )
            .unwrap();
        assert!(fts_rowids(&db, "fts_bash_command", "\"test\"").is_empty());
        assert_eq!(fts_rowids(&db, "fts_file_path", "\"test\""), vec![id]);
    }

    #[test]
    fn test_schema_table_structure() {
        let db = create_test_db();
        let create_sql: String = db
            .conn
            .query_row(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name='tool_events'",
                [],
                |row| row.get(0),
            )
            .unwrap();

        assert!(create_sql.contains("hook_event_name TEXT GENERATED ALWAYS AS"));
        assert!(create_sql.contains("tool_name TEXT GENERATED ALWAYS AS"));
        assert!(create_sql.contains("cwd TEXT GENERATED ALWAYS AS"));
        assert!(create_sql.contains("transcript_path TEXT GENERATED ALWAYS AS"));

        // V1 columns must not be present
        assert!(!create_sql.contains("command TEXT GENERATED ALWAYS AS"));
        assert!(!create_sql.contains("description TEXT GENERATED ALWAYS AS"));
        assert!(!create_sql.contains("command_word TEXT GENERATED ALWAYS AS"));

        let columns: Vec<String> = {
            let mut stmt = db.conn.prepare("PRAGMA table_info(tool_events)").unwrap();
            stmt.query_map([], |row| row.get::<_, String>(1))
                .unwrap()
                .collect::<Result<Vec<_>, _>>()
                .unwrap()
        };
        for expected in &["id", "session_id", "payload", "created_at"] {
            assert!(columns.contains(&expected.to_string()), "Missing column: {}", expected);
        }
    }

    #[test]
    fn test_tool_schemas_has_no_file_name_column() {
        let db = create_test_db();
        let columns: Vec<String> = {
            let mut stmt = db.conn.prepare("PRAGMA table_info(tool_schemas)").unwrap();
            stmt.query_map([], |row| row.get::<_, String>(1))
                .unwrap()
                .collect::<Result<Vec<_>, _>>()
                .unwrap()
        };
        assert!(
            !columns.contains(&"file_name".to_string()),
            "tool_schemas should not have file_name column in V3"
        );
    }

    #[test]
    fn test_concurrent_access_simulation() {
        use std::thread;

        let temp_path = "/tmp/test_concurrent.db";
        std::fs::remove_file(temp_path).ok();
        { let _db = create_database(temp_path).unwrap(); }

        let handles: Vec<_> = (0..3)
            .map(|thread_id| {
                let path = temp_path.to_string();
                thread::spawn(move || {
                    let db = create_database(&path).unwrap();
                    for i in 0..5 {
                        let payload = format!(
                            r#"{{"session_id":"thread{}_event{}","tool_name":"Bash","tool_input":{{"command":"echo {}"}}}}"#,
                            thread_id, i, i
                        );
                        db.insert_event(&payload).unwrap();
                    }
                })
            })
            .collect();

        for h in handles { h.join().unwrap(); }

        let db = create_database(temp_path).unwrap();
        let count: i32 = db
            .conn
            .query_row("SELECT COUNT(*) FROM tool_events", [], |row| row.get(0))
            .unwrap();
        assert_eq!(count, 15);

        std::fs::remove_file(temp_path).ok();
        std::fs::remove_file(format!("{}-wal", temp_path)).ok();
        std::fs::remove_file(format!("{}-shm", temp_path)).ok();
    }

    #[test]
    fn test_invalid_database_path() {
        let result = create_database("/nonexistent/directory/test.db");
        assert!(result.is_err());
        assert!(matches!(result.unwrap_err(), DatabaseError::Sqlite(_)));
    }

    #[test]
    fn test_large_json_payload() {
        let db = create_test_db();
        let large_command = "x".repeat(10000);
        let payload = format!(
            r#"{{"session_id":"large_test","tool_name":"Bash","hook_event_name":"PreToolUse","cwd":"/home","transcript_path":"/path/to/transcript.jsonl","tool_input":{{"command":"{}","description":"Large command test"}}}}"#,
            large_command
        );
        assert!(db.insert_event(&payload).is_ok());

        let stored_command: String = db
            .conn
            .query_row(
                "SELECT json_extract(payload, '$.tool_input.command') FROM tool_events WHERE session_id = 'large_test'",
                [],
                |row| row.get(0),
            )
            .unwrap();
        assert_eq!(stored_command, large_command);
    }

    #[test]
    fn test_unicode_json_payload() {
        let db = create_test_db();
        let payload = r#"{"session_id":"unicode_test","tool_name":"Bash","tool_input":{"command":"echo '你好世界 🌍'","description":"Unicode test with émojis and ñoñ-ASCII çhars"}}"#;
        assert!(db.insert_event(payload).is_ok());

        let command: String = db
            .conn
            .query_row(
                "SELECT json_extract(payload, '$.tool_input.command') FROM tool_events WHERE session_id = 'unicode_test'",
                [],
                |row| row.get(0),
            )
            .unwrap();
        assert_eq!(command, "echo '你好世界 🌍'");
    }

    #[test]
    fn test_session_id_validation_types() {
        let db = create_test_db();

        let cases = [
            (r#"{"session_id":"normal-uuid","tool_name":"Test"}"#, true),
            (r#"{"session_id":"123","tool_name":"Test"}"#, true),
            (r#"{"session_id":"","tool_name":"Test"}"#, true),
            (r#"{"session_id":123,"tool_name":"Test"}"#, false),
            (r#"{"session_id":null,"tool_name":"Test"}"#, false),
            (r#"{"tool_name":"Test"}"#, false),
        ];

        for (payload, should_succeed) in &cases {
            let result = db.insert_event(payload);
            assert_eq!(result.is_ok(), *should_succeed, "payload: {}", payload);
        }
    }
}
