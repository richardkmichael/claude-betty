# Summary

A collection of skills, subagents, hooks, output styles, mods, a status line, and an MCP server for
Claude Code. Each component is self-contained and installed independently.

## Subagents

- `git-wright` — owns all git operations: committing, interactive rebase, staging, conflict
  resolution, branch and remote management, and pull requests.
- `github-researcher` — researches GitHub (issues, pull requests, releases, CHANGELOGs) through
  the `gh` CLI.

## Skills

- `create-commit-message` — write a commit message for the staged changes, matching the project's
  conventions.
- `handle-ci-failure` — diagnose a failed GitHub Actions run, match it to a known failure pattern,
  fix it, verify, then commit and push. Reads a per-project `.github/HANDLE_CI_FAILURE.md` for the
  branch, check command, push policy, and project-specific patterns.
- `history` — search and read past Claude Code session transcripts.
- `learning-quiz` — teach the operator the concepts covered in the session, working a checklist and
  quizzing to confirm each stage before moving on.
- `matlab-docs` — look up MATLAB documentation by function name, `mathworks.com` URL, or local
  help-HTML path. Checks a local MATLAB install before the network.
- `matlab-profile` — investigate MATLAB performance: time hotspots, memory, parallelism, and call
  patterns. Profiles and reports; it does not change your code.
- `refine-plan` — interview the user about a plan file to sharpen it.
- `review-pr-companion` — package a code review as a review branch of fixup or standalone commits
  plus a REVIEW_HANDOFF.md that walks the author's agent through it, pushed with no PR opened.
- `tmux` — drive interactive CLIs (git, python, gdb) by sending keystrokes and scraping pane
  output.
- `tool-usage` — query the `tool-monitor` database to analyze Claude's tool usage.

## Hooks

- `tool-monitor` — captures every tool invocation into a SQLite database via the four
  tool-hook events, for later analysis of tool usage. A Rust binary; pairs with the `tool-usage`
  skill. See `hooks/tool-monitor/README.md` for the schema, example queries, and migration tools.
- `github-researcher` — denies `github.com` and `githubusercontent.com` WebFetch calls in the main
  agent, allowed inside the agent, and logged either way. Pairs with the `github-researcher`
  subagent.
- `markdown-format` — formats each Markdown file Claude edits: wraps at 100 columns, aligns tables,
  removes bold, and formats and lints Claude Code frontmatter. See
  `hooks/markdown-format/README.md` for setup.
- `background-shell` — tells Claude, after each Bash command that ends up in the background, that
  the command's shell has job control on, so stopping one of its process groups ends the task and
  kills the stopped work. Standard library only.

## Output styles

- `plain` — write to be read fast and understood once: common words over impressive ones, no
  rhetorical flourishes, and no volitional verbs for what code does.

## Status line

- `statusline.py` — a three-section status line: context and rate-limit gauges on the left, a
  pull-request badge in the centre, and account, model, and effort level on the right. Context is
  measured against the auto-compaction threshold, so 100% is the point the session gets compacted
  rather than the point the context window fills. Where a plan meters a model against its own
  weekly allowance, that window gets a gauge of its own beside the plan's, because the plan gauge
  alone reads reassuringly right up to the point the model stops answering. When the line will not
  fit the terminal, the least important items are dropped one at a time instead of letting the
  terminal truncate at an arbitrary column. Status line rendering records quota data, which the
  `quota` MCP server reads. Standard library only.

## Mods

Mods are Claude Code plugins with a hooks module.

- `commits` — `/commits` opens a pane beside the transcript listing the branch's commits, with the
  selected commit's message and diff. Claude can open the same pane, naming the commits to show. A
  commit the user picks goes to Claude with their next prompt. See `mods/commits/README.md`.
- `statusline` — draws `statusline.py`'s line above the prompt on Claude Code Desktop, which has no
  status line setting. See `mods/statusline/README.md`.

`mods/DEVELOPMENT.md` collects what was learned writing them: where the reference material is, how
to run a mod, panes, keys and focus, and what installed mods cannot do.

## MCP servers

- `quota` — a `get-quota` tool that tells Claude how much plan quota is left and how fast it is
  being spent. Claude Code passes quota data only to the status line command, so `statusline.py`
  writes it and the server only reads it. It needs that script as the status line command, drawing
  or `--record-only`. A `PreToolUse` hook passes it the calling session's ID, and a second hook
  tells Claude when a window passes 90%, 95% or 99%.

## tmux notifications

- `tmux/` — delivers Claude Code's desktop notifications from inside tmux, with any terminal
  application, on macOS and Linux. A `SessionStart` hook copies the session's pane output to a
  watcher, which posts each notification naming the pane's project and its tmux session, window and
  pane. Clicking it switches tmux to that pane. Standard library only.

## Installation

### Skills, subagents, and output styles

Claude Code discovers skills from `~/.claude/skills/`, subagents from `~/.claude/agents/`, and
output styles from `~/.claude/output-styles/`.

Install a component by symlinking it or copying it:

```
# Skill: symlink the directory (or use `cp -R` to copy)
ln -s "$PWD/skills/tool-usage" ~/.claude/skills/tool-usage

# Subagent: symlink the file (or use `cp` to copy)
ln -s "$PWD/subagents/git-wright.md" ~/.claude/agents/git-wright.md

# Output style: symlink the file (or use `cp` to copy)
ln -s "$PWD/output-styles/plain.md" ~/.claude/output-styles/plain.md
```

