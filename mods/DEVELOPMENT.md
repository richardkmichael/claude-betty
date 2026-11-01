# Writing Claude Code mods

What was learned building the `commits` mod, collected so a new session can start a different mod
without rediscovering it. Everything was checked against Claude Code 2.1.283: its types, its bundle,
its `plugin-authoring` skill, and a live session for styling, scrolling and selection. The API, type
and tooling claims were rechecked against the 2.1.287 types and skill. Of the live-session
observations, only the focus behaviour was rechecked in a 2.1.287 session: a `$.prompt.fill` made
with the ring on the pane still hands the keys to the composer, and one made from a Client region's
`ui.message` handler still leaves them where they were. The feature is in early access and moves
quickly, so re-check anything that matters against the current types file before relying on it.

## Reference material

- Feature issue, for the announcement, discussion and feedback:
  https://github.com/anthropics/claude-code/issues/91870
- Upstream mods, Anthropic's four shipped mods and their README:
  https://github.com/anthropics/claude-code/tree/main/mods
- Generated types, the authoritative API for every event, method, element and prop:
  `.claude-plugin/types/claude-code/index.d.ts`, which the engine writes beside each mod it loads,
  and the single-file copy the `plugin-authoring` skill extracts.
- The `commits` mod, a working pane mod with a Client region, tests and keys: `mods/commits/`
  and its README.
- Native `/diff` extraction, how the engine's own sidebar does selection, pill and focus:
  `claude-code-analysis/analysis/NATIVE_DIFF_SIDEBAR.md`.
- Panes and composer notes, on placement, the composer pill, drag-to-attach and focus keys:
  `claude-code-analysis/analysis/PLUGIN_PANES_AND_COMPOSER.md`.
- Prettified bundle, the ground truth when the types are silent:
  `claude-code-analysis/work/<version>/package/cli.prettier.js`.
- Claude Code expert, research questions answered from the bundle with citations: the MCP tool
  `mcp__claude-code-expert__ask_claude_code_expert`.
- Keybindings file, for rebinding engine actions a mod's Buttons ride:
  `~/.claude/keybindings.json`.
- The `plugin-authoring` skill that ships with Claude Code, for writing or debugging a mod: where
  the types come from, how to run a plugin under development, and where the engine reports what it
  refused. Load it at the start of a mod session. It extracts a `reference.md`, worked examples (a
  pane, a band, a tool call) and the types, as one file, to
  `/tmp/claude-<uid>/bundled-skills/<version>/<hash>/plugin-authoring/`. Loading it also starts
  the engine's watch on a per-session `~/.claude/dev-mods/<session>/` folder, where a new mod can be
  written and hot-reloaded once the person agrees.

The `claude-code-analysis/` paths above are in the `claude-code-analysis` repository. The expert
tool returns a `session_id`; passing it back continues the same research context.

The upstream mods are marked "All rights reserved". Read them for patterns; do not copy code.

## Enabling and running

Mods need the function-hooks feature, which is off unless the environment variable is set:

```
CLAUDE_CODE_ENABLE_FUNCTION_HOOKS=1 claude --plugin-dir /abs/path/to/mods/<name>
```

The variable can also be set for a project in `.claude/settings.json`, which is simpler for a
repository where mods are developed:

```json
{
  "env": { "CLAUDE_CODE_ENABLE_FUNCTION_HOOKS": "1" }
}
```

- The path is the mod's own directory, the one holding `.claude-plugin/plugin.json`.
- Run it from any other repository by giving the absolute path.
- Starting it from inside another Claude Code session needs `unset CLAUDECODE` first.
- Saving a file hot-reloads the hooks module once the folder has been quiet: a quarter second
  after a single save, or once a run of saves stops. Saves made during the session's own turn
  reload when the turn ends, or sooner when a tool or command the mod registered is about to run,
  so the turn can try what it wrote. A `claude -p` run always loads the mod fresh. The reload
  leaves stale engine state (an open pane, text the mod wrote into the composer), so a mod should
  clean up after itself at `session.start`.
