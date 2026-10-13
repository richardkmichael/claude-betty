---
name: tool-usage
description: >
  Query the tool-monitor database to look up and analyze Claude tool usage.
  Use when asked about tool usage statistics, which tools have been used most, recent
  tool activity, bash commands run, files read or edited, grep patterns searched,
  session analysis, or any question about how Claude has been using its tools.
allowed-tools:
  - Bash(sqlite3 -safe -readonly *)
  - Bash(test -f ~/.claude/tool-monitor.sqlite)
---

# Tool Usage

The tool-monitor database records every Claude Code tool invocation.

Database: `~/.claude/tool-monitor.sqlite`

## tool_events table

| Column            | Type     | Notes                                                        |
|-------------------|----------|--------------------------------------------------------------|
| `id`              | INTEGER  | Primary key                                                  |
| `session_id`      | TEXT     | Groups events by conversation session                        |
| `hook_event_name` | TEXT     | `PreToolUse`, `PostToolUse`, `PostToolUseFailure`,           |
|                   |          | or `PermissionRequest`                                       |
| `tool_name`       | TEXT     | e.g. `Bash`, `Read`, `Edit`, `Agent`, or an MCP tool         |
| `cwd`             | TEXT     | Working directory at time of invocation                      |
| `transcript_path` | TEXT     | Path to the session transcript file                          |
| `created_at`      | DATETIME | Timestamp, in UTC                                            |
| `payload`         | TEXT     | Full hook JSON: the fields above, plus `tool_input` and more |

Each tool call records one `PreToolUse` event, then one `PostToolUse` or `PostToolUseFailure`
event, and a `PermissionRequest` event when it prompted.  Filter on
`hook_event_name = 'PreToolUse'` to count each call once.  `PostToolUse` payloads also carry
`tool_response`, which holds the result, such as an Edit's `oldString`, `newString` and
`originalFile`.

Tool-specific input fields live inside `payload` and are read with `json_extract`:

```sql
json_extract(payload, '$.tool_input.command')    -- Bash: the shell command
json_extract(payload, '$.tool_input.file_path')  -- Read/Write/Edit: file path
json_extract(payload, '$.tool_input.pattern')    -- Grep/Glob: search pattern
json_extract(payload, '$.tool_input.old_string') -- Edit: text being replaced
```

## Discovering fields for any tool

The `tool_schemas` table holds a JSON Schema for a fixed set of older core tools.  Its tool names
are not always capitalized as recorded (`Webfetch`, not `WebFetch`), and newer tools such as
`Agent`, `Skill`, `ToolSearch` and MCP tools have no entry.  Match names case-insensitively:

```sql
SELECT json_extract(schema_json, '$.properties.tool_input.properties')
  FROM tool_schemas
 WHERE tool_name = 'WebFetch' COLLATE NOCASE
   AND hook_event = 'PreToolUse';
```

For any tool without a schema, read the keys of its most recent payload:

```sql
SELECT json_extract(payload, '$.tool_input')
  FROM tool_events
 WHERE tool_name = 'Skill' AND hook_event_name = 'PreToolUse'
 ORDER BY id DESC
 LIMIT 3;
```

## FTS5 substring search

For substring searches, the FTS5 tables are much faster than `LIKE '%term%'` on `tool_events`,
which scans every payload.

Query `tool_schemas.indexed_fields` to find which fields have FTS coverage:

```sql
SELECT tool_name, indexed_fields
  FROM tool_schemas
 WHERE hook_event = 'PreToolUse'
   AND indexed_fields != '[]'
 ORDER BY tool_name;
```

Each `indexed_fields` value is a JSON array of `{"path", "fts_table", "fts_column"}` objects.
Use `fts_table` and `fts_column` from those results to build the JOIN:

```sql
SELECT e.created_at, json_extract(e.payload, '$.tool_input.command')
  FROM tool_events e
  JOIN fts_bash_command f ON f.event_id = e.id
 WHERE f.command MATCH '"git commit"'
   AND e.hook_event_name = 'PreToolUse'
 ORDER BY e.created_at DESC;
```

The FTS tables index every hook event, not only `PreToolUse`.  Without the
`hook_event_name` filter, each call matches two or three times.

The FTS tables use the trigram tokenizer:

- A `MATCH` term must be at least 3 characters.  A shorter term matches nothing, without error.
  For a shorter term, use `LIKE` on the FTS column, e.g. `f.command LIKE '%gh %'`, which still
  uses the index.
- Special characters (`.`, `-`, `/`) require phrase quoting: `MATCH '"CLAUDE.md"'`, not
  `MATCH 'CLAUDE.md'`.

Use `json_extract` with `=` for exact equality, which uses the expression indexes.

## Running queries

Before querying, verify the database exists:

```bash
test -f ~/.claude/tool-monitor.sqlite
```

If it does not, tool-monitor is not installed.

Always open the database with `-safe -readonly`.  This prevents writes and disables sqlite3
dot-commands that run shell commands:

```bash
sqlite3 -safe -readonly -column -header ~/.claude/tool-monitor.sqlite "<SQL>"
```

## Two-step workflow for tool-specific queries

When the user asks about a specific tool's inputs (e.g. "which files have I read?", "what bash
commands containing git?"), first look up the field names, as described in "Discovering fields
for any tool", then query:

```sql
SELECT json_extract(payload, '$.tool_input.file_path'), created_at
  FROM tool_events
 WHERE tool_name = 'Read' AND hook_event_name = 'PreToolUse'
 ORDER BY created_at DESC
 LIMIT 20;
```

## Common patterns

Filter Bash commands by text, through FTS:

```sql
JOIN fts_bash_command f ON f.event_id = e.id
WHERE f.command MATCH '"git"' AND e.hook_event_name = 'PreToolUse'
```

Scope to a directory:

```sql
WHERE cwd LIKE '/path/to/my-project%'
```

Scope to the current session:

```sql
WHERE session_id = '${CLAUDE_SESSION_ID}'
```

Activity today, in local time.  `created_at` is in UTC, so convert the local start of day to UTC
rather than converting each row, which would bypass the index:

```sql
WHERE created_at >= datetime(date('now', 'localtime'), 'utc')
```

Tool frequency summary:

```sql
SELECT tool_name, COUNT(*) AS uses
  FROM tool_events
 WHERE hook_event_name = 'PreToolUse'
 GROUP BY tool_name
 ORDER BY uses DESC;
```

The `tool_name` column holds the tool that triggered the hook.  The `matcher` from the hook
configuration (e.g. `"*"`, `"Bash"`) is not recorded.
