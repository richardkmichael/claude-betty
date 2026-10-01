import type {
  Args,
  CommandRunInput,
  On,
  PromptDecoration,
  RenderInput,
  SessionStartInput,
  UiScrollInput,
} from 'claude-code'
import { describe, expect, mock, test, tier } from 'claude-code/testing'
import type { Engine, Mounted } from 'claude-code/testing'

import { RANGE_END_MARK, RANGE_RULE, gutterOf } from '../hooks/diff-client'
import * as Names from '../hooks/names'
import {
  commitReferenceDecorationsOf,
  commitReferenceOf,
  commitReferencesIn,
  linesCommitReferenceOf,
  referenceEditOf,
  withoutCommitReference,
  withoutOtherLines,
} from '../hooks/register'

tier('user')

const SESSION: SessionStartInput = {
  surface: 'terminal',
  isInteractive: true,
  cwd: '/work',
}

const COMMITS: CommandRunInput = {
  command: Names.COMMAND_NAME,
  args: '',
  origin: { kind: 'composer' },
  presentation: { isFullscreen: true, columns: 160 },
}

/** The pane docked with room for everything. */
const PANE: RenderInput<'Pane'> = {
  component: 'Pane',
  surface: 'terminal',
  requestId: Names.PANE_ID,
  viewport: { columns: 160, rows: 40 },
  props: {
    title: Names.PANE_TITLE,
    isFocused: false,
    bodyColumns: 80,
    placement: 'dock',
    scroll: { offset: 0, bodyRows: 30 },
    view: {},
  },
}

/** The pane while the person holds its keyboard. */
const FOCUSED_PANE: RenderInput<'Pane'> = {
  ...PANE,
  props: { ...PANE.props, isFocused: true },
}

/** The pane with eleven body rows: six pinned rows and a three-row window inside its border. */
const SHORT_PANE: RenderInput<'Pane'> = {
  ...PANE,
  props: { ...PANE.props, scroll: { offset: 0, bodyRows: 11 } },
}

/** One arrow press down while the pane has the keys. */
const ARROW_DOWN: UiScrollInput = {
  component: 'Pane',
  requestId: Names.PANE_ID,
  offset: 1,
  by: 1,
  bodyRows: 9,
  contentRows: 20,
  origin: { kind: 'person' },
}

const SHA_A = 'a'.repeat(40)
const SHA_B = 'b'.repeat(40)

const LOG = [
  `${SHA_A}\x1faaaaaaa\x1fAda\x1f2026-09-18\x1fAdd the pane\x1fA body line`,
  `${SHA_B}\x1fbbbbbbb\x1fAda\x1f2026-09-17\x1fStart the plugin\x1f`,
]
  .map(record => `${record}\x1e\n`)
  .join('')

const SHOW =
  'diff --git a/old.py b/new.py\n' +
  'similarity index 100%\n' +
  'rename from old.py\n' +
  'rename to new.py\n' +
  'diff --git a/app.ts b/app.ts\n' +
  'index 1..2 100644\n' +
  '--- a/app.ts\n' +
  '+++ b/app.ts\n' +
  '@@ -1 +1 @@\n' +
  '-const a = 1\n' +
  '+const a = 2\n'

/** Git's output in /work for each invocation whose command line holds the key. */
const REPOSITORY: Readonly<Record<string, string>> = {
  'rev-parse --show-toplevel': '/work\n',
  'symbolic-ref': 'origin/main\n',
  'git log': LOG,
  'git show': SHOW,
}

/**
 * The world beneath the plugin: git answering from a script, a prompt box
 * the plugin fills and reads, the panes it opens and closes kept, every
 * other call answered from memory.
 */
function worldOf(
  on: On,
  script: Readonly<Record<string, string>> = REPOSITORY,
) {
  const runs: string[][] = []
  const opens: Args<'ui.open'>[] = []
  const opened: string[] = []
  const closed: string[] = []
  const focused: (string | undefined)[] = []
  const scrolled: number[] = []
  const box = {
    text: '',
    cursor: 0,
    isRefusing: false,
    decorations: [] as readonly PromptDecoration[],
  }
  const submitted: { text: string; context: readonly string[] | undefined }[] = []
  const clock = mock.clock(on)

  on('session.start', ($, e) => ({ cwd: e.cwd }))

  on('prompt.submit', ($, e) => {
    submitted.push({ text: e.text, context: e.context })

    return { text: e.text, context: e.context }
  })
  on('command.register', ($, e) => ({ value: { command: e.name } }))
  on('tool.register', ($, e) => ({
    value: { tool: `mcp__${Names.PLUGIN_NAME}__${e.name}` },
  }))

  on('process.run', ($, e) => {
    runs.push([...e.argv])

    const line = e.argv.join(' ')
    const found = Object.entries(script).find(([key]) => line.includes(key))
    const untruncated = { isStdoutTruncated: false, isStderrTruncated: false }

    return {
      value: found
        ? { exitCode: 0, stdout: found[1], stderr: '', ...untruncated }
        : { exitCode: 128, stdout: '', stderr: 'fatal: not a git repository', ...untruncated },
    }
  })

  on('ui.open', ($, e) => {
    opens.push(e)
    opened.push(e.id)

    return { value: { isPlaced: true as const } }
  })

  on('ui.close', ($, e) => {
    closed.push(e.id)

    return { value: undefined }
  })

  on('ui.invalidate', () => ({ value: undefined }))
  on('ui.log', () => ({ value: undefined }))

  on('ui.panes', () => ({
    value: opened.length > closed.length
      ? [{ id: Names.PANE_ID, title: Names.PANE_TITLE, isShown: true, isFocused: false, isPlaced: true }]
      : [],
  }))

  on('prompt.read', () => ({ value: { ...box } }))

  on('prompt.fill', ($, e) => {
    if (box.isRefusing) {
      return { isFilled: false }
    }

    box.text =
      e.mode === 'append'
        ? `${box.text}${e.text}`
        : e.mode === 'insert'
          ? `${box.text.slice(0, box.cursor)}${e.text}${box.text.slice(box.cursor)}`
          : e.text
    box.cursor = box.text.length
    box.decorations = e.decorations ?? []

    return { isFilled: true }
  })

  on('ui.focus', ($, e) => {
    focused.push(e.element)

    return {}
  })

  on('ui.scroll', ($, e) => {
    scrolled.push(e.by)

    return {}
  })

  return { runs, opens, opened, closed, focused, scrolled, box, submitted, clock }
}

