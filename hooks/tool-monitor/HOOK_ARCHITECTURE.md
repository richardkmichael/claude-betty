# Hook Event Architecture

This document describes Claude Code's runtime hook event model: how events are identified, routed,
logged and paired.  It complements:

- `HOOK_SCHEMAS.md`: payload structure, the fields hooks receive on stdin
- [Claude Code Hooks reference](https://code.claude.com/docs/en/hooks): configuration syntax and the
  full list of events

## Two-Level Event Hierarchy

Tool hook events have a two-level identity:

1. The hook event type (`PreToolUse`, `PostToolUse`, `PermissionRequest`, etc.)
2. The specific tool that triggered it (`Bash`, `ExitPlanMode`, `Agent`, etc.)

### In session JSONL transcripts

Session transcripts record some hook runs as `attachment` entries.  The `attachment.type` reports
the outcome (`hook_success`, `hook_additional_context`, `hook_non_blocking_error`,
`hook_blocking_error`, `hook_cancelled`), and `hookName` carries both levels.  An entry from a
`PostToolUse` hook on a Bash call, with values trimmed:

```json
{
  "type": "attachment",
  "attachment": {
    "type": "hook_success",
    "hookName": "PostToolUse:Bash",
    "toolUseID": "toolu_...",
    "hookEvent": "PostToolUse",
    "content": "",
    "stdout": "{\"hookSpecificOutput\": {\"hookEventName\": \"PostToolUse\", ...}}\n",
    "stderr": "",
    "exitCode": 0,
    "command": "uv run --script .../hooks/markdown-format/format.py",
    "durationMs": 221
  },
  "uuid": "...",
  "timestamp": "...",
  "sessionId": "..."
}
```

For tool events, `hookName` follows the format `{hookEvent}:{tool_name}`.  The part after the colon
is the tool name, not the matcher from configuration.  Other events use the event name alone
(`Stop`) or the event name and the value the matcher filters on (`SessionStart:startup`).
`toolUseID` is the `tool_use_id` of the call, so a transcript entry joins to the tool-monitor rows
of the same call.

Not every hook run is recorded.  The tool-monitor hook writes nothing to stdout, and its runs do not
appear in transcripts.

### In hook stdin payloads

The compound `hookName` form does not appear in the JSON passed to hook scripts.  Instead, the two
levels are separate fields:

```json
{
  "hook_event_name": "PreToolUse",
  "tool_name": "ExitPlanMode",
  ...
}
```

Events that are not about a tool call, such as `SessionStart` or `SubagentStop`, have no
`tool_name`.

### In settings.json configuration

Hooks subscribe to events using the `matcher` field, which filters each event on one field of its
payload:

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "*",
        "hooks": [{"type": "command", "command": "my-hook.sh"}]
      }
    ]
  }
}
```

For the tool events (`PreToolUse`, `PostToolUse`, `PostToolUseFailure`, `PermissionRequest`,
`PermissionDenied`), the matcher filters on the tool name.  `"*"`, `""` or an omitted matcher fires
for every tool; `"Bash"` fires only for Bash; `"Edit|Write"` fires for either.  Other events filter
on another field, such as the session source for `SessionStart` or the agent type for
`SubagentStart` and `SubagentStop`, and some events ignore the matcher.  The
[Matcher patterns](https://code.claude.com/docs/en/hooks#matcher-patterns) section of the hooks
reference lists the field and the values for each event.

The matcher value does not appear in hook stdin or in the `hookName` logged to the session
transcript.  Only the resolved `tool_name` does.

## Event Pairing

The events of one tool call share its `tool_use_id`.  `PreToolUse`, `PostToolUse` and
`PostToolUseFailure` all carry it.  `PermissionRequest` does not.  Match a `PermissionRequest` row
to its `PreToolUse` row by `session_id`, `agent_id` and `tool_input`.

A tool call produces one `PreToolUse` event and then, in most cases, either one `PostToolUse` or one
`PostToolUseFailure`.  Some calls have neither, about 1% of Bash calls.  Neither event fires when:

- the user cancels the running tool
- the call is denied permission (`PreToolUse` fires; `PermissionDenied` fires for auto mode denials)
- Claude Code rejects the input in validation, before any hook runs (no `PreToolUse` either)

A query that pairs events by `tool_use_id` must allow for a missing result row.

## Lifecycle Gaps

### ExitPlanMode on the "clear context" path

When a user approves a plan with "Yes, clear context and auto-accept edits", the session resets
before the PostToolUse hook fires.  The recorded events:

```
id    hook_event_name    tool_name
N     PreToolUse         ExitPlanMode
N+1   PermissionRequest  ExitPlanMode
(no PostToolUse: session reset)
N+2   PreToolUse         Glob          <- new session
```

The user's choice, to continue in the session or to clear context, is not recorded in any tool hook
payload.  To infer it, check whether a new session starts in the same project directory within
seconds of the ExitPlanMode `PermissionRequest`.
