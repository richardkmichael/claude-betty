---
name: tool-usage
description: >
  Query the tool-monitor database to look up and analyze Claude tool usage.
  Use when asked about tool usage statistics, which tools have been used most, recent
  tool activity, bash commands run, files read or edited, searches run, tool durations,
  subagent activity, session analysis, or any question about how Claude has been using its
  tools.  For what was said in a conversation, use the history skill instead.
allowed-tools:
  - Bash(sqlite3 -safe -readonly *)
  - Bash(test -f ~/.claude/tool-monitor.sqlite)
---

# Tool Usage

The tool-monitor hook records every Claude Code tool call, plus session, subagent and failure
events, in a SQLite database at `~/.claude/tool-monitor.sqlite`.

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

The database holds gigabytes of payloads.  Two habits keep queries fast and their output small:

- Select fields, not whole payloads.  A `PostToolUse` payload holds the tool's full result, such as
  a Read's whole file or a Bash call's whole output, often tens or hundreds of kilobytes.  Extract
  the fields the question needs with `json_extract`, or drop the result with
  `json_remove(payload, '$.tool_response')`, and add a `LIMIT`.
- Filter on indexed columns first.  `tool_name`, `hook_event_name`, `session_id`, `cwd` and
  `created_at` are indexed.  A `json_extract` or `LIKE` over every payload of a common tool reads a
  large part of the file and takes seconds.

For analysis that SQL cannot express, such as splitting shell commands into the programs they run,
read the database from Python with `sqlite3.connect('file:<path>?mode=ro', uri=True)`.  The skill's
allowed tools do not cover that, so it asks for permission.

## tool_events table

| Column            | Type     | Notes                                                        |
| ----------------- | -------- | ------------------------------------------------------------ |
| `id`              | INTEGER  | Primary key                                                  |
| `session_id`      | TEXT     | Groups events by conversation session                        |
| `hook_event_name` | TEXT     | The hook event; see "Events recorded" below                  |
| `tool_name`       | TEXT     | e.g. `Bash`, `Read`, `Agent`; NULL for events without a tool |
| `cwd`             | TEXT     | Working directory at time of invocation                      |
| `transcript_path` | TEXT     | Path to the session transcript file                          |
| `created_at`      | DATETIME | Timestamp, in UTC, to the second                             |
| `payload`         | TEXT     | Full hook JSON: the fields above, plus `tool_input` and more |

All columns except `id`, `session_id`, `payload` and `created_at` are generated from `payload`.

## Events recorded

| Event                           | Records                                                        |
| ------------------------------- | -------------------------------------------------------------- |
| `PreToolUse`, `PostToolUse`     | Each tool call and its result                                  |
| `PostToolUseFailure`            | A tool call that failed                                        |
| `PermissionRequest`             | A tool call that prompted for permission                       |
| `PermissionDenied`              | A tool call that auto mode denied                              |
| `SessionStart`, `SessionEnd`    | Session boundaries; `$.source` and `$.reason` record the cause |
| `SubagentStart`, `SubagentStop` | Subagent lifetimes, keyed by `$.agent_id`                      |
| `StopFailure`                   | A turn that ended on an API error                              |

Each tool call records one `PreToolUse` event, usually followed by one `PostToolUse` or
`PostToolUseFailure` event, and a `PermissionRequest` event when it prompted.  Filter on
`hook_event_name = 'PreToolUse'` to count each call once.  About 1% of calls have no closing event:
neither post event fires when a call is cancelled, denied or rejected by validation.  To pair the
events of one call, join on `$.tool_use_id`.  `PermissionRequest` payloads do not carry it.

The session, subagent and failure events were added to the hook configuration later than the tool
events, so the database holds them only from that point on.  Except for `PermissionDenied`, their
rows have no `tool_name`.

The `matcher` from the hook configuration (e.g. `"*"`, `"Bash"`) is not recorded.

## Payload fields

Tool-specific fields live inside `payload` and are read with `json_extract`:

```sql
json_extract(payload, '$.tool_input.command')    -- Bash: the shell command
json_extract(payload, '$.tool_input.file_path')  -- Read/Write/Edit: file path
json_extract(payload, '$.tool_input.pattern')    -- Grep/Glob: search pattern
json_extract(payload, '$.tool_input.old_string') -- Edit: text being replaced
```