type World = ReturnType<typeof worldOf>

/**
 * The world with the pane opened by `/commits` on the branch and its first
 * diff loaded.
 */
async function openedWorldOf($: Engine, on: On): Promise<World> {
  const world = worldOf(on)

  await $.session.start(SESSION)
  await $.command.run(COMMITS)
  await world.clock.settle()

  return world
}

/** The pane drawn on the terminal, its diff region sized to `rows`. */
async function mountedPaneOf(
  $: Engine,
  pane: RenderInput<'Pane'> = PANE,
  rows = 26,
): Promise<Mounted<'terminal', 'Pane'>> {
  const ui = await $.ui.mount({
    plugin: Names.PLUGIN_NAME,
    surface: 'terminal',
    component: 'Pane',
    requestId: Names.PANE_ID,
    props: pane.props,
    viewport: pane.viewport,
  })

  await ui.resize({ columns: 78, rows, in: 'diff' })

  return ui
}

/** A left-button drag down the diff region's gutter from one row to another. */
async function dragOver(ui: Mounted<'terminal', 'Pane'>, from: number, to: number): Promise<void> {
  await ui.pointer({ type: 'down', x: 4, y: from, button: 'left', in: 'diff' })
  await ui.pointer({ type: 'move', x: 4, y: to, button: 'left', in: 'diff' })
  await ui.pointer({ type: 'up', x: 4, y: to, button: 'left', in: 'diff' })
}

/**
 * A drawn tree's text as it reads: strings, button labels and code sources,
 * in order.
 */
function textOf(node: unknown): string {
  if (typeof node === 'string' || typeof node === 'number') {
    return String(node)
  }

  if (Array.isArray(node)) {
    return node.map(textOf).join('')
  }

  if (typeof node !== 'object' || node === null) {
    return ''
  }

  const props: unknown = Reflect.get(node, 'props')
  const own = ['label']
    .map(name =>
      typeof props === 'object' && props ? Reflect.get(props, name) : undefined,
    )
    .filter((value): value is string => typeof value === 'string')
    .join('')

  return `${own}${textOf(Reflect.get(node, 'children') ?? [])}`
}

const countIn = (text: string, part: string) => text.split(part).length - 1

