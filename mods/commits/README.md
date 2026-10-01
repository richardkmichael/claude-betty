# commits

A Claude Code mod: `/commits` opens a pane beside the transcript listing the
branch's commits, newest first, with the selected commit's message and diff
beneath. Claude can open the same pane itself, naming the commits to show and
a note to draw under each. A commit the user asks about rides their next
prompt as hidden context.

Function hooks are early access. The mod loads only under
`CLAUDE_CODE_ENABLE_FUNCTION_HOOKS=1`, and the API it is written against may
change between releases.

## Doors

| door                  | who     | what it lists                                        |
| --------------------- | ------- | ---------------------------------------------------- |
| `/commits`            | the user | `origin/HEAD..HEAD`, else `main` or `master`, else 20 |
| `/commits <range>`    | the user | the range as typed (`main..HEAD`, `HEAD~3..HEAD`)     |
| `mcp__commits__show`  | Claude  | `range`, or `commits: [{ sha, note? }]`, or the branch |
| `mcp__commits__close` | Claude  | closes the pane                                      |

Both doors call one implementation. The user's command answers with a line
in the transcript and a hidden note telling Claude the pane is open; the
tool answers Claude directly. A subagent's call is refused.

## In the pane

The header names the range and the count, with the key help in the middle
and `a: ask about commit` on its right. One row per commit follows, then the
selected commit's message and diff in a bordered window that scrolls under
the pinned rows.

The pane opens holding the keyboard, with the focus ring on the selected
row. `ctrl+x tab` gives a pane the keys later; `ctrl+x x` closes one.

| key                   | what it does                                              |
| --------------------- | --------------------------------------------------------- |
| Tab, Shift+Tab        | move the ring; a row it lands on becomes the selection    |
| ↑ ↓                   | move the selection                                        |
| ctrl+↑, ctrl+↓        | move the selection, from the prompt too (opt+↑/↓ as well) |
| `f`, `b`              | page the content forward and back, as `less` does         |
| PgUp PgDn, wheel      | scroll the content; the list stays put                    |
| Enter                 | press the ring's button                                   |
| `a`                   | attach the selected commit to the next prompt, or detach  |
| Esc                   | close the pane                                            |

Pressing `a` includes the selected commit: the row gets a green `⧉` in the
gutter and `[⧉ <sha>]` is appended to the prompt box at once, painted in the
colour the engine gives the native diff selection's pill. The engine
gives the keyboard to a non-empty composer, so `ctrl+x tab` returns to the
pane. Pressing `a` again on an included commit excludes it and takes its
commit reference out. Closing the pane keeps the commits included; reopening
shows them marked. While a commit reference is in the box a slash command
cannot be typed, as with the engine's own pasted-text placeholders; clear
the box or send first.

The selected commit's message and diff are drawn in a region of the pane's
own (a `Client` element, terminal and desktop only). Dragging over lines
there includes that range: the range's first and last lines get `⧉` in
the region's gutter and the lines between a dim `┊`, the commit's row gets
a yellow `⧉` (green once the whole commit is included, which also drops its
ranges), and `[⧉ <sha>:<from>-<to>]` is appended to the prompt box. A click on an included range excludes it; a click elsewhere only
gives the region the keys. While the region holds them: `f` `b` PgUp PgDn
page it and `j` `k` scroll it a line, Tab, Shift+Tab and ↑ ↓ move between
commits, `a` includes or excludes the selected commit, and the keys stay in
the region through all of it, unlike `a` pressed on the pane's own ring,
after which the composer takes them. Other typed letters go nowhere; Escape
hands the keys back to the composer.

When the prompt is sent, each included commit rides along as hidden context
beginning `The user attached commit <sha>`: a `<commit>` element with the
sha, author and date as attributes, holding `<commit-subject>`,
`<commit-message>` and one `<commit-diff path="…">` per file with its hunks
as `git show` prints them (a `note` attribute instead for a rename or a
binary). Each included range rides along beginning `The user attached lines
of commit <sha>`: a `<commit-lines>` element holding one element per run of
lines, named for the part of the commit it is from. A `<commit-diff>` run
carries `new-rev` and `new-lines`, the lines' numbers in the file at the
commit worked out from the hunk header, and, when the run removes lines,
`old-rev` and `old-lines` at the parent, so the model can read around them
with `git show <rev>:<path>` instead of finding them in the diff again.
Commit references are stripped from the text first. A written commit
reference deleted by hand is a detach, and the gutter clears on that edit. A
prompt that held only commit references is sent as `See the attached
commit.` `/clear` and `/resume` forget everything and take the commit
references out.

The ctrl+↑/↓ chords are the engine's diff-list actions, declared on two
empty buttons; with the built-in diff pane open too, the engine gives the
chord to the pane drawn last.

## Limits of the prototype

- While the pane's ring holds the keys, Space, Backspace and any other key
  the pane does not use go to the composer and take the keys with them;
  `ctrl+x tab` returns to the pane. The engine's `abovePrompt:focus` action
  can be bound to a single chord in `~/.claude/keybindings.json`.
- On the main screen (`CLAUDE_CODE_NO_FLICKER=0`) `/commits` says so and opens
  nothing. In the fullscreen layout the engine docks the pane beside the
  transcript from 110 columns and seats it inline above the prompt below that.
- Diff lines are drawn as plain rows with the mod's own colouring, not the
  engine's highlighter, so the content window can be sliced by row and a
  drag can be mapped to lines. On VS Code and mobile, which have no `Client`,
  the rows are drawn without the region and cannot be dragged over.
- The terminal's own selection never reaches the mod: a drag in the region is
  the mod's, and copy-on-select does not fire there.
- No comments typed in the pane yet; `a` attaches the whole commit.
- A file's hunks are cut at 10000 characters, the pane's `Code` limit; an
  attached commit is cut at 60000.
- Nothing is kept across `/clear`, `/resume` or a restart. A fresh load of
  the module (a reload, a worker respawn) closes a pane the engine still
  shows and strips stale commit references from the box.
- The listing does not refresh when the model commits; `/commits` twice
  reloads it.

## Development

    CLAUDE_CODE_ENABLE_FUNCTION_HOOKS=1 claude --plugin-dir mods/commits
    claude plugin validate mods/commits
    claude plugin test mods/commits
    npx -y -p typescript@5 tsc -p mods/commits --noUnusedLocals

The engine writes the types, and a `tsconfig.json` extending them, beside
the mod each time a session loads it; load it once before type-checking a
fresh clone. Every `$` call the mod makes is in `hooks/register.ts`, bound
at `session.start`.