`PostToolUse` payloads also carry `tool_response`, which holds the result, such as an Edit's
`oldString`, `newString` and `originalFile`, or a Bash call's `stdout`, `stderr` and `interrupted`.

Payloads also carry these top-level fields:

| Field             | Events             | Notes                                                |
| ----------------- | ------------------ | ---------------------------------------------------- |
| `tool_use_id`     | Pre, Post, Failure | Same value on the events of one call                 |
| `duration_ms`     | Post, Failure      | Execution time of the tool call                      |
| `agent_id`        | all                | Present only for calls made inside a subagent        |
| `agent_type`      | all                | Subagent type, or the main session's `--agent` agent |
| `prompt_id`       | all                | Groups the calls made in response to one user prompt |
| `permission_mode` | all                | e.g. `default`, `acceptEdits`, `plan`, `auto`        |
| `effort`          | all                | `{"level": ...}`, the reasoning effort in force      |
| `mcp_server`      | MCP tools          | `{"name", "source"}`                                 |
| `error`           | Failure            | The failure message                                  |
| `is_interrupt`    | Failure            | True when the user interrupted the call              |

MCP tools are named `mcp__<server>__<tool>`.

## Discovering fields for any tool

For a tool not covered above, read its most recent inputs:

```sql
SELECT json_extract(payload, '$.tool_input')
  FROM tool_events
 WHERE tool_name = 'Skill' AND hook_event_name = 'PreToolUse'
 ORDER BY id DESC
 LIMIT 3;
```

For its results, select `json_extract(payload, '$.tool_response')` from `PostToolUse` rows the same
way, with `substr` around it when the results may be large.

The `tool_schemas` table also holds a JSON Schema for some older core tools.  It is a snapshot, and
is incomplete: newer tools such as `Agent`, `Skill`, `ToolSearch` and MCP tools have no entry, it
holds tools Claude Code no longer has (`Ls`, `Multiedit`, `Notebookread`, `Todowrite`), and its
`Task` is the tool now recorded as `Agent`.  Its names are not always capitalized as recorded
(`Webfetch`, not `WebFetch`), so match them case-insensitively:

```sql
SELECT json_extract(schema_json, '$.properties.tool_input.properties')
  FROM tool_schemas
 WHERE tool_name = 'WebFetch' COLLATE NOCASE
   AND hook_event = 'PreToolUse';
```

## Searching

### Exact values: expression indexes

Use `json_extract` with `=` for exact equality, which uses the expression indexes.  These are
partial indexes, so the query must repeat the index's `tool_name` condition exactly:

| Expression                         | Required condition                           |
| ---------------------------------- | -------------------------------------------- |
| `$.tool_input.command`             | `tool_name = 'Bash'`                         |
| `$.tool_input.file_path`           | `tool_name IN ('Read', 'Write', 'Edit')`     |
| `$.tool_input.pattern` (Grep/Glob) | `tool_name = 'Grep'` or `tool_name = 'Glob'` |

`tool_name = 'Read'` alone does not match `IN ('Read', 'Write', 'Edit')`, so SQLite falls back to
`idx_tool_name` and reads every Read payload.  To narrow to one of the three tools, keep the `IN`
condition and add a second one on `json_extract(payload, '$.tool_name')`.

### Substrings: FTS5 tables

For substring searches, the FTS5 tables are much faster than `LIKE '%term%'` on `tool_events`, which
scans every payload:

| Table              | Column      | Tools             |
| ------------------ | ----------- | ----------------- |
| `fts_bash_command` | `command`   | Bash              |
| `fts_file_path`    | `file_path` | Read, Write, Edit |
| `fts_grep_pattern` | `pattern`   | Grep              |
| `fts_glob_pattern` | `pattern`   | Glob              |
| `fts_url`          | `url`       | WebFetch          |
| `fts_search_query` | `query`     | WebSearch         |

An FTS row's `rowid` is the `tool_events.id` of the event it indexes.  The FTS tables index every
hook event, so filter on `hook_event_name` to match each call once:

```sql
SELECT e.created_at, json_extract(e.payload, '$.tool_input.command')
  FROM tool_events e
  JOIN fts_bash_command f ON f.rowid = e.id
 WHERE f.command MATCH '"git commit"'
   AND e.hook_event_name = 'PreToolUse'
 ORDER BY e.id DESC
 LIMIT 20;
```

The FTS tables use the trigram tokenizer:

- A `MATCH` term must be at least 3 characters.  A shorter term matches nothing, without error.
  For a shorter term, use `LIKE` on the FTS column, e.g. `f.command LIKE '%gh %'`, which still
  uses the index.
- Special characters (`.`, `-`, `/`) require phrase quoting: `MATCH '"CLAUDE.md"'`, not
  `MATCH 'CLAUDE.md'`.

### Searches and reads run through Bash

The Grep and Glob tools are rarely used.  Searches run as `grep`, `rg` and `find` inside Bash
commands, so look for them in `fts_bash_command`, not under `tool_name = 'Grep'`:

```sql
WHERE (f.command MATCH '"grep "' OR f.command MATCH '"rg "')
  AND e.hook_event_name = 'PreToolUse'
```

Likewise, files are often read with `cat`, `sed -n` or `head` in Bash rather than with the Read
tool, so a question about which files were read needs both sources.

## Common filters

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

## Summaries

Tool frequency:

```sql
SELECT tool_name, COUNT(*) AS calls
  FROM tool_events
 WHERE hook_event_name = 'PreToolUse'
 GROUP BY tool_name
 ORDER BY calls DESC;
```

Tool durations.  The sqlite3 shell has `median()` and `percentile()`:

```sql
SELECT tool_name, COUNT(*) AS calls,
       median(json_extract(payload, '$.duration_ms')) AS p50_ms,
       round(percentile(json_extract(payload, '$.duration_ms'), 90)) AS p90_ms
  FROM tool_events
 WHERE hook_event_name = 'PostToolUse'
   AND created_at >= datetime('now', '-7 days')
 GROUP BY tool_name
 ORDER BY calls DESC;
```

Files edited most in a project over the last week:

```sql
SELECT json_extract(payload, '$.tool_input.file_path') AS file, COUNT(*) AS edits
  FROM tool_events
 WHERE tool_name IN ('Read', 'Write', 'Edit')
   AND json_extract(payload, '$.tool_name') IN ('Edit', 'Write')
   AND hook_event_name = 'PreToolUse'
   AND cwd LIKE '/path/to/my-project%'
   AND created_at >= datetime('now', '-7 days')
 GROUP BY file
 ORDER BY edits DESC
 LIMIT 20;
```

Subagent runs with their tool-call counts.  Tool calls made inside a subagent carry its `agent_id`
and the parent's `session_id`.  `SubagentStop` also fires for Claude Code's internal agents, which
have an empty `agent_type`:

```sql
SELECT json_extract(stop.payload, '$.agent_type') AS agent_type,
       stop.created_at AS stopped_at,
       (SELECT COUNT(*)
          FROM tool_events t
         WHERE t.session_id = stop.session_id
           AND t.hook_event_name = 'PreToolUse'
           AND json_extract(t.payload, '$.agent_id') = json_extract(stop.payload, '$.agent_id')
       ) AS tool_calls
  FROM tool_events stop
 WHERE stop.hook_event_name = 'SubagentStop'
   AND json_extract(stop.payload, '$.agent_type') <> ''
 ORDER BY stop.id DESC
 LIMIT 20;
```

## Archived events

Older events may have been moved out of the live database into compressed archives named
`~/.claude/tool-monitor-archive-<first-date>_<last-date>.sqlite.zst`.  The dates in the name give
the period each archive covers.  Check `SELECT min(created_at) FROM tool_events` first.  Query an
archive only when the question needs events before that date.  When the answer covers only the live
database, say so.

SQLite cannot read a `.zst` file in place.  To query an archive, decompress it into the session's
scratchpad directory, query it there with the same `-safe -readonly` flags, and delete the copy when
done.  An archive expands to several gigabytes, so check free space first.  Archives may use an
older schema than the live database, so check its tables before reusing a query.
