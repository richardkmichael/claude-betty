# Tool Monitor

This project implements a Claude Code hook that stores hook payloads in SQLite for later analysis of
Claude's tool usage.  It is configured for ten hook events:

- Tool events: `PreToolUse`, `PostToolUse`, `PostToolUseFailure` and `PermissionRequest`
- Session, subagent and permission context: `SessionStart`, `SessionEnd`, `SubagentStart`,
  `SubagentStop`, `PermissionDenied` and `StopFailure`

The hook does not *alter* tool calls or sessions.  It writes nothing to stdout and exits 0 once the
payload is stored, so Claude Code proceeds as if no hook ran.  When the payload cannot be stored, it
exits 1 with the error on stderr, which Claude Code reports as a non-blocking hook error.

## Quick Start

### New Installations

Build the release binary and link it onto your `PATH` as `tool-monitor`:

```bash
cargo build --release
ln -s "$PWD/target/release/claude-tool-monitor" ~/bin/tool-monitor
```

The hooks run the binary through the link, so a later `cargo build --release` updates them in place.

Run `claude` and configure `/hooks`, or directly edit `.claude/settings.json`.  Every event runs the
same command:

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
    ],
    "SessionStart": [
      {"hooks": [{"type": "command", "command": "tool-monitor ~/.claude/tool-monitor.sqlite"}]}
    ],
    "SessionEnd": [
      {"hooks": [{"type": "command", "command": "tool-monitor ~/.claude/tool-monitor.sqlite"}]}
    ],
    "SubagentStart": [
      {"hooks": [{"type": "command", "command": "tool-monitor ~/.claude/tool-monitor.sqlite"}]}
    ],
    "SubagentStop": [
      {"hooks": [{"type": "command", "command": "tool-monitor ~/.claude/tool-monitor.sqlite"}]}
    ],
    "PermissionDenied": [
      {"hooks": [{"type": "command", "command": "tool-monitor ~/.claude/tool-monitor.sqlite"}]}
    ],
    "StopFailure": [
      {"hooks": [{"type": "command", "command": "tool-monitor ~/.claude/tool-monitor.sqlite"}]}
    ]
  }
}
```

An omitted matcher matches every occurrence of the event, as `"*"` does.

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

Adding hook events needs no migration.  Their rows go into `tool_events` like any other payload.

## Features

- Schema V3: FTS5 trigram search, expression indexes, one table for every hook event
- Fast Rust binary: processes hook events with excellent performance
- SQLite storage: WAL mode for concurrent access, generated columns for stable fields
- JSON Schema validation: industry-standard validation using `uvx check-jsonschema`
- Migration tools: safe V1→V2, V2→V3 and V3.0→V3.1 migration, with automatic backups or resumable
  steps
- Archiving: moves old events to a compressed SQLite archive and shrinks the live database

## Database Schema

### Schema V3 Design
- `schema_info`: database schema version, metadata, and hook_common_fields list
- `tool_events`: minimal table with stable hook fields + full JSON payload
- `tool_schemas`: JSON Schema documents for each tool/hook combination, loaded by the migration
  tools.  It is a snapshot, and does not follow later changes to `hook_schemas/`.
- `schema_versions`: tool schema evolution tracking
- FTS5 virtual tables: fast wildcard/substring search over key tool_input fields
- Expression indexes: fast equality lookups on tool_input fields

### Generated Columns (Stable Hook Contract Fields)
- `hook_event_name`: the hook event, such as `PreToolUse` or `SubagentStop`
- `tool_name`: name of the tool (Bash, Edit, etc.)
- `cwd`: current working directory
- `transcript_path`: path to the main session's transcript

`tool_name` is NULL for rows from `SessionStart`, `SessionEnd`, `SubagentStart`, `SubagentStop` and
`StopFailure`, which are not about a tool call.  `PermissionDenied` rows have a `tool_name`.  Filter
on `hook_event_name` to separate tool calls from the other events.

### Subagents

A tool call made inside a subagent has the subagent's `agent_id` and `agent_type` in its payload,
and the parent session's `session_id`.  `SubagentStart` and `SubagentStop` rows carry the same
`agent_id`, so a subagent's lifetime joins to its tool rows by `agent_id`.  `SubagentStop` also
carries `agent_transcript_path`, the subagent's own transcript, and `last_assistant_message`, the
text of its final response.  The `Agent` tool's `PostToolUse` row records the same identifier as
`tool_response.agentId`.

`SubagentStop` also fires for agents Claude Code runs for its own features, such as prompt
suggestions.  Those rows have an empty `agent_type` and no tool rows.

### FTS5 Tables (Wildcard Search)
Use `JOIN fts_bash_command f ON f.rowid = tool_events.id WHERE f.command MATCH '"term"'`

Each FTS table's `rowid` is the `tool_events.id` of the event it indexes.  Triggers keep the FTS
tables in step with `tool_events` on insert, delete and update.

| Table              | Field     | Tools             |
| ------------------ | --------- | ----------------- |
| `fts_bash_command` | command   | Bash              |
| `fts_file_path`    | file_path | Read, Write, Edit |
| `fts_grep_pattern` | pattern   | Grep              |
| `fts_glob_pattern` | pattern   | Glob              |
| `fts_url`          | url       | WebFetch          |
| `fts_search_query` | query     | WebSearch         |

Note: special characters (`.`, `-`, `/`) require phrase quoting: `MATCH '"term.ext"'`

## JSON Schema Files

The `hook_schemas/` directory contains JSON Schema files for tool validation:
- Naming: `{toolname}-{hook_event}.json`, with `hook_event` one of `pre_tool_use`, `post_tool_use`,
  `post_tool_use_failure` and `permission_request` (e.g., `bash-pre_tool_use.json`)
- Coverage: the core built-in tools, each for all four tool events.  `index.json` lists them.
- Obsolete tools (`Task`, `TodoWrite`, `MultiEdit`, `NotebookRead`, `LS`) keep their files, marked
  obsolete, for older payloads.
- MCP tools and other built-in tools have no schema files.

`HOOK_SCHEMAS.md` describes the fields, and `HOOK_ARCHITECTURE.md` describes how events are named
and paired.

## Tools

### `tools/migration/migrate_to_v2.py`
Migrates V1 databases to V2 schema.  Creates backups automatically.

```bash
python tools/migration/migrate_to_v2.py monitor.db
```

### `tools/migration/migrate_to_v3.py`
Migrates V2 databases to V3 schema.  Creates backups automatically.

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

Close every Claude Code session first; the script refuses to run while any is open, or while any
process holds the database open.

```bash
# Keep the last 30 days; the archive goes next to the database
python tools/maintenance/archive_events.py ~/.claude/tool-monitor.sqlite