- `CLAUDE_CODE_PLUGIN_DIRS` names the same folders where no flag can be given (a desktop or SDK
  session): absolute paths separated by the platform's path-list separator, read from the
  environment or the `env` block of `~/.claude/settings.json`, never a project's settings.
- Panes dock beside the transcript only in the fullscreen layout. On the main screen
  (`CLAUDE_CODE_NO_FLICKER=0`) a pane opens inline; `e.viewport.isFullscreen` and `command.run`'s
  `presentation.isFullscreen` say which applies.

## Layout of a mod

```
mods/
  .gitignore               ignores */.claude-plugin/types/, */tsconfig.json
  <name>/
    .claude-plugin/plugin.json
    .claude-plugin/types/  written by the engine on each load; never edit
    tsconfig.json          created by the engine if absent; ignored
    hooks/hooks.json       { "modules": ["./register.ts"] }
    hooks/register.ts      export function register(on, options) { ... }
    hooks/*.tsx            view code, and any Client surface modules
    types/index.d.ts       declaration merges, e.g. a tool's input type
    tests/*.test.ts        the testing kit
    README.md
```

There is no command to generate types. Each time a session loads a mod from a folder the person
owns (`--plugin-dir`, `CLAUDE_CODE_PLUGIN_DIRS`, a hot-reloaded folder, a `claude -p` run), the
engine writes `.claude-plugin/types/` beside it:

- `claude-code/index.d.ts`, the plugin API, declaring the modules `claude-code` and
  `claude-code/testing`. Its first line names the Claude Code version that wrote it.
- `claude-code-tools/index.d.ts`, the built-in tools' inputs and results, so a `tool.call` hook
  on `{ tool: 'Bash' }` has `e.command` typed. It lists the tools this build registers on this
  machine.
- `claude-code-mcp/index.d.ts`, the inputs of the MCP tools connected when the mod last loaded.
- One folder per plugin the mod's `plugin.json` lists under `dependencies`, holding that plugin's
  type contract.
- `tsconfig.json`, with the compiler settings, that folder as the only type root, and
  `../../hooks`, `../../types` and `../../tests` included.
- `.gitignore`, ignoring the whole folder.

A mod folder without a `tsconfig.json` also gets one, a single `extends` of that generated config,
so an editor and `tsc -p mods/<name>` type the mod with no step taken. `mods/.gitignore` ignores
both, since the stub would point at a file that exists only after the mod has been loaded. In a
fresh clone, load the mod once (`claude -p --plugin-dir mods/<name> "ok"` is enough) before
type-checking it. `claude plugin validate` and `claude plugin test` do not write the types.

The settings are `jsx: "react"` with `jsxFactory: "h"` and `jsxFragmentFactory: "Fragment"`. JSX
files start with the classic-runtime pragmas (`/* @jsx h */`). Each mod is type-checked on its own
config, against the types the engine last wrote beside it.

A new version can change existing signatures, which breaks the type check while the tests still
pass. From 2.1.277 to 2.1.281, `$.ui.open` went from resolving to nothing to resolving to
`{ isPlaced }`, which broke a wrapper typed `Promise<void>`. From 2.1.283 to 2.1.287,
`ProcessRunResult` gained the required `isStdoutTruncated` and `isStderrTruncated`, which broke a
test's fake `process.run` answer. Load the mod and run the type check after every update.

## Checks

```
claude plugin validate mods/<name>
claude plugin test mods/<name>
npx -y -p typescript@5 tsc -p mods/<name> --noUnusedLocals
```

`claude plugin test` is hidden from help. Never truncate its output.

## The programming model

- `register(on, options)` registers hooks. A hook is `($, e, next)`, Koa-style: call `next(e)` to
  pass on, possibly with a rewritten `e`, or return a result to answer the event.
