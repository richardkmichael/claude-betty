# markdown-format

A `PostToolUse` hook that formats a Markdown file each time Claude edits or writes one, so the model
does not spend requests checking line lengths and fixing formatting by hand.

For each edited `.md` or `.markdown` file, `format.py`:

1. Formats the body with [mdformat](https://github.com/hukkin/mdformat): wraps prose at 100 columns,
   numbers ordered lists consecutively, aligns tables, removes bold (`mdformat-no-bold`) and keeps
   existing two spaces after a sentence (`mdformat-sentence-spacing`).
2. Formats the YAML frontmatter with `claude-yaml`, keeping comments.  When the file is a Claude
   Code configuration file (an agent, skill, output style or memory file), `claude-yaml` also lints
   the frontmatter against the Claude Code schema.
3. Writes the file only if the formatted body renders the same HTML as before, apart from the
   removed bold.
4. Returns any frontmatter problems to Claude as additional context.

Claude Code notices the rewritten file and tells the model, so the next edit does not fail as stale.

## Requirements

- [uv](https://docs.astral.sh/uv/).  The script declares `mdformat` and `mdformat-gfm` inline, and
  `uv run --script` builds and caches its environment.
- The `mdformat-no-bold`, `mdformat-sentence-spacing` and `claude-yaml` packages, passed with
  `--with` or `--with-editable` (a local checkout or a git URL).

## Configuration

In `settings.json`, replace each `<...>` with the location of that package:

```json
{
  "hooks": {
    "PostToolUse": [
      {
        "matcher": "Edit|Write",
        "hooks": [
          {
            "type": "command",
            "command": "uv run --script --with <mdformat-no-bold> --with <mdformat-sentence-spacing> --with <claude-yaml> <path>/hooks/markdown-format/format.py"
          }
        ]
      }
    ]
  }
}
```

## Formatting files by hand

Given paths, the script formats those files instead of reading a hook event.  Use this to bring an
existing repository into line once:

```
uv run --script --with ... hooks/markdown-format/format.py docs/*.md
```

## Tests

```
uv run --with mdformat --with mdformat-gfm --with pytest --with <each package> \
    pytest hooks/markdown-format/tests
```
