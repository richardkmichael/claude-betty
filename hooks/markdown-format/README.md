# markdown-format

Hooks that format a Markdown file each time Claude changes one, so the model does not spend requests
checking line lengths and fixing formatting by hand.  They cover the Edit and Write tools and edits
made through Bash, such as inline Python that rewrites a file.

For each changed `.md` or `.markdown` file, `format.py`:

1. Formats the blocks that changed (paragraphs, tables, headings, and each item of a list) with
   [mdformat](https://github.com/hukkin/mdformat): wraps prose at 100 columns, numbers ordered lists
   1., 2., 3. without zero padding, aligns tables, removes bold (`mdformat-no-bold`) and keeps
   existing two spaces after a sentence (`mdformat-sentence-spacing`).  Unchanged blocks and list
   items are left as they are, so a small edit to an unformatted file gives a small diff.  A list
   whose numbering has a gap is renumbered whole.  Indented code blocks stay indented.  A new file
   is formatted whole, with indented code blocks fenced.
2. Formats the YAML frontmatter with `claude-yaml` when it changed, keeping comments.  When the file
   is a Claude Code configuration file (an agent, skill, output style or memory file), `claude-yaml`
   also lints the frontmatter against the Claude Code schema.
3. Writes the file only if the result renders the same HTML as before, apart from removed bold.
4. Tells Claude what the formatting guarantees, and returns any frontmatter problems.

Claude Code's memory index, `MEMORY.md` in a `memory` directory, is left as it is.  Claude Code
loads only the first 200 lines of the index, which keeps one entry per line, so wrapping it would
drop entries.

## Edits through Bash

Bash commands report no changed files reliably, so the hook watches the files a command names:

- Before the command runs (`PreToolUse`), it saves each Markdown file named in the command text,
  resolving relative paths against a leading `cd`.  Shell globs such as `docs/*.md` are expanded.
- After the command (`PostToolUse`), it formats each saved file whose content changed.  A file the
  command only read is unchanged and left alone, as is a file renamed or copied to a new name.
- A file the command restored from git, as committed or staged before the command ran (e.g.
  `git checkout -- <file>`), is left alone, so a restore holds.
- Claude is told which files were reformatted, and to re-read them before editing again.

A file whose path the command builds at run time is not seen.

## Requirements

- [uv](https://docs.astral.sh/uv/).  The script declares `mdformat` and `mdformat-gfm` inline, and
  `uv run --script` builds and caches its environment.
- The `mdformat-no-bold`, `mdformat-sentence-spacing` and `claude-yaml` packages, passed with
  `--with` or `--with-editable` (a local checkout or a git URL).

## Configuration

`settings.json` needs three hook entries, all running the same command:

| Event         | `matcher`     | `if`          |
| ------------- | ------------- | ------------- |
| `PostToolUse` | `Edit\|Write` |               |
| `PreToolUse`  | `Bash`        | `Bash(*.md*)` |
| `PostToolUse` | `Bash`        | `Bash(*.md*)` |

The `if` filter starts the Bash hooks only for commands that mention a Markdown file.  Each entry's
hook, in exec form, with each `<...>` replaced by the location of that package:

```json
{
  "type": "command",
  "if": "Bash(*.md*)",
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
```

Exec form expands no `~` or `$HOME`, so the paths must be absolute.

## Formatting files by hand

Given paths, the script formats those files whole instead of reading a hook event, fencing indented
code blocks.  Use this to bring an existing repository into line once, in a commit of its own:

```
uv run --script --with ... hooks/markdown-format/format.py docs/*.md
```

## Tests

```
uv run --with mdformat --with mdformat-gfm --with pytest --with <each package> \
    pytest hooks/markdown-format/tests
```