- `$` is the only door to the engine. Every `$` method is itself an event other plugins can hook.
- New nouns can be added to `$` in the `engine.create` fold, as the upstream `telemetry` mod
  does, shipping a `.d.ts` contract named by `plugin.json`'s `types`.
- A plugin-registered slash command's output is prefixed with the plugin name in the transcript.

Events and methods the `commits` mod uses, as a starting inventory:

| Kind   | Name                                    | Notes                                       |
| ------ | --------------------------------------- | ------------------------------------------- |
| Event  | `session.start`                         | Register commands, tools; clean up          |
| Event  | `command.run`                           | Answer `{ text, context? }`                 |
| Event  | `tool.call`                             | Match full tool name; `{ result }` or deny  |
| Event  | `ui.render` (`component: 'Pane'`)       | Return the pane body tree                   |
| Event  | `ui.focus`, `ui.scroll`, `ui.close`     | Redirect ring landings; own scrolls         |
| Event  | `ui.message`                            | Client region posts; may answer `{ props }` |
| Event  | `prompt.edit`, `prompt.submit`          | Watch the draft; append context on submit   |
| Method | `$.command.register`, `$.tool.register` | Tools show as `mcp__<plugin>__<name>`       |
| Method | `$.process.run`                         | Runs git and other programs                 |
| Method | `$.ui.open`, `close`, `invalidate`      | Pane lifecycle and redraw                   |
| Method | `$.ui.panes`, `$.ui.focus`              | List own panes; move the ring               |
| Method | `$.ui.resolve(e)`                       | The element table for this render's surface |
| Method | `$.ui.log`                              | A line in the transcript                    |
| Method | `$.prompt.read`, `$.prompt.fill`        | Read and write the composer draft           |

A plugin tool has no server-level description. The tool's own description and input schema are
all the model sees before the tool is called. A tool's `tool.call` matcher needs its input type
declared by merging into `McpToolInputs` in `types/index.d.ts`.

## Options

A mod's settings use the plugin `userConfig` mechanism. Read from the 2.1.287 types
(`PluginOptions`), the `plugin-authoring` skill and the upstream `agents-md` manifest; not yet
tried in a mod of this repository.

- `plugin.json` declares the fields under `userConfig`: each with a `type` (string, number,
  boolean or a list of strings), `title`, `description`, `default` and `required`. A string field
  may list `options`, which `/config` draws as a picker.
- The values live in `settings.json` under `pluginConfigs["<plugin>"].options`, sensitive ones in
  secure storage. A `--plugin-dir` mod's key is its `plugin.json` name, or `<name>@inline`.
  Managed settings can set them for an organisation, as upstream does with
  `pluginConfigs["cc-plugin-sec-default@builtin"].options.allowManagedModsOnly`.
- `register(on, options)` receives every field, defaults filled in and validated against its
  type. A required field with no value fails the load and names the field; a string value outside
  its `options` counts as unset, so the default applies.
- Each non-sensitive field is a row in `/config`; a change there reloads the module with the new
  options.
- `test(name, { options }, body)` gives the mod values as if stored in settings; left out, the
  test gets the manifest's defaults.

```json
"userConfig": {
  "instructionFiles": {
    "type": "string",
    "title": "Project instructions",
    "default": "claude-md-or-agents-md",
    "options": ["claude-md", "claude-md-or-agents-md", "claude-md-and-agents-md", "managed-only"]
  }
}
```

## Drawing

### Three layers

1. The pane, owned by the engine. `$.ui.open({ id, title, focus?, closeOnEscape? })` asks for it
   and resolves to `{ isPlaced }`. A pane opened in answer to the person (a command they typed, a
   prompt they entered, a Button, Input or Select they worked) is placed at any width: docked
   beside the transcript from 110 columns, inline above the prompt below that. A pane opened
   unasked (a timer, `session.start`) is placed only from 144 columns, or 110 for an id the
   person opened before, and otherwise waits undrawn with `isPlaced: false`. The engine draws the
   pane's title, tab strip and close mark, handles Escape, `ctrl+x x` and the resize chords, and
   scrolls its body.
