-- Tool Monitor Database Schema V3
-- V3 additions over V2:
--   - Expression indexes on tool_input fields (fast equality lookups)
--   - FTS5 trigram virtual tables + triggers (fast wildcard/substring search)
--   - schema_info.hook_common_fields documents fields present in all hook payloads
--   - tool_schemas.indexed_fields stores per-tool FTS index coverage (path, fts_table, fts_column)
--   - tool_schemas.file_name generated column removed (was broken for new hook events)
--   - PostToolUseFailure and PermissionRequest hook coverage
-- V3.1 changes over V3.0:
--   - FTS5 tables are keyed by rowid = tool_events.id, replacing the event_id UNINDEXED column
--   - AFTER DELETE and AFTER UPDATE triggers join the AFTER INSERT triggers, keeping the FTS5
--     tables in step with tool_events on every change

-- Database schema version tracking
CREATE TABLE schema_info (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

INSERT INTO schema_info (key, value) VALUES ('version', '3.1.0');
INSERT INTO schema_info (key, value) VALUES ('description', 'V3.1: FTS5 trigram indexes keyed by tool_events.id and kept in step on insert, delete and update, expression indexes, expanded hook coverage');
INSERT INTO schema_info (key, value) VALUES (
    'hook_common_fields',
    '["session_id","hook_event_name","tool_name","cwd","transcript_path"]'
);

-- Main events table: only extract stable hook contract fields as generated columns
CREATE TABLE tool_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    payload TEXT NOT NULL,

    -- Stable hook contract fields (guaranteed by Claude Code across all hook events)
    hook_event_name TEXT GENERATED ALWAYS AS (json_extract(payload, '$.hook_event_name')) STORED,
    tool_name TEXT GENERATED ALWAYS AS (json_extract(payload, '$.tool_name')) STORED,
    cwd TEXT GENERATED ALWAYS AS (json_extract(payload, '$.cwd')) STORED,
    transcript_path TEXT GENERATED ALWAYS AS (json_extract(payload, '$.transcript_path')) STORED,

    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- Basic indexes
CREATE INDEX idx_session_id ON tool_events(session_id);
CREATE INDEX idx_tool_name ON tool_events(tool_name);
CREATE INDEX idx_hook_event_name ON tool_events(hook_event_name);
CREATE INDEX idx_created_at ON tool_events(created_at);
CREATE INDEX idx_cwd ON tool_events(cwd);

-- Expression indexes: fast equality lookups on tool_input fields
-- These complement FTS5: use for exact matches, FTS5 for wildcard/substring.
CREATE INDEX idx_bash_command
    ON tool_events(json_extract(payload, '$.tool_input.command'))
    WHERE tool_name = 'Bash';

CREATE INDEX idx_file_path
    ON tool_events(json_extract(payload, '$.tool_input.file_path'))
    WHERE tool_name IN ('Read', 'Write', 'Edit');

CREATE INDEX idx_grep_pattern
    ON tool_events(json_extract(payload, '$.tool_input.pattern'))
    WHERE tool_name = 'Grep';

CREATE INDEX idx_glob_pattern
    ON tool_events(json_extract(payload, '$.tool_input.pattern'))
    WHERE tool_name = 'Glob';

-- Tool schema registry (no file_name generated column — naming convention in schema_info)
CREATE TABLE tool_schemas (
    tool_name TEXT NOT NULL,
    hook_event TEXT NOT NULL,   -- 'PreToolUse', 'PostToolUse', 'PostToolUseFailure', or 'PermissionRequest'
    schema_version TEXT NOT NULL,
    schema_json TEXT NOT NULL,  -- Complete JSON Schema document
    category TEXT,              -- File, Search, System, Web, Workflow
    description TEXT,
    indexed_fields TEXT,        -- JSON array of {path, fts_table, fts_column} for this tool's FTS coverage

    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (tool_name, hook_event, schema_version)
);

-- Track which schema version was active at what time
CREATE TABLE schema_versions (
    tool_name TEXT NOT NULL,
    hook_event TEXT NOT NULL,
    version TEXT NOT NULL,
    active_from DATE NOT NULL,
    active_to DATE,
    notes TEXT,
    PRIMARY KEY (tool_name, hook_event, version),
    FOREIGN KEY (tool_name, hook_event, version) REFERENCES tool_schemas(tool_name, hook_event, schema_version)
);

CREATE INDEX idx_schema_active ON schema_versions(tool_name, hook_event, active_from, active_to);

-- FTS5 trigram tables: fast wildcard/substring search over tool_input fields.
-- Each table's rowid is the tool_events.id of the indexed event.
-- Query pattern: JOIN fts_bash_command f ON f.rowid = tool_events.id
--                WHERE f.command MATCH '"search-term"'
-- Note: special characters (., /, -) require phrase quoting: MATCH '"term"'

CREATE VIRTUAL TABLE fts_bash_command USING fts5(
    command,
    tokenize='trigram'
);

CREATE VIRTUAL TABLE fts_file_path USING fts5(
    file_path,
    tokenize='trigram'
);

CREATE VIRTUAL TABLE fts_grep_pattern USING fts5(
    pattern,
    tokenize='trigram'
);

CREATE VIRTUAL TABLE fts_glob_pattern USING fts5(
    pattern,
    tokenize='trigram'
);

CREATE VIRTUAL TABLE fts_url USING fts5(
    url,
    tokenize='trigram'
);

CREATE VIRTUAL TABLE fts_search_query USING fts5(
    query,
    tokenize='trigram'
);

-- Triggers keep each FTS5 table in step with tool_events on insert, delete and update.

CREATE TRIGGER trg_fts_bash_command_insert
AFTER INSERT ON tool_events
WHEN NEW.tool_name = 'Bash'
BEGIN
    INSERT INTO fts_bash_command(rowid, command)
    VALUES (NEW.id, json_extract(NEW.payload, '$.tool_input.command'));
END;

CREATE TRIGGER trg_fts_bash_command_delete
AFTER DELETE ON tool_events
WHEN OLD.tool_name = 'Bash'
BEGIN
    DELETE FROM fts_bash_command WHERE rowid = OLD.id;
END;

CREATE TRIGGER trg_fts_bash_command_update
AFTER UPDATE OF payload ON tool_events
WHEN (OLD.tool_name = 'Bash') OR (NEW.tool_name = 'Bash')
BEGIN
    DELETE FROM fts_bash_command WHERE rowid = OLD.id AND OLD.tool_name = 'Bash';
    INSERT INTO fts_bash_command(rowid, command)
    SELECT NEW.id, json_extract(NEW.payload, '$.tool_input.command') WHERE NEW.tool_name = 'Bash';
END;

CREATE TRIGGER trg_fts_file_path_insert
AFTER INSERT ON tool_events
WHEN NEW.tool_name IN ('Read', 'Write', 'Edit')
BEGIN
    INSERT INTO fts_file_path(rowid, file_path)
    VALUES (NEW.id, json_extract(NEW.payload, '$.tool_input.file_path'));
END;

CREATE TRIGGER trg_fts_file_path_delete
AFTER DELETE ON tool_events
WHEN OLD.tool_name IN ('Read', 'Write', 'Edit')
BEGIN
    DELETE FROM fts_file_path WHERE rowid = OLD.id;
END;

CREATE TRIGGER trg_fts_file_path_update
AFTER UPDATE OF payload ON tool_events
WHEN (OLD.tool_name IN ('Read', 'Write', 'Edit')) OR (NEW.tool_name IN ('Read', 'Write', 'Edit'))
BEGIN
    DELETE FROM fts_file_path WHERE rowid = OLD.id AND OLD.tool_name IN ('Read', 'Write', 'Edit');
    INSERT INTO fts_file_path(rowid, file_path)
    SELECT NEW.id, json_extract(NEW.payload, '$.tool_input.file_path') WHERE NEW.tool_name IN ('Read', 'Write', 'Edit');
END;

CREATE TRIGGER trg_fts_grep_pattern_insert
AFTER INSERT ON tool_events
WHEN NEW.tool_name = 'Grep'
BEGIN
    INSERT INTO fts_grep_pattern(rowid, pattern)
    VALUES (NEW.id, json_extract(NEW.payload, '$.tool_input.pattern'));
END;

CREATE TRIGGER trg_fts_grep_pattern_delete
AFTER DELETE ON tool_events
WHEN OLD.tool_name = 'Grep'
BEGIN
    DELETE FROM fts_grep_pattern WHERE rowid = OLD.id;
END;

CREATE TRIGGER trg_fts_grep_pattern_update
AFTER UPDATE OF payload ON tool_events
WHEN (OLD.tool_name = 'Grep') OR (NEW.tool_name = 'Grep')
BEGIN
    DELETE FROM fts_grep_pattern WHERE rowid = OLD.id AND OLD.tool_name = 'Grep';
    INSERT INTO fts_grep_pattern(rowid, pattern)
    SELECT NEW.id, json_extract(NEW.payload, '$.tool_input.pattern') WHERE NEW.tool_name = 'Grep';
END;

CREATE TRIGGER trg_fts_glob_pattern_insert
AFTER INSERT ON tool_events
WHEN NEW.tool_name = 'Glob'
BEGIN
    INSERT INTO fts_glob_pattern(rowid, pattern)
    VALUES (NEW.id, json_extract(NEW.payload, '$.tool_input.pattern'));
END;

CREATE TRIGGER trg_fts_glob_pattern_delete
AFTER DELETE ON tool_events
WHEN OLD.tool_name = 'Glob'
BEGIN
    DELETE FROM fts_glob_pattern WHERE rowid = OLD.id;
END;

CREATE TRIGGER trg_fts_glob_pattern_update
AFTER UPDATE OF payload ON tool_events
WHEN (OLD.tool_name = 'Glob') OR (NEW.tool_name = 'Glob')
BEGIN
    DELETE FROM fts_glob_pattern WHERE rowid = OLD.id AND OLD.tool_name = 'Glob';
    INSERT INTO fts_glob_pattern(rowid, pattern)
    SELECT NEW.id, json_extract(NEW.payload, '$.tool_input.pattern') WHERE NEW.tool_name = 'Glob';
END;

CREATE TRIGGER trg_fts_url_insert
AFTER INSERT ON tool_events
WHEN NEW.tool_name = 'WebFetch'
BEGIN
    INSERT INTO fts_url(rowid, url)
    VALUES (NEW.id, json_extract(NEW.payload, '$.tool_input.url'));
END;

CREATE TRIGGER trg_fts_url_delete
AFTER DELETE ON tool_events
WHEN OLD.tool_name = 'WebFetch'
BEGIN
    DELETE FROM fts_url WHERE rowid = OLD.id;
END;

CREATE TRIGGER trg_fts_url_update
AFTER UPDATE OF payload ON tool_events
WHEN (OLD.tool_name = 'WebFetch') OR (NEW.tool_name = 'WebFetch')
BEGIN
    DELETE FROM fts_url WHERE rowid = OLD.id AND OLD.tool_name = 'WebFetch';
    INSERT INTO fts_url(rowid, url)
    SELECT NEW.id, json_extract(NEW.payload, '$.tool_input.url') WHERE NEW.tool_name = 'WebFetch';
END;

CREATE TRIGGER trg_fts_search_query_insert
AFTER INSERT ON tool_events
WHEN NEW.tool_name = 'WebSearch'
BEGIN
    INSERT INTO fts_search_query(rowid, query)
    VALUES (NEW.id, json_extract(NEW.payload, '$.tool_input.query'));
END;

CREATE TRIGGER trg_fts_search_query_delete
AFTER DELETE ON tool_events
WHEN OLD.tool_name = 'WebSearch'
BEGIN
    DELETE FROM fts_search_query WHERE rowid = OLD.id;
END;

CREATE TRIGGER trg_fts_search_query_update
AFTER UPDATE OF payload ON tool_events
WHEN (OLD.tool_name = 'WebSearch') OR (NEW.tool_name = 'WebSearch')
BEGIN
    DELETE FROM fts_search_query WHERE rowid = OLD.id AND OLD.tool_name = 'WebSearch';
    INSERT INTO fts_search_query(rowid, query)
    SELECT NEW.id, json_extract(NEW.payload, '$.tool_input.query') WHERE NEW.tool_name = 'WebSearch';
END;
