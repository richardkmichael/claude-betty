# markdown-format

A `PostToolUse` hook that formats a Markdown file each time Claude edits or writes one, so the model
does not spend requests checking line lengths and fixing formatting by hand.

For each edited `.md` or `.markdown` file, `format.py`:

1. Formats the blocks that changed (paragraphs, lists, tables, headings) with
   [mdformat](https://github.com/hukkin/mdformat): wraps prose at 100 columns, numbers ordered lists
   consecutively, aligns tables, removes bold (`mdformat-no-bold`) and keeps existing two spaces
   after a sentence (`mdformat-sentence-spacing`).  Unchanged blocks are left as they are, so a
   small edit to an unformatted file gives a small diff.  A new file is formatted whole.
2. Formats the YAML frontmatter with `claude-yaml` when it changed, keeping comments.  When the file
   is a Claude Code configuration file (an agent, skill, output style or memory file), `claude-yaml`
   also lints the frontmatter against the Claude Code schema.
3. Writes the file only if the result renders the same HTML as before, apart from removed bold.
4. Tells Claude what the formatting guarantees, and returns any frontmatter problems.

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
            "command": "uv",
            "args": [
              "run",
              "--script",
              "--with",
              "<mdformat-no-bold>",
              "--with",
              "<mdformat-sentence-spacing>",
              "--with",
              "<claude-yaml>",
              "<path>/hooks/markdown-format/format.py"
            ]
          }
        ]
      }
    ]
  }
}
```

Exec form expands no `~` or `$HOME`, so the paths must be absolute.

## Formatting files by hand

Given paths, the script formats those files whole instead of reading a hook event.  Use this to
bring an existing repository into line once, in a commit of its own:

```
uv run --script --with ... hooks/markdown-format/format.py docs/*.md
```

## Tests

```
uv run --with mdformat --with mdformat-gfm --with pytest --with <each package> \
    pytest hooks/markdown-format/tests
```