2. The body tree, returned by the mod's `ui.render` hook: engine elements laid out by Ink's
   flexbox. The engine redraws it on `$.ui.invalidate('ui.render')`, on resize and on focus
   change.
3. `Client` regions: an element of the tree that hands a rectangle to a separate surface module
   file, which draws its own sub-tree and receives raw pointer and key events.

### Elements

| Element  | Terminal | Desktop | Mobile | VS Code | Notes                                         |
| -------- | -------- | ------- | ------ | ------- | --------------------------------------------- |
| Box      | yes      | yes     | yes    | yes     | Flexbox: padding, margin, border, gap         |
| Text     | yes      | yes     | yes    | yes     | Colour, background, dim, bold, italic, wrap   |
| Button   | yes      | yes     | yes    | yes     | On the ring; `hotkey`, `action`, `plain`      |
| Input    | yes      | yes     | no     | yes     | One-line field on the ring; raises `ui.input` |
| Select   | yes      | yes     | no     | yes     | Picker on the ring                            |
| Link     | yes      | yes     | yes    | yes     | Opens an https URL; not a control             |
| Code     | yes      | yes     | yes    | yes     | Highlighted source or diff; cannot be sliced  |
| Markdown | yes      | yes     | yes    | yes     | Rendered markdown with pressable links        |
| Client   | yes      | yes     | no     | no      | A surface module's region with raw input      |
| Svg      | no       | yes     | yes    | yes     | Vector drawing                                |
| Raster   | yes      | no      | no     | no      | Cell-level drawing                            |
| Image    | yes      | no      | no     | no      | Images in terminals that show them            |

Colours are a theme key or a raw colour. Text and Box accept `hover` overrides. A Button's
`variant: 'primary'` draws it in the `suggestion` theme colour; `role: 'dismiss'` marks the Button
that closes its site, which the desktop draws as its native close control. `plain` wins over both.

### What the engine styles and a mod cannot change

- Docked panes, and the native sidebars, draw every row on a grey backdrop.
- The focused Button, and any Button under the pointer, is drawn inverse.
- A plain Button is drawn as `hotkey: label`, the hotkey in the `suggestion` theme colour, so a
  Button carrying a hotkey always shows it.
- The close mark is the host's own control: inverse on hover, a stop in the focus ring, drawn
  absolutely at the top right over the body's last cells. Leave two cells of right padding on the
  first row so text does not run under it.

### Spacing

In a terminal, padding, margin, a gap and a blank Text row all look the same: empty cells. They
differ only when an element paints a background or border, or when content scrolls (a blank Text
row scrolls away; padding does not). Use `paddingTop` and `gap` rather than blank rows. The native
diff sidebar insets its header with `paddingY: 1`; the pane host adds no inset of its own. Box
borders have no per-side control.

### Client regions

- `<Client key module="./file.tsx" props height />`. `module` must be a string literal; `props`
  must be plain data. Several regions may coexist; none may nest.
- The surface module is `(props, surface) => tree`, with `surface.state`/`setState`,
  `surface.onPointer`, `surface.onKey`, `surface.post`, timers, and its own element table (no
  Client, Raster or Image). It has no `$`.
- `surface.post(data)` raises `ui.message` in the hooks module, which may answer `{ props }` to
  update the region without a full redraw.
- Pointer events: `down`, `move`, `up`, `enter`, `leave`, with region-local cells, the button and
  modifiers. A pointer capture suppresses the terminal's native selection and copy for that drag.
- Keys reach `onKey` only after a click gives the region focus. Escape never arrives; it returns
  focus to the composer.
- Regions do not exist on mobile. A pane that must work there needs a fallback tree.

## Keyboard and focus

- Keyboard focus sits in one place at a time: the composer, one Button, Input or Select on a
  pane's focus ring, or a Client region. The engine moves it on Tab, clicks, `ctrl+x tab`
  (`abovePrompt:focus`, composer to each pane in turn) and Escape.