Select an installed output style with `/output-style`.

One skill needs another component installed first: `tool-usage` reads the database the
`tool-monitor` hook writes.

### tool-monitor hook

Build and install the binary, then wire the four tool-hook events (`PreToolUse`, `PostToolUse`,
`PostToolUseFailure`, `PermissionRequest`) to it in `settings.json`:

```
cargo install --path hooks/tool-monitor --root ~
# installs ~/bin/claude-tool-monitor
```

The database is created on first run. See `hooks/tool-monitor/README.md` for the exact hook
configuration, schema, example queries, and migration tools.

### github-researcher hook

`gate.sh` runs as a `PreToolUse` hook on `WebFetch`; `invocation.sh` runs as a `SubagentStart` hook
matched to the `github-researcher` agent. Place both scripts where Claude Code can run them
(symlink or copy), then wire them in `settings.json`:

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "WebFetch",
        "hooks": [{"type": "command", "command": "~/.claude/hooks/github-researcher/gate.sh"}]
      }
    ],
    "SubagentStart": [
      {
        "matcher": "github-researcher",
        "hooks": [{"type": "command", "command": "~/.claude/hooks/github-researcher/invocation.sh"}]
      }
    ]
  }
}
```

Pair it with the `github-researcher` subagent so denied main-thread fetches have somewhere to route.

### background-shell hook

`notice.py` runs as a `PostToolUse` hook on `Bash`:

```json
{
  "hooks": {
    "PostToolUse": [
      {
        "matcher": "Bash",
        "hooks": [
          {"type": "command", "command": "python /path/to/this-repository/hooks/background-shell/notice.py"}
        ]
      }
    ]
  }
}
```

### Status line

`statusline.py` reads the status line payload on stdin and prints one line. Place it where Claude
Code can run it, then point `settings.json` at it:

```
ln -s "$PWD/statusline/statusline.py" ~/.claude/statusline.py
```

```json
{
  "statusLine": {
    "type": "command",
    "command": "~/.claude/statusline.py",
    "padding": 2
  }
}
```

The script draws its own pull-request badge, so set `prStatusFooterEnabled` to `false` in
`settings.json` to drop the duplicate from Claude Code's footer. `ACCOUNT_LABELS` near the top of
the file maps an email address to the account name shown for it. The module docstring covers the
environment variables that tune width detection, including the column ruler used to measure how
many columns the host's own chrome occupies.

One thing to know before installing it: the per-model gauge needs data that only Anthropic's OAuth
usage endpoint reports, so the script fetches it itself. Once the data for the logged-in account is
90 seconds old, a render starts the script again in the background to call the endpoint, reading the
login's access token from the Keychain (or from `.credentials.json` off macOS) without renewing it.
The request costs no tokens, the fetch is detached so no render ever waits on it, and a lock file
keeps concurrent sessions to one fetch every 90 seconds between them. When the endpoint rate-limits
a fetch, the next attempt waits as long as the endpoint says, and never less than five minutes,
because it usually says to retry at once. Every attempt is logged so the undocumented limit can be
read off. The answers are kept under `~/.local/state/claude-statusline-quota/`, one directory per
account. A value too old to trust reads `STL` in place of the percentage rather than quietly showing
a stale number. This is the only part of the script that reaches beyond reading a file.

Status line rendering also records quota data under the same directory, which the `quota` MCP
server reads. `mcp/quota/schemas/` defines the files, and `statusline/RECORDING.md` describes how
they are written. To keep the recording without
drawing a line, and so keep Claude Code's own footer, run it with `--record-only`: it prints
nothing and exits 0, which Claude Code treats the same as having no status line.

```json
"command": "~/.claude/statusline.py --record-only"
```

A host that has no status line of its own, such as a mod drawing the line on Claude Code Desktop,
runs it with `--cached-limits --sections`. `--cached-limits` fills in the five-hour and seven-day
windows from the usage cache when the payload has no rate limits, and `--sections` prints the line's
parts as a JSON object for the host to lay out, instead of one line.

Its tests run from the repository root with `uv run --with pytest pytest statusline/tests`.

To combine it with another status line producer, pipe that producer into it:

```
"command": "other-producer | ~/.claude/statusline.py"
```

### Mods

Load a mod for one session with `claude --plugin-dir /path/to/this-repository/mods/<name>`, and
repeat the flag to load several.

To load mods in every session, list their directories in `CLAUDE_CODE_PLUGIN_DIRS`, in the `env`
block of `~/.claude/settings.json`. This is also the only way to load a mod in Claude Code Desktop,
which takes no flags.

```json
{
  "env": {
    "CLAUDE_CODE_PLUGIN_DIRS": "/path/to/this-repository/mods/commits:/path/to/this-repository/mods/statusline"
  }
}
```

The value is one string. Each entry is a single mod's directory, the one holding
`.claude-plugin/plugin.json`, given as an absolute path or one starting with `~`. Entries are
separated by `:` on macOS and Linux, and by `;` on Windows. A project's `.claude/settings.json`
and `.claude/settings.local.json` cannot set the variable: Claude Code ignores it there.

### quota MCP server

`mcp/quota/README.md` has the `claude mcp add` command and the `PreToolUse` hook entry. The server
needs `uv`, and `statusline.py` installed as above.

### tmux notifications

`tmux/README.md` has the `settings.json` entries for the notification setting and the `SessionStart`
hook, and covers building the macOS notifier app.
