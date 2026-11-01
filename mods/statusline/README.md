# statusline

A Claude Code mod: draws this repository's status line in the band above the prompt on Claude Code
Desktop, which has no `statusLine` setting. In the terminal, Claude Code runs the user's
`statusLine` command as usual and the mod draws nothing.

## How it works

The band is redrawn each time a turn ends. The mod builds a status line payload from
`$.session.usage()` and the session's working directory, and runs
`~/.claude/statusline.py --cached-limits --sections` on it:

- `--cached-limits` fills in the five-hour and seven-day windows from the script's usage cache,
  since a Desktop session may report no rate limits.
- `--sections` prints the line's parts as JSON. The mod places the context gauge on the left, the
  quota gauges and the directory in the centre, and the account on the right.

The payload has no session ID, so the script records nothing for the `quota` MCP server from a
payload partly filled from its cache. The model is left out, because Desktop shows it under the
prompt. If the script fails, the band shows its error line.

## Installing

The mod needs `statusline.py` installed as `~/.claude/statusline.py`; see the repository README. A
Desktop session takes no `--plugin-dir` flag, so name the mod's directory in
`CLAUDE_CODE_PLUGIN_DIRS`, in the `env` block of `~/.claude/settings.json`:

```json
{
  "env": {
    "CLAUDE_CODE_PLUGIN_DIRS": "/path/to/this-repository/mods/statusline"
  }
}
```

Several mods are separated with the platform's path-list separator, `:` on macOS and Linux.
