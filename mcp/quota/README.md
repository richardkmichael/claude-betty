# quota

An MCP server with one tool, `get-quota`, that tells Claude how much of the plan's quota is left and
how fast it is being spent, so that it can decide what work to start. It reports the five-hour and
seven-day plan windows and any per-model weekly windows, each with the percentage used, the reset
time, and the rate of use over the last 2, 10 and 20 minutes. A forecast lists every window with the
minutes until it runs out if the last 2 minutes' rate continues, beside the minutes until it resets.
The longer spans show whether that rate is a burst or steady.

The data is what the status line shows. The server fetches nothing: it reads what
`statusline/statusline.py` records on every render, as described in `statusline/RECORDING.md`.
When a recording has stopped being updated, the tool returns an error instead of a number, because
a value that no longer moves reads as quota not being spent.

## What it returns

An answer for an account under heavy load, abridged:

```json
{
  "account": "Work",
  "plan_as_of": "2026-09-30T23:06:34+00:00",
  "exhaustion_at_last_2_min_rate": {
    "five_hour": { "in_minutes": 80, "resets_in_minutes": 284 },
    "seven_day": { "in_minutes": null, "resets_in_minutes": 3353 },
    "Fable": { "in_minutes": null, "resets_in_minutes": 3353 }
  },
  "plan": {
    "five_hour": {
      "used_percent": 20,
      "resets_at": "2026-10-01T04:00:00+00:00",
      "resets_at_local": "2026-09-30T21:00:00-07:00",
      "resets_in_minutes": 284,
      "percent_per_minute": { "last_2_min": 1.0, "last_10_min": 1.0, "last_20_min": 1.25 }
    },
    "seven_day": { "...": "the same fields" }
  },
  "models": {
    "Fable": { "...": "the same fields", "fetched_seconds_ago": 21 }
  }
}
```

- `in_minutes` is `null` when the 2-minute rate is zero or not measured yet.
- Rates are percentage points per minute, every span ending now. Percentages are whole numbers,
  so the 2-minute rate moves in steps of half a point a minute.
- `measured_minutes` appears only for a span that reaches back past the start of the window or of
  the recording.
- The answer is MCP structured content, with an `outputSchema` describing every field.

When it cannot vouch for a figure, the tool returns an error that says why and what to do: the
status line is not recording, its recording has gone stale, no quota data has arrived since the
session started or since `/login`, or the data is still the previous login's. Per-model figures
older than five minutes are left out with a note, as the status line shows `STL` for them.

## How the model is told to use it

The server's instructions, which Claude Code shows the model at session start, say to call
`get-quota` before large or parallel work and when asked about quota. They tell the model never to
slow down for quota, since reaching a limit only pauses work until the reset and quota left unused
at a reset is lost. Near a limit, it should start only work that will finish before it, and it
should ask first if the seven-day window would run out. Quota used by other sessions or other
machines is normal, and the model is told not to investigate it. A running session picks up
changed instructions when its server reconnects.

## Requirements

- `statusline.py` running as the status line command, either drawing the line or with
  `--record-only` to keep Claude Code's own footer. See the status line section of the top-level
  README.
- `uv`, which runs `server.py` with its one dependency, the MCP Python SDK.
- The `PreToolUse` hook in `hook.py`, which passes the calling session's ID to the tool.

## Installation

Register the server under the name `quota`, since the hook's matcher below depends on it:

```
claude mcp add --scope user quota -- uv run --script "$PWD/mcp/quota/server.py"
```

Add the hook to `~/.claude/settings.json`, with the path to your checkout:

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "mcp__quota__get-quota",
        "hooks": [{ "type": "command", "command": "python /path/to/mcp/quota/hook.py" }]
      }
    ]
  }
}
```

Setting `statusLine.refreshInterval` is recommended. The status line is otherwise drawn only when
something in the session changes, and a call made while it has not been drawn for a minute, such
as one from a long-running subagent, is refused as stale.

## Why the hook

The tool publishes no parameters, yet the server has to know which session is asking so that it
can read that session's recording. The `CLAUDE_CODE_SESSION_ID` the server inherits is fixed when
Claude Code starts it, and goes stale on `/clear` or an in-session `/resume`, while the server keeps
running. No MCP request carries the current ID either.

A `PreToolUse` hook's input always carries the current `session_id`, and the `updatedInput` it
returns is merged into the call's arguments before the call is sent. So `hook.py` adds
`session_id` to every `get-quota` call, and the server parses it from the raw arguments against a
private allow-list. The model sees a tool with no inputs, and has nothing to fill in or get wrong.
A call from a subagent carries the parent session's ID, which is the session whose status line is
recorded.

Without the hook, every call fails with an error that names it.
