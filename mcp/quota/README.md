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