# Archive everything before a date (UTC)
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

Other options:

- `--ignore-sessions`: skip the check for running Claude Code sessions and open handles, for a
  database the hook does not write to, such as a copy
- `--cache-mb N`: the SQLite page cache in MB (default: 1024)

Requirements:
- The FTS delete triggers.  The script checks for them, not for a schema version, and stops with a
  pointer to `migrate_to_v3_1.py` when they are missing.
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
Validates database payloads against JSON schemas using `uvx check-jsonschema`.  For each tool and
tool event in the database, it validates the most recent payload against the matching file in
`hook_schemas/`, relative to the current directory, so run it from this directory.  A tool with no
schema file counts as a failure.

```bash
# Validate all tools
python tools/validation/validate_against_schemas.py monitor.db

# Validate specific tool
python tools/validation/validate_against_schemas.py monitor.db Bash PreToolUse
```

### `tools/validation/derive_schemas_from_payloads.py`
Analyzes actual payloads and reports, for each tool and hook event, the top-level fields with their
types and how often they appear.  It prints the analysis and does not write schema files.

```bash
python tools/validation/derive_schemas_from_payloads.py monitor.db
python tools/validation/derive_schemas_from_payloads.py monitor.db --tool Bash
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
-- Tool usage statistics: one PreToolUse row per tool call
SELECT tool_name, COUNT(*)
  FROM tool_events
 WHERE hook_event_name = 'PreToolUse'
 GROUP BY tool_name
 ORDER BY COUNT(*) DESC;

-- Bash commands containing a pattern (FTS5 wildcard search)
SELECT e.created_at, json_extract(e.payload, '$.tool_input.command')
  FROM tool_events e
  JOIN fts_bash_command f ON f.rowid = e.id
 WHERE f.command MATCH '"git"'
 ORDER BY e.created_at DESC;

-- Files accessed today, in local time.  created_at is UTC, so the start of the local day is
-- converted to UTC, and the comparison uses idx_created_at.
SELECT json_extract(payload, '$.tool_input.file_path'), created_at
  FROM tool_events
 WHERE tool_name IN ('Read', 'Write', 'Edit')
   AND hook_event_name = 'PreToolUse'
   AND created_at >= datetime(date('now', 'localtime'), 'utc');

-- Subagent runs: start, stop and tool calls, joined by agent_id
SELECT json_extract(stop.payload, '$.agent_type') AS agent_type,
       start.created_at AS started_at,
       stop.created_at AS stopped_at,
       (SELECT COUNT(*)
          FROM tool_events t
         WHERE t.session_id = stop.session_id
           AND t.hook_event_name = 'PreToolUse'
           AND json_extract(t.payload, '$.agent_id') = json_extract(stop.payload, '$.agent_id')
       ) AS tool_calls
  FROM tool_events stop
  LEFT JOIN tool_events start
    ON start.session_id = stop.session_id
   AND start.hook_event_name = 'SubagentStart'
   AND json_extract(start.payload, '$.agent_id') = json_extract(stop.payload, '$.agent_id')
 WHERE stop.hook_event_name = 'SubagentStop'
   AND json_extract(stop.payload, '$.agent_type') <> ''
 ORDER BY stop.id DESC
 LIMIT 20;

-- Available schemas
SELECT tool_name, hook_event, category
  FROM tool_schemas
 ORDER BY category, tool_name;
```

## Dependencies

- Runtime: none (SQLite bundled)
- Validation: `uvx` (for JSON Schema validation)
- Migration and maintenance: Python 3.9+