describe('register', () => {
  test('outside a git repository /commits says so and opens nothing', async ($, on) => {
    const world = worldOf(on, {})

    await $.session.start(SESSION)

    const { text } = await $.command.run(COMMITS)

    expect(text).toBe(Names.NOT_IN_REPOSITORY_TEXT)
    expect(world.opened).toEqual([])
  })

  test('a fresh load closes a pane the engine still shows and strips stale commit references', async ($, on) => {
    const world = worldOf(on)

    world.opened.push(Names.PANE_ID)
    world.box.text = `Look: ${commitReferenceOf('deadbee')} at this`

    await $.session.start(SESSION)

    expect(world.closed).toEqual([Names.PANE_ID])
    expect(world.box.text).toBe('Look: at this')
  })

  test('/commits lists the branch, selects the newest, draws its diff', async ($, on) => {
    const world = worldOf(on)

    await $.session.start(SESSION)

    const { text } = await $.command.run(COMMITS)

    expect(text).toBe(`${Names.PANEL_SHOWN_TEXT}: 2 commits in origin/main..HEAD`)
    expect(world.opened).toEqual([Names.PANE_ID])

    const logRun = world.runs.find(argv => argv[1] === 'log')

    expect(logRun, 'the branch range is what git log lists').toContain(
      'origin/main..HEAD',
    )

    await world.clock.settle()

    const tree = await $.ui.render(PANE)
    const drawn = textOf(tree)
    const json = JSON.stringify(tree)

    expect(drawn).toContain('❯   aaaaaaa Add the pane')
    expect(drawn).toContain('bbbbbbb Start the plugin')
    expect(drawn).toContain(Names.ASK_LABEL)
    expect(drawn).toContain(Names.HELP_TEXT)
    expect(json, "the content is the diff region's").toContain('"module":"hooks/diff-client.tsx"')
    expect(json).toContain('A body line')
    expect(json).toContain('@@ -1 +1 @@')
    expect(json).toContain('+const a = 2')
    expect(json).toContain('(renamed from old.py)')
  })
  test('the content is always drawn past the body, so arrows scroll rather than walk', async ($, on) => {
    const world = worldOf(on)

    await $.session.start(SESSION)
    await $.command.run(COMMITS)

    const marginOf = (tree: unknown) =>
      JSON.stringify(tree).match(/"children":\[" "\]/g)?.length ?? 0

    const loading = JSON.stringify(await $.ui.render(SHORT_PANE))

    await world.clock.settle()

    const loaded = await $.ui.render(SHORT_PANE)

    expect(loading, "the region takes the body's room").toContain('"height":3')
    expect(JSON.stringify(loaded)).toContain('"height":3')
    expect(marginOf(loaded), 'rows past the body, so arrows scroll').toBeGreaterThanOrEqual(8)
  })
  test('the pane opens with the keys and Escape to close', async ($, on) => {
    const world = worldOf(on)

    await $.session.start(SESSION)
    await $.command.run(COMMITS)

    expect(world.opens[0]).toEqual({
      id: Names.PANE_ID,
      title: Names.PANE_TITLE,
      focus: true,
      closeOnEscape: true,
    })
  })

  test('the ring landing on a row selects that commit', async ($, on) => {
    const world = await openedWorldOf($, on)

    expect(textOf(await $.ui.render(PANE))).toContain('❯   aaaaaaa Add the pane')

    expect(
      await $.ui.focus({
        component: 'Pane',
        requestId: Names.PANE_ID,
        plugin: Names.PLUGIN_NAME,
        element: 'commit:bbbbbbb',
        origin: { kind: 'person' },
      }),
    ).toEqual({})

    await world.clock.settle()

    const after = textOf(await $.ui.render(PANE))

    expect(after).toContain('❯   bbbbbbb Start the plugin')
    expect(after).toContain('    aaaaaaa Add the pane')
    expect(after).toContain(Names.ASK_LABEL)

    const shows = world.runs.filter(argv => argv[1] === 'show')

    expect(shows.map(argv => argv.at(-1)), 'both diffs were loaded').toEqual([
      SHA_A,
      SHA_B,
    ])
  })

  test('the ring wraps from the first row to the last and back, past the header', async ($, on) => {
    const world = await openedWorldOf($, on)
    await $.ui.render(FOCUSED_PANE)

    const ringOnto = (element: string | undefined) =>
      $.ui.focus({
        component: 'Pane',
        requestId: Names.PANE_ID,
        ...(element === undefined ? {} : { plugin: Names.PLUGIN_NAME, element }),
        origin: { kind: 'person' },
      })

    expect(await ringOnto('list-down'), 'Shift+Tab off the top').toEqual({})
    expect(world.focused.at(-1), 'redirected to the last row').toBe('commit:bbbbbbb')
    expect(textOf(await $.ui.render(FOCUSED_PANE))).toContain('❯   bbbbbbb Start the plugin')

    expect(await ringOnto(undefined), "Tab off the bottom onto the engine's stop").toEqual({})
    expect(world.focused.at(-1), 'the stop never reached the engine').toBe('commit:bbbbbbb')
    expect(textOf(await $.ui.render(FOCUSED_PANE))).toContain('❯   aaaaaaa Add the pane')
  })

  test("the list chords move the selection from the prompt", async ($, on) => {
    const world = await openedWorldOf($, on)

    const drawn = JSON.stringify(await $.ui.render(PANE))

    expect(drawn).toContain(`"action":"${Names.LIST_DOWN_ACTION}"`)
    expect(drawn).toContain(`"action":"${Names.LIST_UP_ACTION}"`)

    await $.ui.press({ plugin: Names.PLUGIN_NAME, key: 'list-down' })
    await world.clock.settle()

    expect(textOf(await $.ui.render(PANE))).toContain('❯   bbbbbbb Start the plugin')

    await $.ui.press({ plugin: Names.PLUGIN_NAME, key: 'list-down' })
    await world.clock.settle()

    expect(textOf(await $.ui.render(PANE)), 'the end holds').toContain(
      '❯   bbbbbbb Start the plugin',
    )

    await $.ui.press({ plugin: Names.PLUGIN_NAME, key: 'list-up' })
    await world.clock.settle()

    expect(textOf(await $.ui.render(PANE))).toContain('❯   aaaaaaa Add the pane')
  })

  test('the selected row seeds the ring and the ask button has its hotkey', async ($, on) => {
    await openedWorldOf($, on)

    const drawn = JSON.stringify(await $.ui.render(PANE))

    expect(drawn).toContain('"key":"commit:aaaaaaa"')
    expect(drawn.match(/"autoFocus":true/g), 'one element seeds the ring').toHaveLength(1)
    expect(drawn).toContain(`"hotkey":"${Names.ASK_HOTKEY}"`)
  })

  test('the arrows move the selection, and f and b page the content under the pinned list', async ($, on) => {
    const world = await openedWorldOf($, on)

    const before = JSON.stringify(await $.ui.render(SHORT_PANE))

    expect(countIn(before, 'Add the pane'), 'the row and the content head').toBe(2)
    expect(before).toContain('"offset":0')

    expect(await $.ui.scroll(ARROW_DOWN)).toEqual({})
    await world.clock.settle()

    expect(textOf(await $.ui.render(SHORT_PANE))).toContain('❯   bbbbbbb Start the plugin')

    await $.ui.scroll({ ...ARROW_DOWN, by: -1, offset: 0 })
    await world.clock.settle()

    expect(textOf(await $.ui.render(SHORT_PANE))).toContain('❯   aaaaaaa Add the pane')

    await $.ui.press({ plugin: Names.PLUGIN_NAME, key: 'page-forward' })
    await world.clock.settle()

    const pagedTree = await $.ui.render(SHORT_PANE)
    const paged = JSON.stringify(pagedTree)

    expect(countIn(paged, 'Add the pane'), 'the content head paged away').toBe(1)
    expect(textOf(pagedTree), 'the selection stays').toContain('❯   aaaaaaa Add the pane')
    expect(paged).not.toContain('"offset":0')

    await $.ui.press({ plugin: Names.PLUGIN_NAME, key: 'page-back' })
    await world.clock.settle()

    expect(JSON.stringify(await $.ui.render(SHORT_PANE))).toContain('"offset":0')
    expect(world.scrolled, 'the engine never scrolls the pane itself').toEqual([])
  })
  test('the model closes the pane', async ($, on) => {
    const world = worldOf(on)

    await $.session.start(SESSION)

    const unopened = await $.tool.call({ tool: Names.CLOSE_TOOL_FULL_NAME })

    expect(String(unopened.result)).toContain('was not open')

    await $.command.run(COMMITS)

    const closed = await $.tool.call({ tool: Names.CLOSE_TOOL_FULL_NAME })

    expect(String(closed.result)).toContain('Closed')
    expect(world.closed).toEqual([Names.PANE_ID])
  })

  test('/commits again hides the pane', async ($, on) => {
    const world = worldOf(on)

    await $.session.start(SESSION)
    await $.command.run(COMMITS)

    const { text } = await $.command.run(COMMITS)

    expect(text).toBe(Names.PANEL_HIDDEN_TEXT)
    expect(world.closed).toEqual([Names.PANE_ID])
  })

  test('the model shows named commits, each with its note', async ($, on) => {
    const world = worldOf(on)

    await $.session.start(SESSION)

    const answer = await $.tool.call({
      tool: Names.TOOL_FULL_NAME,
      commits: [{ sha: 'bbbbbbb', note: 'Is this split right?' }],
    })

    expect(answer.deny).toBeUndefined()
    expect(String(answer.result)).toContain('Showing 2 commits (1 commit)')
    expect(world.opened).toEqual([Names.PANE_ID])

    const logRun = world.runs.find(argv => argv[1] === 'log')

    expect(logRun).toContain('--no-walk=unsorted')
    expect(logRun).toContain('bbbbbbb')

    await world.clock.settle()

    const drawn = textOf(await $.ui.render(PANE))

    expect(drawn).toContain('note: Is this split right?')
  })

  test('the model is refused a range that is not one', async ($, on) => {
    worldOf(on)

    await $.session.start(SESSION)

    const answer = await $.tool.call({
      tool: Names.TOOL_FULL_NAME,
      range: '--output=/tmp/x',
    })

    expect(answer.deny).toContain('not a revision range')
  })

  test('ask marks the commit and writes its commit reference at once', async ($, on) => {
    const world = await openedWorldOf($, on)
    await $.ui.render(FOCUSED_PANE)

    await $.ui.press({ plugin: Names.PLUGIN_NAME, key: 'ask' })
    await world.clock.settle()

    const included = textOf(await $.ui.render(FOCUSED_PANE))

    expect(included).toContain('❯ ⧉ aaaaaaa Add the pane')
    expect(included, 'the help text does not change').toContain(Names.ASK_LABEL)
    expect(world.box.text, 'the short sha alone names the commit').toBe('⧉ aaaaaaa ')

    await $.ui.press({ plugin: Names.PLUGIN_NAME, key: 'list-down' })
    await world.clock.settle()
    await $.ui.render(FOCUSED_PANE)
    await $.ui.press({ plugin: Names.PLUGIN_NAME, key: 'ask' })
    await world.clock.settle()

    expect(world.box.text).toBe(`${commitReferenceOf('aaaaaaa')} ${commitReferenceOf('bbbbbbb')} `)
    expect(textOf(await $.ui.render(FOCUSED_PANE))).toContain('  ⧉ aaaaaaa Add the pane')
    expect(textOf(await $.ui.render(FOCUSED_PANE))).toContain('❯ ⧉ bbbbbbb Start the plugin')
  })

  test('every written commit reference is painted, its space not', async ($, on) => {
    const world = await openedWorldOf($, on)
    const first = commitReferenceOf('aaaaaaa')
    const second = commitReferenceOf('bbbbbbb')
    await $.ui.render(FOCUSED_PANE)

    await $.ui.press({ plugin: Names.PLUGIN_NAME, key: 'ask' })
    await world.clock.settle()

    expect(world.box.decorations).toEqual([{ start: 0, end: first.length, color: 'green' }])

    await $.ui.press({ plugin: Names.PLUGIN_NAME, key: 'list-down' })
    await world.clock.settle()
    await $.ui.render(FOCUSED_PANE)
    await $.ui.press({ plugin: Names.PLUGIN_NAME, key: 'ask' })
    await world.clock.settle()

    const secondStart = first.length + 1

    expect(world.box.decorations, 'a fill replaces the runs, so both are painted').toEqual([
      { start: 0, end: first.length, color: 'green' },
      { start: secondStart, end: secondStart + second.length, color: 'green' },
    ])
  })

  test('only the commit references named are painted, green for a commit and yellow for lines', async () => {
    const mine = commitReferenceOf('aaaaaaa')
    const lines = linesCommitReferenceOf('bbbbbbb', { from: 2, to: 4 })
    const text = `see ${mine} and ⧉ fffffff and ${lines}`
    const linesStart = text.indexOf(lines)

    expect(commitReferenceDecorationsOf(text, new Set([mine, lines]))).toEqual([
      { start: 4, end: 4 + mine.length, color: 'green' },
      { start: linesStart, end: linesStart + lines.length, color: 'yellow' },
    ])
  })

  test('a commit reference is found whole, never as the start of a longer one', async () => {
    const short = linesCommitReferenceOf('aaaaaaa', { from: 9, to: 10 })
    const long = linesCommitReferenceOf('aaaaaaa', { from: 9, to: 100 })
    const whole = commitReferenceOf('aaaaaaa')
    const text = `${long} ${short} why?`

    expect(commitReferencesIn(text)).toEqual([long, short])
    expect(commitReferencesIn(text), 'the whole commit is not named').not.toContain(whole)
    expect(withoutCommitReference(text, short)).toBe(`${long} why?`)
    expect(withoutCommitReference(text, whole), 'nothing to strip').toBe(text)
  })

  test('Opt+Backspace makes a range of lines the whole commit, then removes it', async () => {
    const lines = linesCommitReferenceOf('aaaaaaa', { from: 9, to: 10 })
    const whole = commitReferenceOf('bbbbbbb')
    const text = `why ${lines} and ${whole} more`
    const written = new Set([lines, whole])
    const linesStart = text.indexOf(lines)
    const wholeStart = text.indexOf(whole)
    const optBackspaceAt = (cursor: number) => ({
      key: { key: 'backspace', meta: true as const },
      text,
      cursor,
      start: cursor - 1,
      end: cursor,
      inputText: '',
    })

    expect(referenceEditOf(optBackspaceAt(linesStart + lines.length + 1), written)).toEqual({
      kind: 'whole',
      lines,
      start: linesStart,
      end: linesStart + lines.length + 1,
      commitReference: commitReferenceOf('aaaaaaa'),
    })
    expect(referenceEditOf(optBackspaceAt(wholeStart + 3), written), 'the cursor inside').toEqual({
      kind: 'remove',
      start: wholeStart,
      end: wholeStart + whole.length + 1,
    })
    expect(
      referenceEditOf({ ...optBackspaceAt(wholeStart + 3), key: { key: 'backspace', shift: true } }, written),
      'Shift+Backspace does the same',
    ).toEqual({ kind: 'remove', start: wholeStart, end: wholeStart + whole.length + 1 })
    expect(
      referenceEditOf({ ...optBackspaceAt(linesStart + lines.length), key: { key: 'backspace' } }, written),
      'a plain Backspace edits a character',
    ).toBeNull()
    expect(referenceEditOf(optBackspaceAt(3), written), 'away from a reference').toBeNull()
    expect(referenceEditOf(optBackspaceAt(wholeStart + 3), new Set([lines])), 'not written').toBeNull()
  })

  test('an arrow that would land inside a commit reference lands at its edge', async () => {
    const whole = commitReferenceOf('bbbbbbb')
    const text = `see ${whole} here`
    const start = text.indexOf(whole)
    const end = start + whole.length
    const moveTo = (cursor: number, at: number) => ({ text, cursor, start: at, end: at, inputText: '' })
    const written = new Set([whole])

    expect(referenceEditOf(moveTo(start, start + 1), written), '→ jumps past it').toEqual({ kind: 'move', at: end })
    expect(referenceEditOf(moveTo(end, end - 1), written), '← jumps before it').toEqual({ kind: 'move', at: start })
    expect(referenceEditOf(moveTo(end, end + 1), written), 'outside it').toBeNull()
  })

  test("including the whole commit takes the commit's other ranges out of the box", async () => {
    const whole = commitReferenceOf('aaaaaaa')
    const first = linesCommitReferenceOf('aaaaaaa', { from: 1, to: 2 })
    const second = linesCommitReferenceOf('aaaaaaa', { from: 5, to: 6 })
    const other = linesCommitReferenceOf('bbbbbbb', { from: 1, to: 2 })
    const text = `${first} x ${whole} ${second} ${other} y`

    expect(withoutOtherLines({ text, cursor: text.length }, whole)).toEqual({
      text: `x ${whole} ${other} y`,
      cursor: text.length - (first.length + 1) - (second.length + 1),
    })
  })

  test('the prompt rides an included commit, once', async ($, on) => {
    const world = await openedWorldOf($, on)
    await $.ui.render(FOCUSED_PANE)
    await $.ui.press({ plugin: Names.PLUGIN_NAME, key: 'ask' })
    await world.clock.settle()

    expect(world.box.text).toBe(`${commitReferenceOf('aaaaaaa')} `)

    await $.prompt.submit({
      text: `${world.box.text}Is the split right?`,
      wait: false,
      origin: { kind: 'composer' },
    })

    const [first] = world.submitted

    expect(first?.text).toBe('Is the split right?')
    expect(first?.context?.[0]).toContain(`${Names.ATTACHED_LEAD} aaaaaaa`)
    expect(first?.context?.[0]).toContain(`<commit sha="${SHA_A}" author="Ada" date="2026-09-18">`)
    expect(first?.context?.[0]).toContain('<commit-subject>Add the pane</commit-subject>')
    expect(first?.context?.[0]).toContain('<commit-message>\nA body line\n</commit-message>')
    expect(first?.context?.[0]).toContain('<commit-diff path="app.ts">\n@@ -1 +1 @@')
    expect(first?.context?.[0]).toContain('<commit-diff path="new.py" note="renamed from old.py"/>')
    expect(first?.context?.[0]).toContain('</commit-diff>\n</commit>')

    await $.prompt.submit({
      text: 'And this?',
      wait: false,
      origin: { kind: 'composer' },
    })

    expect(world.submitted[1]?.context, 'the second prompt carries nothing').toBeUndefined()
    expect(textOf(await $.ui.render(PANE))).toContain('❯   aaaaaaa Add the pane')
  })

  test('ask again takes a written commit reference back out', async ($, on) => {
    const world = await openedWorldOf($, on)
    await $.ui.render(FOCUSED_PANE)

    world.box.text = 'Look: '
    world.box.cursor = world.box.text.length

    await $.ui.press({ plugin: Names.PLUGIN_NAME, key: 'ask' })
    await world.clock.settle()
    await $.ui.render(PANE)

    expect(world.box.text).toBe(`Look: ${commitReferenceOf('aaaaaaa')} `)

    await $.ui.press({ plugin: Names.PLUGIN_NAME, key: 'ask' })
    await world.clock.settle()

    expect(world.box.text).toBe('Look: ')
    expect(textOf(await $.ui.render(PANE))).toContain('❯   aaaaaaa Add the pane')
  })

  test('a commit reference deleted by hand excludes and attaches nothing', async ($, on) => {
    const world = await openedWorldOf($, on)
    await $.ui.render(FOCUSED_PANE)
    await $.ui.press({ plugin: Names.PLUGIN_NAME, key: 'ask' })
    await world.clock.settle()
    await $.ui.render(PANE)

    expect(world.box.text).toBe(`${commitReferenceOf('aaaaaaa')} `)

    world.box.text = 'never mind'
    world.box.cursor = world.box.text.length

    // The kit cannot raise prompt.edit; a redraw while the composer holds the
    // keys reads the box instead.
    expect(textOf(await $.ui.render(PANE)), 'the gutter follows the box').toContain(
      '❯   aaaaaaa Add the pane',
    )

    await $.prompt.submit({
      text: 'never mind',
      wait: false,
      origin: { kind: 'composer' },
    })

    expect(world.submitted[0]?.context, 'no commit reference, no attachment').toBeUndefined()
  })

  test('two included commits ride together, and only the commit references are stripped', async ($, on) => {
    const world = await openedWorldOf($, on)
    await $.ui.render(FOCUSED_PANE)

    await $.ui.press({ plugin: Names.PLUGIN_NAME, key: 'ask' })
    await world.clock.settle()
    await $.ui.press({ plugin: Names.PLUGIN_NAME, key: 'list-down' })
    await world.clock.settle()
    await $.ui.render(FOCUSED_PANE)
    await $.ui.press({ plugin: Names.PLUGIN_NAME, key: 'ask' })
    await world.clock.settle()
    await $.ui.render(PANE)

    expect(world.box.text).toBe(`${commitReferenceOf('aaaaaaa')} ${commitReferenceOf('bbbbbbb')} `)

    await $.prompt.submit({
      text: world.box.text,
      wait: false,
      origin: { kind: 'composer' },
    })

    const [only] = world.submitted

    expect(only?.text, 'commit references alone become a line the model can act on').not.toBe('')
    expect(only?.context).toHaveLength(2)
    expect(only?.context?.[0]).toContain(SHA_A)
    expect(only?.context?.[1]).toContain(SHA_B)
  })

  test('closing the pane keeps the commit reference and the commit included', async ($, on) => {
    const world = await openedWorldOf($, on)
    await $.ui.render(FOCUSED_PANE)
    await $.ui.press({ plugin: Names.PLUGIN_NAME, key: 'ask' })
    await world.clock.settle()

    await $.command.run(COMMITS)

    expect(world.box.text).toBe(`${commitReferenceOf('aaaaaaa')} `)

    await $.command.run(COMMITS)
    await world.clock.settle()

    expect(textOf(await $.ui.render(FOCUSED_PANE)), 'still included on reopen').toContain(
      '❯ ⧉ aaaaaaa Add the pane',
    )
  })

  test('an included commit whose fill was refused rides anyway', async ($, on) => {
    const world = await openedWorldOf($, on)
    await $.ui.render(FOCUSED_PANE)

    world.box.isRefusing = true

    await $.ui.press({ plugin: Names.PLUGIN_NAME, key: 'ask' })
    await world.clock.settle()

    expect(world.box.text, 'the fill was refused').toBe('')
    expect(textOf(await $.ui.render(FOCUSED_PANE)), 'included all the same').toContain(
      '❯ ⧉ aaaaaaa Add the pane',
    )

    await $.prompt.submit({
      text: 'Go.',
      wait: false,
      origin: { kind: 'composer' },
    })

    expect(world.submitted[0]?.context?.[0]).toContain(SHA_A)
  })

  test('a commit reference a refused fill left behind is written at the next redraw', async ($, on) => {
    const world = await openedWorldOf($, on)
    await $.ui.render(FOCUSED_PANE)

    world.box.isRefusing = true

    await $.ui.press({ plugin: Names.PLUGIN_NAME, key: 'ask' })
    await world.clock.settle()

    expect(world.box.text, 'the fill was refused').toBe('')

    world.box.isRefusing = false

    await $.ui.render(PANE)
    await world.clock.settle()

    expect(world.box.text, 'written once the box takes it').toBe(`${commitReferenceOf('aaaaaaa')} `)
  })

  test('/clear forgets the included commits and takes their commit references out', async ($, on) => {
    const world = worldOf(on)

    on('command.run', { command: 'clear' }, () => ({}))

    await $.session.start(SESSION)
    await $.command.run(COMMITS)
    await world.clock.settle()
    await $.ui.render(FOCUSED_PANE)
    await $.ui.press({ plugin: Names.PLUGIN_NAME, key: 'ask' })
    await world.clock.settle()
    await $.ui.render(PANE)

    expect(world.box.text).toBe(`${commitReferenceOf('aaaaaaa')} `)

    await $.command.run({ ...COMMITS, command: 'clear' })

    expect(world.box.text).toBe('')
  })

  test('a drag over diff lines includes them and writes their commit reference', async ($, on) => {
    const world = await openedWorldOf($, on)

    const ui = await mountedPaneOf($)

    expect(await ui.find({ in: 'diff', text: /-const a = 1/ })).toBeDefined()

    // content lines 9 and 10 are the two changed lines of app.ts
    await dragOver(ui, 9, 10)
    await world.clock.settle()

    const commitReference = linesCommitReferenceOf('aaaaaaa', { from: 9, to: 10 })

    expect(world.box.text).toBe(`${commitReference} `)
    expect(await ui.find({ in: 'diff', text: /⧉/ }), 'the included mark in the gutter').toBeDefined()

    const partly = JSON.stringify(await $.ui.render(PANE))

    expect(partly, "the row's mark is yellow for lines only").toContain(
      '{"color":"yellow"},"children":["⧉"]',
    )
    expect(partly).not.toContain('{"color":"green"},"children":["⧉"]')

    await $.prompt.submit({
      text: `${world.box.text}Why this change?`,
      wait: false,
      origin: { kind: 'composer' },
    })

    const [only] = world.submitted

    expect(only?.text).toBe('Why this change?')
    expect(only?.context?.[0]).toContain(`${Names.ATTACHED_LEAD_LINES} lines of commit aaaaaaa`)
    expect(only?.context?.[0]).toContain(
      [
        '<commit-lines sha="aaaaaaa" subject="Add the pane">',
        '<commit-diff path="app.ts" new-rev="aaaaaaa" new-lines="1" old-rev="aaaaaaa^" old-lines="1">',
        '-const a = 1',
        '+const a = 2',
        '</commit-diff>',
        '</commit-lines>',
      ].join('\n'),
    )
    expect(only?.context?.[0], 'the legend explains the line attributes').toContain('new-lines are')
    expect(only?.context?.[0]).not.toContain('@@')

    await ui.unmount()
  })

  test('an included range is marked at its ends, with a dashed rule between', async () => {
    const included = [{ from: 3, to: 6 }, { from: 9, to: 9 }]

    expect([2, 3, 4, 5, 6, 7, 9].map(at => gutterOf(at, included))).toEqual([
      '  ',
      RANGE_END_MARK,
      RANGE_RULE,
      RANGE_RULE,
      RANGE_END_MARK,
      '  ',
      RANGE_END_MARK,
    ])
    expect(gutterOf(4, [{ from: 4, to: 5 }]), 'two lines are both ends').toBe(RANGE_END_MARK)
  })

  test('a range edited back into shape is included again, and one being edited stays', async ($, on) => {
    const world = await openedWorldOf($, on)
    const ui = await mountedPaneOf($)

    await dragOver(ui, 9, 10)
    await world.clock.settle()

    const edit = async (text: string, cursor = text.length) => {
      world.box.text = text
      world.box.cursor = cursor
      await $.ui.render(PANE)
      await world.clock.settle()
    }
    const includedRanges = async () =>
      /"included":(\[[^\]]*\])/.exec(JSON.stringify(await $.ui.render(PANE)))?.[1]

    await edit('⧉ aaaaaaa:9-')

    expect(world.box.text, 'a half-edited reference stays').toBe('⧉ aaaaaaa:9-')
    expect(await includedRanges(), 'its range is no longer included').toBe('[]')

    await edit('⧉ aaaaaaa:9-9')

    expect(world.box.text, 'still being typed').toBe('⧉ aaaaaaa:9-9')

    await edit('⧉ aaaaaaa:9-9 ')

    expect(world.box.text).toBe('⧉ aaaaaaa:9-9 ')
    expect(world.box.decorations, 'painted yellow again').toEqual([
      { start: 0, end: '⧉ aaaaaaa:9-9'.length, color: 'yellow' },
    ])
    expect(await includedRanges(), 'the line is marked').toBe('[{"from":9,"to":9}]')

    await edit('why ⧉ aaaaaaa:9-999 ', 0)

    expect(world.box.text, 'a range past the content stays text').toBe('why ⧉ aaaaaaa:9-999 ')

    await ui.unmount()
  })

  test('including the whole commit drops its included lines and turns the mark green', async ($, on) => {
    const world = await openedWorldOf($, on)

    const ui = await mountedPaneOf($)

    await dragOver(ui, 9, 10)
    await world.clock.settle()

    expect(world.box.text).toBe(`${linesCommitReferenceOf('aaaaaaa', { from: 9, to: 10 })} `)

    await $.ui.press({ plugin: Names.PLUGIN_NAME, key: 'ask' })
    await world.clock.settle()

    const whole = JSON.stringify(await $.ui.render(PANE))

    expect(whole, 'green once the whole commit is included').toContain(
      '{"color":"green"},"children":["⧉"]',
    )
    expect(whole).not.toContain('{"color":"yellow"},"children":["⧉"]')
    expect(world.box.text, "the ranges' commit references are gone").toBe(`${commitReferenceOf('aaaaaaa')} `)

    await ui.redraw()

    expect(
      await ui.find({ in: 'diff', text: /⧉/ }),
      'the gutter marks on the lines are gone',
    ).toBeUndefined()

    await ui.unmount()
  })

  test('a click focuses, a drag includes, a click on the included range excludes', async ($, on) => {
    const world = await openedWorldOf($, on)

    const ui = await mountedPaneOf($)

    await ui.pointer({ type: 'down', x: 4, y: 9, button: 'left', in: 'diff' })
    await ui.pointer({ type: 'up', x: 4, y: 9, button: 'left', in: 'diff' })
    await world.clock.settle()

    expect(world.box.text, 'a plain click includes nothing').toBe('')

    await ui.pointer({ type: 'down', x: 4, y: 9, button: 'left', in: 'diff' })
    await ui.pointer({ type: 'move', x: 5, y: 9, button: 'left', in: 'diff' })
    await ui.pointer({ type: 'up', x: 5, y: 9, button: 'left', in: 'diff' })
    await world.clock.settle()

    expect(world.box.text, 'a one-line drag includes that line').toBe(
      `${linesCommitReferenceOf('aaaaaaa', { from: 9, to: 9 })} `,
    )

    await ui.pointer({ type: 'down', x: 4, y: 9, button: 'left', in: 'diff' })
    await ui.pointer({ type: 'up', x: 4, y: 9, button: 'left', in: 'diff' })
    await world.clock.settle()

    expect(world.box.text, 'a click on the included line takes the commit reference out').toBe('')
    expect(await ui.find({ in: 'diff', text: /⧉/ })).toBeUndefined()

    await ui.unmount()
  })

  test('keys inside the region scroll the window, and the arrows move the selection', async ($, on) => {
    const world = await openedWorldOf($, on)

    const ui = await mountedPaneOf($, SHORT_PANE, 4)

    expect(await ui.find({ in: 'diff', text: /Add the pane/ })).toBeDefined()

    await ui.key({ key: 'j', in: 'diff' })
    await world.clock.settle()

    expect(await ui.find({ in: 'diff', text: /Add the pane/ }), 'the head scrolled away').toBeUndefined()
    expect(await ui.find({ in: 'diff', text: /Ada/ })).toBeDefined()

    await ui.key({ key: 'k', in: 'diff' })
    await world.clock.settle()

    expect(await ui.find({ in: 'diff', text: /Add the pane/ })).toBeDefined()

    await ui.key({ key: 'f', in: 'diff' })
    await world.clock.settle()

    expect(await ui.find({ in: 'diff', text: /Add the pane/ }), 'f pages forward').toBeUndefined()

    await ui.key({ key: 'b', in: 'diff' })
    await world.clock.settle()

    expect(await ui.find({ in: 'diff', text: /Add the pane/ }), 'b pages back').toBeDefined()

    await ui.key({ key: 'down', in: 'diff' })
    await world.clock.settle()

    expect(textOf(await $.ui.render(SHORT_PANE))).toContain('❯   bbbbbbb Start the plugin')

    await ui.unmount()
  })

  test('an inclusion and an unfocused redraw at once write the commit reference once', async ($, on) => {
    const world = await openedWorldOf($, on)
    await $.ui.render(FOCUSED_PANE)

    await Promise.all([
      $.ui.press({ plugin: Names.PLUGIN_NAME, key: 'ask' }),
      $.ui.render(PANE),
      $.ui.render(PANE),
    ])
    await world.clock.settle()
    await $.ui.render(PANE)

    expect(world.box.text).toBe(`${commitReferenceOf('aaaaaaa')} `)
  })

  test('inside the region, the a key includes the selected commit and Tab moves between commits', async ($, on) => {
    const world = await openedWorldOf($, on)

    const ui = await mountedPaneOf($)

    await ui.key({ key: 'a', in: 'diff' })
    await world.clock.settle()

    expect(world.box.text).toBe(`${commitReferenceOf('aaaaaaa')} `)

    await ui.key({ key: 'tab', in: 'diff' })
    await world.clock.settle()
    await ui.redraw()

    expect(textOf(await $.ui.render(PANE))).toContain('❯   bbbbbbb Start the plugin')
    expect(await ui.find({ in: 'diff', text: /Start the plugin/ })).toBeDefined()

    await ui.key({ key: 'a', in: 'diff' })
    await world.clock.settle()

    expect(world.box.text).toBe(`${commitReferenceOf('aaaaaaa')} ${commitReferenceOf('bbbbbbb')} `)

    await ui.key({ key: 'tab', shift: true, in: 'diff' })
    await world.clock.settle()
    await ui.redraw()

    expect(textOf(await $.ui.render(PANE))).toContain('❯ ⧉ aaaaaaa Add the pane')

    await ui.key({ key: 'up', ctrl: true, in: 'diff' })
    await world.clock.settle()

    expect(textOf(await $.ui.render(PANE)), 'ctrl+up stops at the first row').toContain(
      '❯ ⧉ aaaaaaa Add the pane',
    )

    await ui.key({ key: 'tab', shift: true, in: 'diff' })
    await world.clock.settle()

    expect(textOf(await $.ui.render(PANE)), 'Shift+Tab wraps to the last row').toContain(
      '❯ ⧉ bbbbbbb Start the plugin',
    )

    await ui.key({ key: 'tab', in: 'diff' })
    await world.clock.settle()

    expect(textOf(await $.ui.render(PANE)), 'Tab wraps to the first row').toContain(
      '❯ ⧉ aaaaaaa Add the pane',
    )

    await ui.unmount()
  })

  test('lines from the message are headed as such, and the subject line by itself', async ($, on) => {
    const world = await openedWorldOf($, on)

    const ui = await mountedPaneOf($)

    // content line 0 is the subject, 3 is the body line
    await dragOver(ui, 0, 3)
    await world.clock.settle()

    await $.prompt.submit({
      text: `${world.box.text}Reword?`,
      wait: false,
      origin: { kind: 'composer' },
    })

    const block = world.submitted[0]?.context?.[0] ?? ''

    expect(block).toContain(
      [
        '<commit-lines sha="aaaaaaa" subject="Add the pane">',
        '<commit-subject>Add the pane</commit-subject>',
        '<commit-author>Ada, 2026-09-18</commit-author>',
        '<commit-message>',
        'A body line',
        '</commit-message>',
        '</commit-lines>',
      ].join('\n'),
    )
    expect(block, 'no diff lines, so no legend').not.toContain('new-lines are')

    await ui.unmount()
  })
})
