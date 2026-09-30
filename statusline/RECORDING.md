# What statusline.py records

`statusline.py` records what it reads so that the quota MCP server can answer with the figures the
status line shows. This file is the contract between the two. A change to the layout needs a
matching change in the server, and a change that would break an existing reader bumps `schema`.

## Location

The state directory is `$CLAUDE_QUOTA_STATE`, else `$XDG_STATE_HOME/claude-quota`, else
`~/.local/state/claude-quota`.

```
sessions/<session_id>.json                         one session's latest reading
accounts/<account_uuid>/usage.json                 the usage endpoint's last answer
accounts/<account_uuid>/fetch.lock                 when a fetch was last attempted (its mtime)
accounts/<account_uuid>/history/<YYYY-MM-DD>.jsonl readings that moved, one file per UTC day
```

Every file is written atomically or appended a line at a time, so a reader never sees half a write.

## sessions/<session_id>.json

Rewritten on every render of that session, including idle re-renders.

| Field           | Meaning                                                                  |
| --------------- | ------------------------------------------------------------------------ |
| `schema`        | `1`                                                                      |
| `session_id`    | The payload's `session_id`, which changes on `/clear`                    |
| `claude_pid`    | `CLAUDE_PID` of the Claude Code process drawing the line                 |
| `account_uuid`  | The login `.claude.json` named at this render; `null` without one        |
| `account_label` | The account name the status line shows                                   |
| `rendered_at`   | Epoch seconds of this render                                             |
| `response_at`   | Epoch seconds of the render that first showed the latest API response    |
| `matches_login` | Whether `rate_limits` is `account_uuid`'s own reading; see below         |
| `marker`        | Cost and token counts, compared between renders to detect a new response |
| `rate_limits`   | The payload's `five_hour` and `seven_day`, or `null` when it has none    |

Each window in `rate_limits` is `{"used_percentage": <number>, "resets_at": <epoch seconds>}`.

`response_at` is `null` until the session's first response after its file was created.

`matches_login` is true when the reading's `seven_day.resets_at` is within a minute of the seven-day
reset in `account_uuid`'s `usage.json`. The payload names no account, and after `/login` a session
can go on reporting the previous account's figures for several responses. The seven-day reset is
fixed for the week and differs between accounts, so it identifies whose reading it is. It is false
until the new account's first fetch has landed.

## accounts/<account_uuid>/usage.json

`{"fetched_at": <epoch seconds>, "usage": <the endpoint's answer, verbatim>}`. The per-model
windows are the entries of `usage.limits` whose `scope.model.display_name` is set. Each entry has a
`kind`, a `percent` and an ISO 8601 `resets_at`. The file is replaced about once a minute while any
session on the account is drawing the line.

## accounts/<account_uuid>/history/*.jsonl

One JSON object per line, in two kinds, told apart by `source`:

- `payload`, written on an API response whose reading `matches_login`, when it changed the
  session's `rate_limits` or is the first to match. It carries `t`, `session_id`, `five_hour`
  and `seven_day`, the last two in the session file's shape.
- `endpoint`, written when a fetch returned `limits` different from the last one. It carries `t`
  and `limits`, in `usage.json`'s shape.

`t` is epoch seconds. An idle session writes no rows, so a gap in the history means the figures did
not move or nothing was drawing the line. The session file's `rendered_at` tells the two apart.
Several sessions on one account each write their own rows, so the same reading can appear more than
once.

## Trusting a reading

A session's `rate_limits` is the last API response's, repeated on every render until the next one
arrives. It is the current login's reading when all of these hold:

- `rendered_at` is recent, so the status line is still being drawn;
- `rate_limits` and `response_at` are not `null`, so a response has reported quota data. They
  are `null` before a session's first response and just after `/login`, and always on an API key,
  Bedrock or Vertex;
- `matches_login` is true, so the figures are the logged-in account's.

## Retention

Session files are deleted seven days after their last write, and history files nine days after
their last append. The fetch that runs about once a minute does the pruning.