- The focus ring is the Tab order of a pane's Buttons, Inputs and Selects, wrapping at the ends,
  plus the other panes' tabs and the close mark. `autoFocus` seats it; `$.ui.focus` moves it
  within a pane that already has focus; a `ui.focus` hook may redirect a landing.
- There is no raw key event for the engine tree. A Button's `hotkey` is one digit or lowercase
  letter, pressed only while its pane has focus. A Button's `action` names an engine keybinding
  action and is pressed by that action's chord even from the composer, unless a dialog is up or
  an engine handler of the same action is mounted. Invisible Buttons riding `app:diffFileListUp`
  and `app:diffFileListDown` give a mod ctrl+↑/↓ from anywhere.
- A Button inside a `display: "none"` Box keeps its hotkey in a pane (checked live on 2.1.287).
  That binds a key with nothing drawn and nothing to click: the commits mod's `a` is an empty
  Button hidden that way, with the key's help beside it as Text.
- A press does not report whether it came from the hotkey, Enter or a click, so one Button cannot
  select on Enter and do something else on its hotkey.
- On a pane's ring, the arrows raise `ui.scroll` only while the engine has rows to scroll; with
  nothing to scroll they walk the ring's Buttons, Inputs and Selects, wrapping at the ends. Tab
  also stops on the close mark; the arrows skip it. Answering every person-origin `ui.scroll`
  with `{}` stops the whole pane scrolling.
- `$.ui.focus` waits up to three seconds for a pending focus request or an element not yet drawn
  before it checks the site, so it can follow an open or a redraw directly.
- `$.prompt.fill` releases pane focus. An engine hook (`useReleaseOnEdit`) calls
  `focusPane(null)` whenever the draft changes while a pane holds the keyboard, and no method brings
  focus back: `$.ui.focus` is denied, and `$.ui.open({ focus: true })` is honoured only while the
  draft is empty. A fill made from a Client region's `ui.message` handler does not lose focus,
  because the region holds it in a separate store.
- The native diff sidebar never holds the keyboard and never edits the draft, so it never meets
  this problem. A mod that does not take focus, and drives its pane by global chords and the
  mouse, avoids it too.
- A focused pane handles Tab, Shift+Tab, Enter, Escape, ↑ ↓, PgUp PgDn, Home End, the `ctrl+x`
  chords and its Buttons' hotkeys. Every other key goes to the composer as typing: Space (which
  can never be a hotkey), Backspace, Delete, ← →, punctuation, letters and digits no hotkey
  binds, and ctrl or meta editing chords. The composer applies the key, and the first change to
  the draft releases the pane's focus as `$.prompt.fill` does; a key that changes nothing (← →,
  Backspace on an empty draft) leaves the pane focused. A hotkey ignores Shift: Shift+A presses
  `a`. A mod cannot stop this: a `prompt.edit` hook runs after the release and can only undo the
  edit. Read from the 2.1.286 bundle.
- `abovePrompt:focus` is a `Chat` keybinding action, so one chord in `~/.claude/keybindings.json`
  (`"opt+tab": "abovePrompt:focus"`) can stand in for `ctrl+x tab` to return to a pane.

## The composer

