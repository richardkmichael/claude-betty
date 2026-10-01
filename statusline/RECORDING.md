# What statusline.py records

`statusline.py` records quota data for the quota MCP server. The JSON Schemas in
`mcp/quota/schemas/` define the files the server reads, so any recorder that writes them works with
the server. This file describes how `statusline.py` writes them. A change that would break an
existing reader bumps `schema` in the session file.

## Location

The state directory is `$CLAUDE_QUOTA_STATE`, else `$XDG_STATE_HOME/claude-quota`, else
`~/.local/state/claude-quota`.

```
sessions/<session_id>.json                         one session's latest reading
accounts/<account_uuid>/usage.json                 the usage endpoint's last answer
accounts/<account_uuid>/history/<YYYY-MM-DD>.jsonl readings that moved, one file per UTC day
accounts/<account_uuid>/fetch.lock                 when a fetch was last attempted (its mtime)
accounts/<account_uuid>/fetches/<YYYY-MM-DD>.jsonl every usage-endpoint fetch attempted
```

The server reads the first three. Every file is written atomically or appended a line at a time,
so a reader never sees half a write.

## sessions/<session_id>.json

Schema: `mcp/quota/schemas/session.schema.json`. Rewritten on every render of that session,
including idle re-renders. `statusline.py` also writes `claude_pid` and `marker`, the cost and token
counts it compares between renders to detect a new API response. The server ignores both.

`response_at` is `null` until the session's first response after its file was created.

`context` is the main conversation's context as of the last response: the tokens in it, the window
size, and where auto-compaction fires, the same values the ctx gauge is drawn from. It is `null`
before the first response. `transcript_path` is the main conversation's transcript, where the
server finds a subagent's transcript to read its context.

`matches_login` is true when the reading's `seven_day.resets_at` is within a minute of the seven-day
reset in `account_uuid`'s `usage.json`. The payload names no account, and after `/login` a session
can go on reporting the previous account's quota for several responses. The seven-day reset is
fixed for the week and differs between accounts, so it identifies whose reading it is. It is false
until the new account's first fetch has landed.

## accounts/<account_uuid>/usage.json

Schema: `mcp/quota/schemas/usage.schema.json`. The per-model windows are the entries of
`usage.limits` whose `scope.model.display_name` is set. The file is replaced about once a minute
while any session on the account is drawing the line.

## accounts/<account_uuid>/history/*.jsonl

Schema for each line: `mcp/quota/schemas/history-row.schema.json`. There are two kinds of row, told
apart by `source`:

- `payload`, written on an API response whose reading `matches_login`, when it changed the
  session's `rate_limits` or is the first to match.
- `endpoint`, written when a fetch returned `limits` different from the last one.

An idle session writes no rows, so a gap in the history means the quota did not move or nothing
was drawing the line. The session file's `rendered_at` tells the two apart. Several sessions on one
account each write their own rows, so the same reading can appear more than once.

## accounts/<account_uuid>/fetches/*.jsonl

One line per attempt to fetch the usage endpoint: `t`, `status`, and `retry_after` when the
response carried one. `status` is the HTTP status, or `no_token`, `discarded` (the login changed
while the request was in flight), or the name of the exception that stopped the request. The
endpoint's rate limit is not documented, and this log is the record of what it accepts. The server
does not read it.

## Trusting a reading

A session's `rate_limits` is the last API response's, repeated on every render until the next one
arrives. It is the current login's reading when all of these hold:

- `rendered_at` is recent, so the status line is still being drawn;
- `rate_limits` and `response_at` are not `null`, so a response has reported quota data. They
  are `null` before a session's first response and just after `/login`, and always on an API key,
  Bedrock or Vertex;
- `matches_login` is true, so the data is the logged-in account's.

## Retention

Session files are deleted seven days after their last write, and history and fetch-log files nine
days after their last append. The fetch that runs about once a minute does the pruning.
