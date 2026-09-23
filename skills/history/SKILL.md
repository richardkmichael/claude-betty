---
name: history
description: >
  Search and read past Claude Code session transcripts. Use to recall what was said or decided in an
  earlier session, find a prior discussion by topic, recover content from before a context
  compaction, pull up a specific session by name or ID, or investigate past sessions in detail by
  filtering on record type, tool, subtype, or field. Searches conversation prose by default;
  technical records (tool calls, results, system events) are reachable through filters. Typed as
  `/history [selector] <query>`, and usable by the model to investigate history on its own.
---

# Session history

## Invocation

`/history [selector] <query>` searches your conversation history and reports the matching records.

- `/history <query>` searches every session in the current project. This is the default and the
  common case: "we discussed X, what did we decide?"
- `/history <selector> <query>` searches one specific session. The `<selector>` is its name (set
  with /rename), its UUID, or a unique UUID prefix. The session may live in another project.

To tell a selector from query text, resolve the leading token with `find`. If it resolves to a
session, that token is the selector and the rest is the query. If not, the whole input is the query
against the current project. The only ambiguous case is a query whose first word is an exact session
name, and resolution is exact, so it is rare.

This dispatch is for interpreting a user's typed `/history` line. When the model invokes this skill
on its own to investigate history, it calls `history.py` directly with explicit flags (for example
`--tool Bash --query …` or `--type system --subtype api_error`) rather than going through the
dispatch.

## The driver

Everything goes through one script. The current session UUID is available as `${CLAUDE_SESSION_ID}`.
Pass it as `--session-id` so the driver can locate the current session and project.

```
python ${CLAUDE_SKILL_DIR}/scripts/history.py search [--query Q] [target] [filters] [output]
python ${CLAUDE_SKILL_DIR}/scripts/history.py fetch  --index N [N ...] [target]
python ${CLAUDE_SKILL_DIR}/scripts/history.py find   --name X | --uuid Y
```

### Verbs

- `search`: find records matching a query and/or filters. By default it searches the conversation
  prose: your messages and the assistant's text responses. It skips tool calls, tool results, and
  other technical records, which you reach with `--type`, `--tool`, or `--subtype`. Prints one line
  per match, prefixed with the session name and record index as `[<name>:<index>] <role>:`, then the
  full content, not truncated. Default target: every session in the current project.
- `fetch`: print records by index from one session. Use it to read the records around a hit for
  context, or records that a query did not match. Indices come from a `search` line.
- `find`: resolve a selector to a transcript path, used to classify the leading `/history` token. It
  prints the path. On multiple matches it uses the most recent; on no match it suggests close names.

### Targets (search and fetch)

| flag          | meaning                                                       |
| ------------- | ------------------------------------------------------------ |
| (none)        | search: every session in the current project (the default)   |
| `--name X`    | the session named X (any name it ever had via /rename)       |
| `--uuid Y`    | the session UUID Y (a unique prefix works)                   |
| `--file P`    | an explicit transcript path                                  |
| `--project N` | every session in the project whose directory name contains N |
| `--current`   | only the current session                                     |

Names resolve current-project-first, widening to all projects on a miss. The most recently modified
session wins a tie. Always pass `--session-id ${CLAUDE_SESSION_ID}` so the default project and
`--current` resolve correctly.

A bare search (no target) searches the current project. The current session contributes only its
out-of-context content, so the message that triggered the search is not echoed back. If the current
project yields no match, the search widens to every project automatically, newest first and capped,
so a forgotten session is found without naming it.

### Filters and output (search)

Filters: `--type` (user, assistant, system, ...), `--tool NAME` (`*` for any), `--subtype`,
`--before N`, `--after N`, `--limit N`. Any of `--type`, `--tool`, or `--subtype` switches off the
prose default and searches that record category, so technical records are searched on demand.

Output: summaries (default), `--full` (whole JSON records), `--count`, `--field PATH` (extract a
dotted field, e.g. `message.usage`). `--width N` truncates each match to N characters, centered on
the match so it is never hidden; the default is full content.

`search --type user` with no query lists the human-typed messages. Tool results, slash commands, and
continuation notices are filtered out.

## Mapping the invocation

Always pass `--session-id ${CLAUDE_SESSION_ID}`; it is omitted below for brevity.

- `/history we discussed caching, what did we decide?`
  becomes `search --query "we discussed caching, what did we decide?"`
- `/history git-wright-revision what about the trailer?`
  resolves the token with `find`, then
  `search --name git-wright-revision --query "what about the trailer?"`
- `/history e1308f50 what about the trailer?`
  is the same, with `--uuid e1308f50` instead of `--name`.

A search line already shows the matched record in full. To see its neighbors for context, pass the
index and the ones around it to `fetch`, e.g. `fetch --uuid <session> --index 90 91 92`. Indices are
0-based; the index in a `[name:index]` prefix is the one `fetch` takes.

## Current-session recovery

`search --current` searches only the current session's out-of-context content, meaning everything
before the last `compact_boundary`, plus its parent and fork chain. Use it to recover what was
compacted away from this conversation specifically, rather than searching the whole project.

## Schema reference and compaction

The bundled JSON Schema (`references/claude-code-session-transcript.schema.json`) documents every
record type, field, and enum. Consult it to write ad-hoc queries beyond the standard verbs, for
correct field paths, record types, and subtypes. Attachment payloads are described per subtype, as
`attachment_<subtype>` definitions. It is a snapshot reference, not loaded at runtime, and is
refreshed from its upstream copy with `maintenance/sync-schema.sh`.

For how compaction, microcompaction, forks, and clear-context are recorded, and which content is out
of context, see `references/compaction-internals.md`.
