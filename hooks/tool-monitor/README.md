# Tool Monitor

This project implements four Claude Code hooks — `PreToolUse`, `PostToolUse`, `PostToolUseFailure`,
and `PermissionRequest` — to store hook payloads in SQLite for future analysis of Claude's tool usage.

The hooks do not *alter* tool calls; they return exit code 0 (success) transparently and only capture
the hook payload.  In transcript mode (CTRL-R), `stdout` from the hook is shown to the user.

## Quick Start

### New Installations

Build the release binary and link it onto your `PATH` as `tool-monitor`:

```bash
cargo build --release
ln -s "$PWD/target/release/claude-tool-monitor" ~/bin/tool-monitor
```

The hooks run the binary through the link, so a later `cargo build --release` updates them in place.

Run `claude` and configure `/hooks`, or directly edit `.claude/settings.json`:

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "*",
        "hooks": [{"type": "command", "command": "tool-monitor ~/.claude/tool-monitor.sqlite"}]
      }
    ],
    "PostToolUse": [
      {
        "matcher": "*",
        "hooks": [{"type": "command", "command": "tool-monitor ~/.claude/tool-monitor.sqlite"}]
      }
    ],
    "PostToolUseFailure": [
      {
        "matcher": "*",
        "hooks": [{"type": "command", "command": "tool-monitor ~/.claude/tool-monitor.sqlite"}]
      }
    ],
    "PermissionRequest": [
      {
        "matcher": "*",
        "hooks": [{"type": "command", "command": "tool-monitor ~/.claude/tool-monitor.sqlite"}]
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

### `tools/maintenance/archive_events.py`
Moves old events out of the live database into a compressed SQLite archive, and shrinks the live
database.  Run it whenever the database has grown larger than you want.  It keeps the last 30 days
by default.

Close every Claude Code session first; the script refuses to run while any is open.

```bash
# Keep the last 30 days; the archive goes next to the database
python tools/maintenance/archive_events.py ~/.claude/tool-monitor.sqlite

# Archive everything before a date
python tools/maintenance/archive_events.py --before YYYY-MM-DD ~/.claude/tool-monitor.sqlite

# Keep 60 days, and write the archive to another volume
python tools/maintenance/archive_events.py --days 60 --archive-dir /Volumes/backup \
    ~/.claude/tool-monitor.sqlite
```

The archive is named for the dates it covers, e.g. `tool-monitor-archive-<first>_<last>.sqlite.zst`.
It holds `tool_events` (`id`, `session_id`, `payload`, `created_at`) and an `archive_info` table
recording the source database, its schema version and the archived range.

Steps, each timed in the output:

1. Copy the old events to the archive, and verify the copy before deleting anything.
2. Delete the events in one statement.  The FTS delete triggers remove their search entries.
3. Fully merge the FTS indexes, dropping the deletion markers the deletes left.
4. Compact: write the remaining data to a new file with `VACUUM INTO`, verify it, and swap it in.
   The previous file is kept as `tool-monitor-old.sqlite`.
5. Compress the archive with `zstd`, check it decompresses to the same bytes, and remove the
   uncompressed copy.

An interrupted run resumes where it stopped when rerun.  When the run finishes, start Claude Code,
make a tool call, confirm the hook wrote to the database (the script prints the query), then delete
`tool-monitor-old.sqlite`.

`--online` archives while Claude Code is in use.  Every write is a short transaction: events are
deleted in batches, the FTS indexes are merged in bounded steps, and freed pages are returned to the
disk with bounded `PRAGMA incremental_vacuum` calls.  It is much slower than compacting offline, and
it needs `auto_vacuum=INCREMENTAL`.  With other sessions running, add `--pause` to give their hooks
more room on the lock.

Requirements:
- Schema 3.1 or later, for the FTS delete triggers.
- Free space for the archive and, offline, for the compacted file.  The script checks both.
- `zstd` on `PATH`.

To query an archive, decompress it and attach it:

```bash
zstd -d tool-monitor-archive-<first>_<last>.sqlite.zst
sqlite3 -readonly tool-monitor-archive-<first>_<last>.sqlite \
    "SELECT created_at, json_extract(payload, '$.tool_input.command') FROM tool_events
      WHERE json_extract(payload, '$.tool_name') = 'Bash' LIMIT 10;"
```

To restore archived events into the live database, insert them back; the insert triggers rebuild
their FTS entries, and the ids never collide with newer events:

```sql
ATTACH 'tool-monitor-archive-<first>_<last>.sqlite' AS archive;
INSERT INTO tool_events (id, session_id, payload, created_at)
SELECT id, session_id, payload, created_at FROM archive.tool_events;
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