Terms: the composer is the input field; the prompt draft is its text before submission (the
types' `PromptBox`, `{ text, cursor }`); the prompt is what is submitted.

- `$.prompt.fill({ text, mode })` with `replace`, `append` or `insert` writes plain text into the
  prompt draft. It is refused (`isFilled: false`) while a dialog is open. Since 2.1.283 an engine
  refusal carries `refusal`: `dialog`, or `no_composer` where the session has no composer.
- `prompt.edit` fires on each edit the person makes; the testing kit cannot raise it.
- `prompt.submit` may rewrite `text` and append hidden `context` blocks the model receives with
  the prompt.
- A mod cannot create the composer pill the native sidebar shows, nor an attachment. The native
  path stores the selection with `setIdeSelection` and the composer draws it from its
  `leadingPill` prop; neither is exposed. Text a mod writes into the prompt draft to stand for
  what it will send (the commits mod's commit reference) is ordinary draft text, so the mod must
  reconcile it on every edit and strip it on submit.
- Since 2.1.287 a mod can style ranges of the draft. `PromptDecoration` is `{ start, end }` in
  UTF-16 code units plus `color`, `backgroundColor`, `dimColor`, `bold`, `italic`, `underline` and
  `strikethrough`. `$.prompt.fill` takes `decorations`, and a `prompt.edit` answer carries them.
  Each answer's list replaces the last, so a mod re-emits its runs on every edit. It is paint
  only: the styled text is still ordinary draft text.
- A slash command typed while such text is in the prompt draft is sent to the model as a prompt.

## Selections

- No hook or method observes the terminal's text selection. A drag in the transcript, a native panel
  or another mod's pane is invisible to a mod.
- A mod's own Client region sees pointer coordinates for drags inside itself and can map rows to
  its own content. It never receives the selected text from the engine.
- A pointer capture in a region suppresses the terminal's copy-on-select for that drag. Since
  2.1.281 a mod can copy text itself with `$.ui.copy({ text, surface })`, which writes the
  clipboard (the machine's tool, else OSC 52) and resolves to `{ isCopied }`. It writes only; it
  does not read or expose any selection.

## Testing

### The kit

`import { describe, expect, mock, test, tier } from 'claude-code/testing'`; types `Engine` and
`Mounted` come from the same module.

- `tier('user')` seats the plugin as an installed one.
- Tests register `on(...)` hooks beneath the plugin to fake the engine: `process.run`,
  `ui.open`, `prompt.read`, `prompt.fill` (answer `{ isFilled }`), `command.register`,
  `tool.register`, and so on.
- `$.session.start`, `$.command.run`, `$.tool.call`, `$.ui.render`, `$.ui.press`, `$.ui.scroll`,
  `$.prompt.submit` drive the plugin.
- `$.ui.mount(...)` draws a component with its Client regions live, returning a handle with
  `resize`, `pointer`, `key`, `post`, `find`, `redraw` and `unmount`.
- `mock.clock(on)` gives `settle()` for timers and pending work.
- Limits: `prompt.edit` cannot be raised, and `$.ui.focus` is always denied because the mock pane
  never holds the keyboard.
- A refusal cause cannot be simulated. A test's `prompt.fill` hook may answer
  `{ isFilled: false, refusal: 'no_composer' }`, but the engine strips a cause written by a hook,
  so the plugin reads `{ isFilled: false }` and only the refusal itself is testable.

### Live testing in tmux

Tests do not cover focus, placement or colour. Drive a real session in tmux:

```
tmux new-session -d -s modtest -x 160 -y 50 -c /path/to/scratch/repo
tmux send-keys -t modtest "unset CLAUDECODE; export CLAUDE_CODE_ENABLE_FUNCTION_HOOKS=1; \
  claude --plugin-dir /abs/path/to/mods/<name> --model claude-haiku-4-5-20251001" Enter
tmux send-keys -t modtest "/<command>" Enter
tmux capture-pane -t modtest -p          # text
tmux capture-pane -t modtest -e -p       # with colour escapes
```

Mouse events are SGR sequences sent literally, with 1-based column and row:

```
tmux send-keys -t modtest -l "$(printf '\033[<0;60;20M')"    # left press
tmux send-keys -t modtest -l "$(printf '\033[<32;60;22M')"   # drag
tmux send-keys -t modtest -l "$(printf '\033[<0;60;22m')"    # release
tmux send-keys -t modtest -l "$(printf '\033[<35;60;22M')"   # hover
```

Watch for a stray Shift+Tab switching the session into plan mode, and for a pending Claude Code
update that stops panes drawing until the session is restarted. The transcript of a test session
under `~/.claude/projects/<scratch-dir>/` shows the exact context blocks the model received.

## The shipped `diff` mod and the native `/diff`

Two implementations of `/diff` are in the binary.

| Implementation        | Gate                                                  | Default |
| --------------------- | ----------------------------------------------------- | ------- |
| Native sidebar        | on unless the kill switch `tengu_jazzy_ripple` is set | on      |
| `diff` mod (upstream) | the server flag `tengu_quiet_dolphin`                 | off     |

The mod cannot serve `/diff` for a user. `--plugin-dir mods/diff` loads it, and at `session.start`
it calls `$.command.register` for `diff`; the engine refuses ("refused: it is the built-in /diff"),
and the mod matches that wording (`BUILTIN_HOLDS_PATTERN`) and stays idle without logging anything.
Its `command.run` hook then passes `/diff` on to the native sidebar. The native sidebar answers with
a plain "Diff panel shown"; a plugin's output carries its name ("diff: Diff panel shown") and its
pane sits right of a `│` divider, which tells the two apart. To try the mod, change its command to
another name in both `hooks/names/command-name.ts` and the literal `command.run` matcher in
`hooks/register.ts`, for example `diffm`. The server flag is not needed then. It uses only public
methods and hooks; its "ask" attaches a whole file on submit and marks the file `asked ✓` in the
pane, writing nothing to the composer. Its docked pane opens without focus. The native sidebar's
drag-to-attach, pill and attachment are engine code the mod cannot reproduce.

Built-in mods can be given behaviour installed mods do not get. Through 2.1.286, `$.telemetry` was
not in the core types: the built-in `telemetry` plugin added it to `$` for Anthropic's own plugin
analytics and refused callers outside the built-in and core tiers. From 2.1.287 the core types
declare `$.telemetry.log` and `$.telemetry.mark`, but a first-party row is still queued only for
built-ins and the engine, and a collector record reaches the collector only when the engine raised
it. A method `claude-code.d.ts` declares can be called by every plugin, yet still do nothing for an
installed one; read its doc comment, and an upstream mod's README, before relying on it. The
built-in plugins in 2.1.283 are `sec-default`, `agents-md`, `telemetry`, `plugin-authoring`, `tips`,
`mermaid` and `diff`; `tips` draws the spinner tip line through a host interface, not through `$`,
and is off unless the server flag `tengu_tips_mod` is set.

## Known gaps

Found while building the `commits` mod:

- No method returns keyboard focus to a pane after `$.prompt.fill`.
- No selection event, even for a drag inside the mod's own pane.
- No composer pill or attachment method.
- The pane close mark cannot be restyled or taken out of the focus ring.
- Buttons cannot bind arrows or chords of their own.
- The spinner tip line cannot be read, replaced or added to at runtime.
- The upstream `diff` mod cannot be run, and differs from the `/diff` users see.

## Working conventions from the `commits` mod

- Keep names, texts and tool descriptions in one `names.ts`.
- Spell a `command.run` matcher's command as a literal (`{ command: 'commits' }`), not a constant.
  The engine's scan reads literal names to decide which slash commands typed at startup wait for a
  module still loading; a name it cannot read makes every early command wait. `claude plugin
  validate` shows the difference: `command.run{command=commits}` against `command.run{command=?}`.
- A matcher value that is an array matches any of its items (`{ command: ['clear', 'resume'] }`).
- Keep git or other process work in a module that takes a `run` function, so tests can script it.
- Build the view as a pure function of a model object; the render hook only resolves the element
  table and hands it the model. Memoise layout on the identity of the model parts it reads.
- Give the model a hidden orientation note (the `context` of the command or tool result) whenever
  the UI opens, describing what the user can do and what the attachment will look like.
- Wrap context blocks for the model in XML-style elements with attributes for metadata; quoted
  content inside them is unescaped.
- Clean up at `session.start`: close a pane the engine still lists, strip text the mod wrote.

