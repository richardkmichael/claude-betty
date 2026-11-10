# Claude Code Hooks

All Claude Code hooks are documented at: https://code.claude.com/docs/en/hooks

There are hook events, inputs and outputs.  Hook outputs control Claude Code; tool-monitor reads the
input and returns no output.

This project captures four tool events:

- `PreToolUse`: before a tool call executes
- `PostToolUse`: after a tool call succeeds
- `PostToolUseFailure`: after a tool call fails
- `PermissionRequest`: when Claude Code asks for permission to run a tool call

It also captures six events for session, subagent and permission context:

- `SessionStart`: when a session starts, resumes, is cleared, compacted or forked
- `SessionEnd`: when a session ends
- `SubagentStart`: when a subagent is spawned
- `SubagentStop`: when a subagent finishes
- `PermissionDenied`: when auto mode denies a tool call
- `StopFailure`: when a turn ends on an API error

# Tool Hook Schemas

> Note: the official documentation has been observed to contain inaccuracies.  Treat the schemas
> here as starting points, and verify them against real captured payloads before relying on them for
> validation or migration work.

The four tool events share a common base, with varying properties per event:

- `tool_input`: present in all four events; varies by `tool_name`
- `tool_use_id`: present in `PreToolUse`, `PostToolUse` and `PostToolUseFailure`; absent from
  `PermissionRequest`
- `tool_response`: present in `PostToolUse` only; varies by `tool_name`
- `duration_ms`: present in `PostToolUse` and `PostToolUseFailure`
- `error` and `is_interrupt`: present in `PostToolUseFailure` only
- `permission_suggestions`: present in `PermissionRequest` only

## Common Fields

These top-level fields appear across hook events.  Optional fields are absent when they do not
apply.

| Field             | Type   | Present                                                           |
| ----------------- | ------ | ----------------------------------------------------------------- |
| `session_id`      | string | Always                                                            |
| `hook_event_name` | string | Always                                                            |
| `transcript_path` | string | Always; the main session's transcript, also inside a subagent     |
| `cwd`             | string | Always                                                            |
| `prompt_id`       | string | After the first user prompt; UUID of the prompt being processed   |
| `scratchpad_dir`  | string | When the session has a scratchpad directory                       |
| `permission_mode` | string | Tool events and most others; see below                            |
| `effort`          | object | `{"level": ...}`, on models with effort; see below                |
| `agent_id`        | string | When the hook fires inside a subagent or in-process teammate      |
| `agent_type`      | string | Inside a subagent, or in a session started with `--agent <name>`  |
| `mcp_server`      | object | `{"name": ..., "source": ...}`, on tool events for MCP tools only |
| `tool_name`       | string | Tool events and `PermissionDenied`; absent from the other events  |

`permission_mode` is one of `default`, `plan`, `acceptEdits`, `auto`, `dontAsk` or
`bypassPermissions`.

`effort.level` is one of `low`, `medium`, `high`, `xhigh` or `max`.  It is the level in effect when
the hook runs, including a skill's or subagent's level.

`agent_type` can appear without `agent_id`.  In a session started with `--agent`, the main session's
tool calls carry the agent name in `agent_type` and have no `agent_id`.  Inside a subagent, both
fields are present and `agent_type` is the subagent's type.

`mcp_server.name` is the server name, and `mcp_server.source` is where the server's definition came
from, such as `user`, `project`, `plugin`, `sdk` or `dynamic`.

## Base Schemas

### Tool Event Base Schema

