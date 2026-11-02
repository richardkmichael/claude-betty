# Tool Monitor

This project implements four Claude Code hooks — `PreToolUse`, `PostToolUse`, `PostToolUseFailure`,
and `PermissionRequest` — to store hook payloads in SQLite for future analysis of Claude's tool usage.

The hooks do not *alter* tool calls; they return exit code 0 (success) transparently and only capture
the hook payload.  In transcript mode (CTRL-R), `stdout` from the hook is shown to the user.

## Quick Start

### New Installations

Install to `~/bin/claude-tool-monitor`:

```bash
cargo install --path . --root ~
```

Run `claude` and configure `/hooks`, or directly edit `.claude/settings.json`:

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "*",
        "hooks": [{"type": "command", "command": "claude-tool-monitor ~/.claude/tool-monitor.sqlite"}]
      }
    ],
    "PostToolUse": [
      {
        "matcher": "*",
        "hooks": [{"type": "command", "command": "claude-tool-monitor ~/.claude/tool-monitor.sqlite"}]
      }
    ],
    "PostToolUseFailure": [
      {
        "matcher": "*",
        "hooks": [{"type": "command", "command": "claude-tool-monitor ~/.claude/tool-monitor.sqlite"}]
      }
    ],
    "PermissionRequest": [
      {
        "matcher": "*",
        "hooks": [{"type": "command", "command": "claude-tool-monitor ~/.claude/tool-monitor.sqlite"}]
      }
    ]
  }
}
```

### Update Installations
```bash
# Migrate V1 → V2 if needed
python tools/migration/migrate_to_v2.py your_existing_monitor.db

# Migrate V2 → V3 (adds FTS5 search, expression indexes, expanded hook coverage)
python tools/migration/migrate_to_v3.py your_existing_monitor.db

# Migrate V3.0 → V3.1 (FTS5 tables keyed by event id, delete and update triggers)
python tools/migration/migrate_to_v3_1.py your_existing_monitor.db

# Validate schemas work correctly
python tools/validation/validate_against_schemas.py your_existing_monitor.db
```

## Features

- Schema V3: FTS5 trigram search, expression indexes, four hook events
- Fast Rust binary: processes tool events with excellent performance
- SQLite storage: WAL mode for concurrent access, generated columns for stable fields
- JSON Schema validation: industry-standard validation using `uvx check-jsonschema`
- Migration tools: safe V1→V2 and V2→V3 migration with automatic backups


## Database Schema

### Schema V3 Design
- `schema_info`: database schema version, metadata, and hook_common_fields list
- `tool_events`: minimal table with stable hook fields + full JSON payload
- `tool_schemas`: JSON Schema documents for each tool/hook combination (including PostToolUseFailure and PermissionRequest)
- `schema_versions`: tool schema evolution tracking
- FTS5 virtual tables: fast wildcard/substring search over key tool_input fields
- Expression indexes: fast equality lookups on tool_input fields

### Generated Columns (Stable Hook Contract Fields)
- `hook_event_name` - PreToolUse/PostToolUse/PostToolUseFailure/PermissionRequest
- `tool_name` - name of the tool (Bash, Edit, etc.)
- `cwd` - current working directory
- `transcript_path` - path to session transcript

### FTS5 Tables (Wildcard Search)
Use `JOIN fts_bash_command f ON f.rowid = tool_events.id WHERE f.command MATCH '"term"'`

Each FTS table's `rowid` is the `tool_events.id` of the event it indexes.  Triggers keep the FTS
tables in step with `tool_events` on insert, delete and update.

| Table | Field | Tools |
|---|---|---|
| `fts_bash_command` | command | Bash |
| `fts_file_path` | file_path | Read, Write, Edit |
| `fts_grep_pattern` | pattern | Grep |
| `fts_glob_pattern` | pattern | Glob |
| `fts_url` | url | WebFetch |
| `fts_search_query` | query | WebSearch |

Note: special characters (`.`, `-`, `/`) require phrase quoting: `MATCH '"term.ext"'`

## JSON Schema Files

The `hook_schemas/` directory contains JSON Schema files for tool validation:
- Naming: `{toolname}-{hook_event}.json` (e.g., `bash-pre_tool_use.json`)
- Coverage: 14 tools × 2 hook events (PreToolUse + PostToolUse)
- Additional PostToolUseFailure and PermissionRequest schemas stored in `tool_schemas` table

## Tools

### `tools/migration/migrate_to_v2.py`
Migrates V1 databases to V2 schema. Creates backups automatically.

```bash
python tools/migration/migrate_to_v2.py monitor.db
```

### `tools/migration/migrate_to_v3.py`
Migrates V2 databases to V3 schema. Creates backups automatically.

```bash
python tools/migration/migrate_to_v3.py monitor.db
```

### `tools/migration/migrate_to_v3_1.py`
Migrates V3.0 databases to V3.1: FTS5 tables keyed by `tool_events.id`, with delete and update
triggers.  Runs while the hook is writing, in short transactions with pauses between them, and
resumes where it stopped if interrupted.

```bash
python tools/migration/migrate_to_v3_1.py monitor.db
```

### `tools/validation/validate_against_schemas.py`
Validates database payloads against JSON schemas using `uvx check-jsonschema`.

```bash
# Validate all tools
python tools/validation/validate_against_schemas.py monitor.db

# Validate specific tool
python tools/validation/validate_against_schemas.py monitor.db Bash PreToolUse
```

### `tools/validation/derive_schemas_from_payloads.py`
Analyzes actual payloads to derive JSON schema structures.

```bash
python tools/validation/derive_schemas_from_payloads.py monitor.db
python tools/validation/derive_schemas_from_payloads.py monitor.db --tool Bash
python tools/validation/derive_schemas_from_payloads.py monitor.db --compare
```

### `tools/validation/check_schema_version.py`
Checks database schema version and compatibility.

```bash
python tools/validation/check_schema_version.py monitor.db
```

## Building

```bash
cargo build
cargo build --release
cargo test
```

## Example Queries

```sql
-- Tool usage statistics
SELECT tool_name, COUNT(*)
  FROM tool_events
 GROUP BY tool_name
 ORDER BY COUNT(*) DESC;

-- Bash commands containing a pattern (FTS5 wildcard search)
SELECT e.created_at, json_extract(e.payload, '$.tool_input.command')
  FROM tool_events e
  JOIN fts_bash_command f ON f.rowid = e.id
 WHERE f.command MATCH '"git"'
 ORDER BY e.created_at DESC;

-- Files accessed today (equality lookup via expression index)
SELECT json_extract(payload, '$.tool_input.file_path'), created_at
  FROM tool_events
 WHERE tool_name IN ('Read', 'Write', 'Edit')
   AND date(created_at) = date('now');

-- Available schemas
SELECT tool_name, hook_event, category
  FROM tool_schemas
 ORDER BY category, tool_name;
```

## Dependencies

- Runtime: none (SQLite bundled)
- Validation: `uvx` (for JSON Schema validation)
- Migration: Python 3.7+
