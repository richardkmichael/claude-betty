# Summary

A collection of skills, subagents, hooks, output styles, and a status line for Claude Code. Each
component is self-contained and installed independently.

## Subagents

- `git-wright` — owns all git operations: committing, interactive rebase, staging, conflict
  resolution, branch and remote management, and pull requests.
- `github-researcher` — researches GitHub (issues, pull requests, releases, CHANGELOGs) through
  the `gh` CLI.

## Skills

- `create-commit-message` — write a commit message for the staged changes, matching the project's
  conventions.
- `git-interactive` — reshape git history: interactive rebase, split, squash, reorder, amend,
  `add -p`. Uses the `tmux` skill for the terminal session.
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
  terminal truncate at an arbitrary column. Standard library only.

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

Two skills need another component installed first: `tool-usage` reads the database the
`tool-monitor` hook writes, and `git-interactive` drives its terminal through the `tmux` skill.

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

One thing to know before installing it: the per-model gauge reads figures Claude Code caches from
its usage endpoint, and nothing refreshes those on a schedule — they are written only when
`/usage` runs. So once they pass six minutes old, a render starts `claude -p /usage` in the
background to replace them. That command costs no tokens and starts no session, the spawn is
detached so no render ever waits on it, and a lock file in the temp directory keeps concurrent
sessions to one refresh between them. A figure too old to trust reads `STL` in place of the
percentage rather than quietly showing a stale number. This is the only part of the script that
reaches beyond reading a file, and it is confined to one function.

To combine it with another status line producer, pipe that producer into it:

```
"command": "other-producer | ~/.claude/statusline.py"
```