All four tool events extend this base.  `hook_event_name` takes the event's own name.

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "type": "object",
  "properties": {
    "session_id": {"type": "string"},
    "prompt_id": {"type": "string"},
    "transcript_path": {"type": "string"},
    "cwd": {"type": "string"},
    "scratchpad_dir": {"type": "string"},
    "permission_mode": {"type": "string"},
    "effort": {"type": "object", "properties": {"level": {"type": "string"}}},
    "agent_id": {"type": "string"},
    "agent_type": {"type": "string"},
    "mcp_server": {
      "type": "object",
      "properties": {"name": {"type": "string"}, "source": {"type": "string"}}
    },
    "hook_event_name": {"const": "PreToolUse"},
    "tool_name": {"type": "string"},
    "tool_input": {"type": "object"},
    "tool_use_id": {"type": "string"}
  },
  "required": ["session_id", "transcript_path", "cwd", "hook_event_name", "tool_name",
               "tool_input", "tool_use_id"]
}
```

### PostToolUse Additions

```json
{
  "properties": {
    "tool_response": {
      "oneOf": [
        {"type": "string"},
        {"type": "object"},
        {"type": "array"}
      ]
    },
    "duration_ms": {"type": "number"}
  },
  "required": ["tool_response"]
}
```

`duration_ms` is the tool's execution time, excluding time spent in permission prompts and
`PreToolUse` hooks.

### PostToolUseFailure Additions

```json
{
  "properties": {
    "error": {"type": "string"},
    "is_interrupt": {"type": "boolean"},
    "duration_ms": {"type": "number"}
  },
  "required": ["error"]
}
```

There is no `tool_response`, since the tool failed before returning one.  `is_interrupt` is true
when the failure reached Claude Code as an abort rather than as an error the tool reported.  For
Bash, `error` starts with an `Exit code N` line, followed by the command's interleaved output.

### PermissionRequest Differences

`PermissionRequest` has no `tool_use_id`, so `required` omits it.  It adds:

```json
{
  "properties": {
    "permission_suggestions": {
      "type": "array",
      "items": {
        "type": "object",
        "properties": {
          "type": {"type": "string"},
          "rules": {
            "type": "array",
            "items": {
              "type": "object",
              "properties": {
                "toolName": {"type": "string"},
                "ruleContent": {"type": "string"}
              }
            }
          },
          "behavior": {"type": "string"},
          "mode": {"type": "string"},
          "directories": {"type": "array", "items": {"type": "string"}},
          "destination": {"type": "string"}
        }
      }
    }
  }
}
```

`permission_suggestions` holds permission updates Claude Code suggests for the request.  Each
entry's `type` determines its other fields:

| `type`                                    | Fields                             |
| ----------------------------------------- | ---------------------------------- |
| `addRules`, `replaceRules`, `removeRules` | `rules`, `behavior`, `destination` |
| `setMode`                                 | `mode`, `destination`              |
| `addDirectories`, `removeDirectories`     | `directories`, `destination`       |

The array is optional, and it does not list exactly the options the permission dialog shows.

## Other Event Fields

These events extend the common fields.  Apart from `PermissionDenied`, they have no `tool_name`, so
their rows in `tool_events` have a NULL `tool_name` column.

| Event              | Fields beyond the common fields                               |
| ------------------ | ------------------------------------------------------------- |
| `SessionStart`     | `source`; optional `model`, `agent_type`, `session_title`     |
| `SessionEnd`       | `reason`                                                      |
| `SubagentStart`    | `agent_id`, `agent_type`                                      |
| `SubagentStop`     | `agent_id`, `agent_type`, `agent_transcript_path`, and others |
| `PermissionDenied` | `tool_name`, `tool_input`, `tool_use_id`, `reason`            |
| `StopFailure`      | `error`; optional `error_details`, `last_assistant_message`   |

- `SessionStart.source` is `startup`, `resume`, `clear`, `compact` or `fork`.  A resumed or forked
  session can also carry `seconds_since_last_response`, `context_tokens`,
  `prompt_cache_likely_expired` and `estimated_cache_write_usd`.
- `SessionEnd.reason` is `clear`, `resume`, `logout`, `prompt_input_exit` or `other`.
- `SubagentStop` also has `stop_hook_active`, `last_assistant_message`, `background_tasks` and
  `session_crons`.  `agent_transcript_path` is the subagent's own transcript, and
  `last_assistant_message` is the text of its final response.  Claude Code also runs internal
  agents, for example for prompt suggestions, and `SubagentStop` fires for those with an empty
  `agent_type`.
- `PermissionDenied` fires only for auto mode denials, and carries `mcp_server` for MCP tools.
  `reason` names the denial, such as a classifier rule in square brackets.
- `StopFailure.error` is the error type, such as `rate_limit`, `overloaded`, `server_error` or
  `unknown`.  `last_assistant_message` holds the API error text.

## Tool-Specific Input/Output Schemas

Each tool extends the base schema with its own `tool_input` and `tool_response`.  The tables below
list the fields.  Fields marked optional are absent from some payloads.

Every built-in tool returns an object as its `tool_response`.  MCP tools return an object, an array
of content blocks or, rarely, a string, so the base schema accepts all three.

### Bash Tool

| Field                                    | Type    | Notes                                       |
| ---------------------------------------- | ------- | ------------------------------------------- |
| `tool_input.command`                     | string  |                                             |
| `tool_input.description`                 | string  | Optional                                    |
| `tool_input.timeout`                     | number  | Optional; milliseconds                      |
| `tool_input.run_in_background`           | boolean | Optional                                    |
| `tool_input.dangerouslyDisableSandbox`   | boolean | Optional                                    |
| `tool_response.stdout`                   | string  |                                             |
| `tool_response.stderr`                   | string  |                                             |
| `tool_response.interrupted`              | boolean |                                             |
| `tool_response.isImage`                  | boolean |                                             |
| `tool_response.noOutputExpected`         | boolean |                                             |
| `tool_response.backgroundTaskId`         | string  | Optional; background commands               |
| `tool_response.bashEditDiff`             | object  | Optional; files changed in a Git repository |
| `tool_response.persistedOutputPath`      | string  | Optional; output saved to a file            |
| `tool_response.returnCodeInterpretation` | string  | Optional                                    |

### Read Tool

| Field                  | Type   | Notes                     |
| ---------------------- | ------ | ------------------------- |
| `tool_input.file_path` | string | Absolute                  |
| `tool_input.offset`    | number | Optional                  |
| `tool_input.limit`     | number | Optional                  |
| `tool_response.type`   | string | `text` or `image`         |
| `tool_response.file`   | object | Contents depend on `type` |

For `text`, `file` has `filePath`, `content`, `numLines`, `startLine` and `totalLines`.  For
`image`, `file` has `base64`, `type` (a MIME type), `originalSize` and `dimensions`.

### Edit Tool

| Field                                    | Type    | Notes            |
| ---------------------------------------- | ------- | ---------------- |
| `tool_input.file_path`                   | string  | Absolute         |
| `tool_input.old_string`                  | string  |                  |
| `tool_input.new_string`                  | string  |                  |
| `tool_input.replace_all`                 | boolean |                  |
| `tool_response.filePath`                 | string  |                  |
| `tool_response.oldString`                | string  |                  |
| `tool_response.newString`                | string  |                  |
| `tool_response.originalFile`             | string  |                  |
| `tool_response.structuredPatch`          | array   | Hunks; see below |
| `tool_response.userModified`             | boolean |                  |
| `tool_response.replaceAll`               | boolean |                  |
| `tool_response.contentNotInModelContext` | boolean | Optional         |

Each `structuredPatch` hunk has `oldStart`, `oldLines`, `newStart`, `newLines` and `lines`.

### Write Tool

| Field                           | Type           | Notes                               |
| ------------------------------- | -------------- | ----------------------------------- |
| `tool_input.file_path`          | string         | Absolute                            |
| `tool_input.content`            | string         |                                     |
| `tool_response.type`            | string         | `create` or `update`                |
| `tool_response.filePath`        | string         |                                     |
| `tool_response.content`         | string         |                                     |
| `tool_response.originalFile`    | string or null | Null when the file is created       |
| `tool_response.structuredPatch` | array          | Same hunks as Edit; empty on create |
| `tool_response.userModified`    | boolean        |                                     |

### Grep Tool

| Field                       | Type    | Notes                                      |
| --------------------------- | ------- | ------------------------------------------ |
| `tool_input.pattern`        | string  |                                            |
| `tool_input.output_mode`    | string  | `content`, `files_with_matches` or `count` |
| `tool_input.path`           | string  | Optional                                   |
| `tool_input.glob`           | string  | Optional                                   |
| `tool_input.type`           | string  | Optional                                   |
| `tool_input.-i`, `-n`, `-o` | boolean | Optional                                   |
| `tool_input.-A`, `-B`, `-C` | number  | Optional                                   |
| `tool_input.multiline`      | boolean | Optional                                   |
| `tool_input.head_limit`     | number  | Optional                                   |
| `tool_response.mode`        | string  |                                            |
| `tool_response.filenames`   | array   | Strings                                    |
| `tool_response.numFiles`    | number  |                                            |
| `tool_response.content`     | string  | In `content` mode                          |
| `tool_response.numLines`    | number  | In `content` mode                          |

### Glob Tool

| Field                      | Type    | Notes    |
| -------------------------- | ------- | -------- |
| `tool_input.pattern`       | string  |          |
| `tool_input.path`          | string  | Optional |
| `tool_response.filenames`  | array   | Strings  |
| `tool_response.durationMs` | number  |          |
| `tool_response.numFiles`   | number  |          |
| `tool_response.truncated`  | boolean |          |

### WebFetch Tool

| Field                      | Type   | Notes                                       |
| -------------------------- | ------ | ------------------------------------------- |
| `tool_input.url`           | string |                                             |
| `tool_input.prompt`        | string |                                             |
| `tool_input.offset`        | number | Optional; characters to skip on a long page |
| `tool_response.url`        | string |                                             |
| `tool_response.code`       | number | HTTP status                                 |
| `tool_response.codeText`   | string |                                             |
| `tool_response.bytes`      | number |                                             |
| `tool_response.result`     | string | The model's answer to `prompt`              |
| `tool_response.durationMs` | number |                                             |

### WebSearch Tool

| Field                           | Type   | Notes                              |
| ------------------------------- | ------ | ---------------------------------- |
| `tool_input.query`              | string |                                    |
| `tool_input.mode`               | string | Optional; `standard` or `extended` |
| `tool_input.allowed_domains`    | array  | Optional                           |
| `tool_input.blocked_domains`    | array  | Optional                           |
| `tool_response.query`           | string |                                    |
| `tool_response.results`         | array  | Result objects and summary strings |
| `tool_response.durationSeconds` | number |                                    |
| `tool_response.searchCount`     | number |                                    |

### Agent Tool

| Field                             | Type    | Notes                                       |
| --------------------------------- | ------- | ------------------------------------------- |
| `tool_input.description`          | string  |                                             |
| `tool_input.prompt`               | string  |                                             |
| `tool_input.subagent_type`        | string  |                                             |
| `tool_input.model`                | string  | Optional                                    |
| `tool_input.effort`               | string  | Optional                                    |
| `tool_input.name`                 | string  | Optional                                    |
| `tool_input.run_in_background`    | boolean | Optional                                    |
| `tool_response.status`            | string  | `completed` or `async_launched`             |
| `tool_response.agentId`           | string  | Matches `agent_id` on the subagent's events |
| `tool_response.prompt`            | string  |                                             |
| `tool_response.resolvedModel`     | string  |                                             |
| `tool_response.canContinueAgent`  | boolean |                                             |
| `tool_response.outputFile`        | string  | Background agents                           |
| `tool_response.content`           | array   | Completed agents; text blocks               |
| `tool_response.totalDurationMs`   | number  | Completed agents                            |
| `tool_response.totalTokens`       | number  | Completed agents; final request only        |
| `tool_response.totalToolUseCount` | number  | Completed agents                            |
| `tool_response.toolStats`         | object  | Completed agents                            |
| `tool_response.usage`             | object  | Completed agents; final request only        |

For a background agent, `PostToolUse` fires when the agent moves to the background, not when it
finishes, so the response has no usage fields.  `SubagentStop` marks the end of the run.

### Skill Tool

| Field                        | Type    | Notes                                 |
| ---------------------------- | ------- | ------------------------------------- |
| `tool_input.skill`           | string  |                                       |
| `tool_input.args`            | string  | Optional                              |
| `tool_response.success`      | boolean |                                       |
| `tool_response.commandName`  | string  |                                       |
| `tool_response.allowedTools` | array   | Optional                              |
| `tool_response.status`       | string  | Optional; `forked` for a forked skill |
| `tool_response.agentId`      | string  | Optional; with `status: forked`       |
| `tool_response.result`       | string  | Optional; with `status: forked`       |

### ToolSearch Tool

| Field                                | Type   | Notes      |
| ------------------------------------ | ------ | ---------- |
| `tool_input.query`                   | string |            |
| `tool_input.max_results`             | number |            |
| `tool_response.query`                | string |            |
| `tool_response.matches`              | array  | Tool names |
| `tool_response.total_deferred_tools` | number |            |
| `tool_response.failed_mcp_servers`   | array  | Optional   |

### TaskCreate Tool

| Field                    | Type   | Notes                                      |
| ------------------------ | ------ | ------------------------------------------ |
| `tool_input.subject`     | string |                                            |
| `tool_input.description` | string |                                            |
| `tool_input.activeForm`  | string | Optional                                   |
| `tool_input.metadata`    | object | Optional; free-form                        |
| `tool_response.task`     | object | `id`, `subject` and optional `description` |

### TaskUpdate Tool

| Field                         | Type    | Notes                     |
| ----------------------------- | ------- | ------------------------- |
| `tool_input.taskId`           | string  |                           |
| `tool_input.status`           | string  | Optional; see below       |
| `tool_input.subject`          | string  | Optional                  |
| `tool_input.description`      | string  | Optional                  |
| `tool_input.addBlockedBy`     | array   | Optional                  |
| `tool_input.addBlocks`        | array   | Optional                  |
| `tool_input.owner`            | string  | Optional                  |
| `tool_input.metadata`         | object  | Optional; free-form       |
| `tool_response.success`       | boolean |                           |
| `tool_response.taskId`        | string  |                           |
| `tool_response.updatedFields` | array   |                           |
| `tool_response.statusChange`  | object  | Optional; `from` and `to` |

`status` is `pending`, `in_progress`, `completed` or `deleted`.

### SendMessage Tool

| Field                          | Type    | Notes                                |
| ------------------------------ | ------- | ------------------------------------ |
| `tool_input.to`                | string  |                                      |
| `tool_input.message`           | string  |                                      |
| `tool_input.summary`           | string  |                                      |
| `tool_input.type`              | string  |                                      |
| `tool_input.recipient`         | string  |                                      |
| `tool_input.recipient_kind`    | string  | `agent`, `name`, `session` or `main` |
| `tool_input.content`           | string  |                                      |
| `tool_response.success`        | boolean |                                      |
| `tool_response.message`        | string  |                                      |
| `tool_response.resumedAgentId` | string  | Optional                             |
| `tool_response.revivedAgentId` | string  | Optional                             |

### Monitor Tool

| Field                      | Type    | Notes |
| -------------------------- | ------- | ----- |
| `tool_input.command`       | string  |       |
| `tool_input.description`   | string  |       |
| `tool_input.timeout_ms`    | number  |       |
| `tool_response.taskId`     | string  |       |
| `tool_response.timeoutMs`  | number  |       |
| `tool_response.persistent` | boolean |       |

### ExitPlanMode Tool

| Field                     | Type   | Notes                                      |
| ------------------------- | ------ | ------------------------------------------ |
| `tool_input.plan`         | string | Injected by Claude Code from the plan file |
| `tool_input.planFilePath` | string | Injected by Claude Code                    |
| `tool_response.plan`      | string |                                            |

## Obsolete Tools

Claude Code no longer has these tools.  Their names still appear in databases that hold older
payloads.

| Tool           | Replacement                                  |
| -------------- | -------------------------------------------- |
| `Task`         | `Agent`                                      |
| `TodoWrite`    | `TaskCreate`, `TaskUpdate` and related tools |
| `MultiEdit`    | `Edit`                                       |
| `NotebookRead` | `Read`                                       |
| `LS`           | `Bash` and `Glob`                            |

## Other Tools

The tables above cover the core built-in tools.  The database also holds MCP tools
(`mcp__<server>__<tool>`) and other built-in tools such as `AskUserQuestion`, `SubagentHandback`,
`TaskGet`, `TaskList`, `TaskStop` and the `Cron*` tools.  To see their fields, read recent
payloads:

```sql
SELECT json_extract(payload, '$.tool_input')
  FROM tool_events
 WHERE tool_name = 'SubagentHandback' AND hook_event_name = 'PreToolUse'
 ORDER BY id DESC
 LIMIT 3;
```
