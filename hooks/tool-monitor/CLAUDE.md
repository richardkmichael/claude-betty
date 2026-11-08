# Tool Monitor Description

This Claude tool is a Claude Code hook that stores hook payloads in SQLite for later analysis of
Claude's tool usage.  It runs on the tool events `PreToolUse`, `PostToolUse`, `PostToolUseFailure`
and `PermissionRequest`, and on `SessionStart`, `SessionEnd`, `SubagentStart`, `SubagentStop`,
`PermissionDenied` and `StopFailure`.

The hook does not *alter* tool calls.  It writes nothing to stdout and exits 0 once the payload is
stored.

# Resources

- Overview: @./README.md
- `tool-monitor` hook information: @./HOOK_SCHEMAS.md
- Hook event naming and pairing: `HOOK_ARCHITECTURE.md`
- Claude Code Hooks reference documentation: https://code.claude.com/docs/en/hooks
  - Review this documentation when adding new features, debugging, or answering questions about
    Hooks, generally
  - Official documentation has known inaccuracies; always verify hook schemas empirically against
    real captured payloads
