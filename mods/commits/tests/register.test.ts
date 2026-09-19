import type {
  Args,
  CommandRunInput,
  On,
  RenderInput,
  SessionStartInput,
  UiScrollInput,
} from 'claude-code'
import { describe, expect, mock, test, tier } from 'claude-code/testing'

import * as Names from '../hooks/names'
import { linesTokenOf, tokenOf } from '../hooks/register'

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

/** The pane with eight body rows: five pinned rows and a three-row window. */
const SHORT_PANE: RenderInput<'Pane'> = {
  ...PANE,
  props: { ...PANE.props, scroll: { offset: 0, bodyRows: 8 } },
}

/** One arrow press down while the pane has the keys. */
const ARROW_DOWN: UiScrollInput = {
  component: 'Pane',
  requestId: Names.PANE_ID,
  offset: 1,
  by: 1,
  bodyRows: 8,
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
  const box = { text: '', cursor: 0, isRefusing: false }
  const clock = mock.clock(on)

  on('session.start', ($, e) => ({ cwd: e.cwd }))
  on('command.register', ($, e) => ({ value: { command: e.name } }))
  on('tool.register', ($, e) => ({
    value: { tool: `mcp__${Names.PLUGIN_NAME}__${e.name}` },
  }))

  on('process.run', ($, e) => {
    runs.push([...e.argv])

    const line = e.argv.join(' ')
    const found = Object.entries(script).find(([key]) => line.includes(key))

    return {
      value: found
        ? { exitCode: 0, stdout: found[1], stderr: '' }
        : { exitCode: 128, stdout: '', stderr: 'fatal: not a git repository' },
    }
  })

  on('ui.open', ($, e) => {
    opens.push(e)
    opened.push(e.id)

    return { value: undefined }
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

  return { runs, opens, opened, closed, focused, scrolled, box, clock }
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
  const own = ['label', 'source']
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

  test('a fresh load closes a pane the engine still shows and strips stale tokens', async ($, on) => {
    const world = worldOf(on)

    world.opened.push(Names.PANE_ID)
    world.box.text = `Look: ${tokenOf('deadbee')} at this`

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
    const world = worldOf(on)

    await $.session.start(SESSION)
    await $.command.run(COMMITS)
    await world.clock.settle()

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
    const world = worldOf(on)

    await $.session.start(SESSION)
    await $.command.run(COMMITS)
    await world.clock.settle()
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
    const world = worldOf(on)

    await $.session.start(SESSION)
    await $.command.run(COMMITS)
    await world.clock.settle()

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
    const world = worldOf(on)

    await $.session.start(SESSION)
    await $.command.run(COMMITS)
    await world.clock.settle()

    const drawn = JSON.stringify(await $.ui.render(PANE))

    expect(drawn).toContain('"key":"commit:aaaaaaa"')
    expect(drawn.match(/"autoFocus":true/g), 'one element seeds the ring').toHaveLength(1)
    expect(drawn).toContain(`"hotkey":"${Names.ASK_HOTKEY}"`)
  })

  test('the arrows scroll the content under the pinned list', async ($, on) => {
    const world = worldOf(on)

    await $.session.start(SESSION)
    await $.command.run(COMMITS)
    await world.clock.settle()

    const before = JSON.stringify(await $.ui.render(SHORT_PANE))

    expect(countIn(before, 'Add the pane'), 'the row and the content head').toBe(2)
    expect(before).toContain('"offset":0')

    expect(await $.ui.scroll(ARROW_DOWN)).toEqual({})
    await world.clock.settle()

    const afterTree = await $.ui.render(SHORT_PANE)
    const after = JSON.stringify(afterTree)

    expect(countIn(after, 'Add the pane'), 'the content head scrolled away').toBe(1)
    expect(textOf(afterTree)).toContain('❯   aaaaaaa Add the pane')
    expect(textOf(afterTree)).toContain('bbbbbbb Start the plugin')
    expect(after).toContain('"offset":1')
    expect(world.scrolled, 'the engine never scrolls the pane itself').toEqual([])

    await $.ui.scroll({ ...ARROW_DOWN, by: -1, offset: 0 })
    await $.ui.scroll({ ...ARROW_DOWN, by: -1, offset: 0 })
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

  test('ask marks the commit and writes its token at once', async ($, on) => {
    const world = worldOf(on)

    await $.session.start(SESSION)
    await $.command.run(COMMITS)
    await world.clock.settle()
    await $.ui.render(FOCUSED_PANE)

    await $.ui.press({ plugin: Names.PLUGIN_NAME, key: 'ask' })
    await world.clock.settle()

    const armed = textOf(await $.ui.render(FOCUSED_PANE))

    expect(armed).toContain('❯ ⧉ aaaaaaa Add the pane')
    expect(armed, 'the help text does not change').toContain(Names.ASK_LABEL)
    expect(world.box.text).toBe(`${tokenOf('aaaaaaa')} `)

    await $.ui.press({ plugin: Names.PLUGIN_NAME, key: 'list-down' })
    await world.clock.settle()
    await $.ui.render(FOCUSED_PANE)
    await $.ui.press({ plugin: Names.PLUGIN_NAME, key: 'ask' })
    await world.clock.settle()

    expect(world.box.text).toBe(`${tokenOf('aaaaaaa')} ${tokenOf('bbbbbbb')} `)
    expect(textOf(await $.ui.render(FOCUSED_PANE))).toContain('  ⧉ aaaaaaa Add the pane')
    expect(textOf(await $.ui.render(FOCUSED_PANE))).toContain('❯ ⧉ bbbbbbb Start the plugin')
  })

  test('the prompt rides an armed commit, once', async ($, on) => {
    const world = worldOf(on)
    const submitted: { text: string; context: readonly string[] | undefined }[] = []

    on('prompt.submit', ($, e) => {
      submitted.push({ text: e.text, context: e.context })

      return { text: e.text, context: e.context }
    })

    await $.session.start(SESSION)
    await $.command.run(COMMITS)
    await world.clock.settle()
    await $.ui.render(FOCUSED_PANE)
    await $.ui.press({ plugin: Names.PLUGIN_NAME, key: 'ask' })
    await world.clock.settle()

    expect(world.box.text).toBe(`${tokenOf('aaaaaaa')} `)

    await $.prompt.submit({
      text: `${world.box.text}Is the split right?`,
      wait: false,
      origin: { kind: 'composer' },
    })

    const [first] = submitted

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

    expect(submitted[1]?.context, 'the second prompt carries nothing').toBeUndefined()
    expect(textOf(await $.ui.render(PANE))).toContain('❯   aaaaaaa Add the pane')
  })

  test('ask again takes a written token back out', async ($, on) => {
    const world = worldOf(on)

    await $.session.start(SESSION)
    await $.command.run(COMMITS)
    await world.clock.settle()
    await $.ui.render(FOCUSED_PANE)

    world.box.text = 'Look: '
    world.box.cursor = world.box.text.length

    await $.ui.press({ plugin: Names.PLUGIN_NAME, key: 'ask' })
    await world.clock.settle()
    await $.ui.render(PANE)

    expect(world.box.text).toBe(`Look: ${tokenOf('aaaaaaa')} `)

    await $.ui.press({ plugin: Names.PLUGIN_NAME, key: 'ask' })
    await world.clock.settle()

    expect(world.box.text).toBe('Look: ')
    expect(textOf(await $.ui.render(PANE))).toContain('❯   aaaaaaa Add the pane')
  })

  test('a token deleted by hand disarms and attaches nothing', async ($, on) => {
    const world = worldOf(on)
    const submitted: (readonly string[] | undefined)[] = []

    on('prompt.submit', ($, e) => {
      submitted.push(e.context)

      return { text: e.text, context: e.context }
    })

    await $.session.start(SESSION)
    await $.command.run(COMMITS)
    await world.clock.settle()
    await $.ui.render(FOCUSED_PANE)
    await $.ui.press({ plugin: Names.PLUGIN_NAME, key: 'ask' })
    await world.clock.settle()
    await $.ui.render(PANE)

    expect(world.box.text).toBe(`${tokenOf('aaaaaaa')} `)

    world.box.text = 'never mind'
    world.box.cursor = world.box.text.length

    // The kit cannot raise prompt.edit; the redraw backstop reads the box.
    expect(textOf(await $.ui.render(FOCUSED_PANE)), 'the gutter follows the box').toContain(
      '❯   aaaaaaa Add the pane',
    )

    await $.prompt.submit({
      text: 'never mind',
      wait: false,
      origin: { kind: 'composer' },
    })

    expect(submitted[0], 'no token, no attachment').toBeUndefined()
  })

  test('two armed commits ride together, and only the tokens are stripped', async ($, on) => {
    const world = worldOf(on)
    const submitted: { text: string; context: readonly string[] | undefined }[] = []

    on('prompt.submit', ($, e) => {
      submitted.push({ text: e.text, context: e.context })

      return { text: e.text, context: e.context }
    })

    await $.session.start(SESSION)
    await $.command.run(COMMITS)
    await world.clock.settle()
    await $.ui.render(FOCUSED_PANE)

    await $.ui.press({ plugin: Names.PLUGIN_NAME, key: 'ask' })
    await world.clock.settle()
    await $.ui.press({ plugin: Names.PLUGIN_NAME, key: 'list-down' })
    await world.clock.settle()
    await $.ui.render(FOCUSED_PANE)
    await $.ui.press({ plugin: Names.PLUGIN_NAME, key: 'ask' })
    await world.clock.settle()
    await $.ui.render(PANE)

    expect(world.box.text).toBe(`${tokenOf('aaaaaaa')} ${tokenOf('bbbbbbb')} `)

    await $.prompt.submit({
      text: world.box.text,
      wait: false,
      origin: { kind: 'composer' },
    })

    const [only] = submitted

    expect(only?.text, 'tokens alone become a line the model can act on').not.toBe('')
    expect(only?.context).toHaveLength(2)
    expect(only?.context?.[0]).toContain(SHA_A)
    expect(only?.context?.[1]).toContain(SHA_B)
  })

  test('closing the pane keeps the token and the commit armed', async ($, on) => {
    const world = worldOf(on)

    await $.session.start(SESSION)
    await $.command.run(COMMITS)
    await world.clock.settle()
    await $.ui.render(FOCUSED_PANE)
    await $.ui.press({ plugin: Names.PLUGIN_NAME, key: 'ask' })
    await world.clock.settle()

    await $.command.run(COMMITS)

    expect(world.box.text).toBe(`${tokenOf('aaaaaaa')} `)

    await $.command.run(COMMITS)
    await world.clock.settle()

    expect(textOf(await $.ui.render(FOCUSED_PANE)), 'still armed on reopen').toContain(
      '❯ ⧉ aaaaaaa Add the pane',
    )
  })

  test('an armed commit whose fill was refused rides anyway', async ($, on) => {
    const world = worldOf(on)
    const submitted: (readonly string[] | undefined)[] = []

    on('prompt.submit', ($, e) => {
      submitted.push(e.context)

      return { text: e.text, context: e.context }
    })

    await $.session.start(SESSION)
    await $.command.run(COMMITS)
    await world.clock.settle()
    await $.ui.render(FOCUSED_PANE)

    world.box.isRefusing = true

    await $.ui.press({ plugin: Names.PLUGIN_NAME, key: 'ask' })
    await world.clock.settle()

    expect(world.box.text, 'the fill was refused').toBe('')
    expect(textOf(await $.ui.render(FOCUSED_PANE)), 'armed all the same').toContain(
      '❯ ⧉ aaaaaaa Add the pane',
    )

    await $.prompt.submit({
      text: 'Go.',
      wait: false,
      origin: { kind: 'composer' },
    })

    expect(submitted[0]?.[0]).toContain(SHA_A)
  })

  test('/clear forgets the armed commits and takes their tokens out', async ($, on) => {
    const world = worldOf(on)

    on('command.run', { command: 'clear' }, () => ({}))

    await $.session.start(SESSION)
    await $.command.run(COMMITS)
    await world.clock.settle()
    await $.ui.render(FOCUSED_PANE)
    await $.ui.press({ plugin: Names.PLUGIN_NAME, key: 'ask' })
    await world.clock.settle()
    await $.ui.render(PANE)

    expect(world.box.text).toBe(`${tokenOf('aaaaaaa')} `)

    await $.command.run({ ...COMMITS, command: 'clear' })

    expect(world.box.text).toBe('')
  })

  test('a drag over diff lines arms them and writes their token', async ($, on) => {
    const world = worldOf(on)
    const submitted: { text: string; context: readonly string[] | undefined }[] = []

    on('prompt.submit', ($, e) => {
      submitted.push({ text: e.text, context: e.context })

      return { text: e.text, context: e.context }
    })

    await $.session.start(SESSION)
    await $.command.run(COMMITS)
    await world.clock.settle()

    const ui = await $.ui.mount({
      plugin: Names.PLUGIN_NAME,
      surface: 'terminal',
      component: 'Pane',
      requestId: Names.PANE_ID,
      props: PANE.props,
      viewport: PANE.viewport,
    })

    await ui.resize({ columns: 78, rows: 26, in: 'diff' })

    expect(await ui.find({ in: 'diff', text: /-const a = 1/ })).toBeDefined()

    // content lines 9 and 10 are the two changed lines of app.ts
    await ui.pointer({ type: 'down', x: 4, y: 9, button: 'left', in: 'diff' })
    await ui.pointer({ type: 'move', x: 4, y: 10, button: 'left', in: 'diff' })
    await ui.pointer({ type: 'up', x: 4, y: 10, button: 'left', in: 'diff' })
    await world.clock.settle()

    const token = linesTokenOf('aaaaaaa', { from: 9, to: 10 })

    expect(world.box.text).toBe(`${token} `)
    expect(await ui.find({ in: 'diff', text: /⧉/ }), 'the armed mark in the gutter').toBeDefined()

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

    const [only] = submitted

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

  test('arming the whole commit drops its armed lines and turns the mark green', async ($, on) => {
    const world = worldOf(on)

    await $.session.start(SESSION)
    await $.command.run(COMMITS)
    await world.clock.settle()

    const ui = await $.ui.mount({
      plugin: Names.PLUGIN_NAME,
      surface: 'terminal',
      component: 'Pane',
      requestId: Names.PANE_ID,
      props: PANE.props,
      viewport: PANE.viewport,
    })

    await ui.resize({ columns: 78, rows: 26, in: 'diff' })

    await ui.pointer({ type: 'down', x: 4, y: 9, button: 'left', in: 'diff' })
    await ui.pointer({ type: 'move', x: 4, y: 10, button: 'left', in: 'diff' })
    await ui.pointer({ type: 'up', x: 4, y: 10, button: 'left', in: 'diff' })
    await world.clock.settle()

    expect(world.box.text).toBe(`${linesTokenOf('aaaaaaa', { from: 9, to: 10 })} `)

    await $.ui.press({ plugin: Names.PLUGIN_NAME, key: 'ask' })
    await world.clock.settle()

    const whole = JSON.stringify(await $.ui.render(PANE))

    expect(whole, 'green once the whole commit is armed').toContain(
      '{"color":"green"},"children":["⧉"]',
    )
    expect(whole).not.toContain('{"color":"yellow"},"children":["⧉"]')
    expect(world.box.text, "the ranges' tokens are gone").toBe(`${tokenOf('aaaaaaa')} `)

    await ui.redraw()

    expect(
      await ui.find({ in: 'diff', text: /⧉/ }),
      'the gutter marks on the lines are gone',
    ).toBeUndefined()

    await ui.unmount()
  })

  test('a click focuses, a drag arms, a click on the armed range disarms', async ($, on) => {
    const world = worldOf(on)

    await $.session.start(SESSION)
    await $.command.run(COMMITS)
    await world.clock.settle()

    const ui = await $.ui.mount({
      plugin: Names.PLUGIN_NAME,
      surface: 'terminal',
      component: 'Pane',
      requestId: Names.PANE_ID,
      props: PANE.props,
      viewport: PANE.viewport,
    })

    await ui.resize({ columns: 78, rows: 26, in: 'diff' })

    await ui.pointer({ type: 'down', x: 4, y: 9, button: 'left', in: 'diff' })
    await ui.pointer({ type: 'up', x: 4, y: 9, button: 'left', in: 'diff' })
    await world.clock.settle()

    expect(world.box.text, 'a plain click arms nothing').toBe('')

    await ui.pointer({ type: 'down', x: 4, y: 9, button: 'left', in: 'diff' })
    await ui.pointer({ type: 'move', x: 5, y: 9, button: 'left', in: 'diff' })
    await ui.pointer({ type: 'up', x: 5, y: 9, button: 'left', in: 'diff' })
    await world.clock.settle()

    expect(world.box.text, 'a one-line drag arms that line').toBe(
      `${linesTokenOf('aaaaaaa', { from: 9, to: 9 })} `,
    )

    await ui.pointer({ type: 'down', x: 4, y: 9, button: 'left', in: 'diff' })
    await ui.pointer({ type: 'up', x: 4, y: 9, button: 'left', in: 'diff' })
    await world.clock.settle()

    expect(world.box.text, 'a click on the armed line takes the token out').toBe('')
    expect(await ui.find({ in: 'diff', text: /⧉/ })).toBeUndefined()

    await ui.unmount()
  })

  test('keys inside the region scroll the window', async ($, on) => {
    const world = worldOf(on)

    await $.session.start(SESSION)
    await $.command.run(COMMITS)
    await world.clock.settle()

    const ui = await $.ui.mount({
      plugin: Names.PLUGIN_NAME,
      surface: 'terminal',
      component: 'Pane',
      requestId: Names.PANE_ID,
      props: SHORT_PANE.props,
      viewport: SHORT_PANE.viewport,
    })

    await ui.resize({ columns: 78, rows: 4, in: 'diff' })

    expect(await ui.find({ in: 'diff', text: /Add the pane/ })).toBeDefined()

    await ui.key({ key: 'down', in: 'diff' })
    await world.clock.settle()

    expect(await ui.find({ in: 'diff', text: /Add the pane/ }), 'the head scrolled away').toBeUndefined()
    expect(await ui.find({ in: 'diff', text: /Ada/ })).toBeDefined()

    await ui.key({ key: 'k', in: 'diff' })
    await world.clock.settle()

    expect(await ui.find({ in: 'diff', text: /Add the pane/ })).toBeDefined()

    await ui.unmount()
  })

  test('an arming and an unfocused redraw at once write the token once', async ($, on) => {
    const world = worldOf(on)

    await $.session.start(SESSION)
    await $.command.run(COMMITS)
    await world.clock.settle()
    await $.ui.render(FOCUSED_PANE)

    await Promise.all([
      $.ui.press({ plugin: Names.PLUGIN_NAME, key: 'ask' }),
      $.ui.render(PANE),
      $.ui.render(PANE),
    ])
    await world.clock.settle()
    await $.ui.render(PANE)

    expect(world.box.text).toBe(`${tokenOf('aaaaaaa')} `)
  })

  test('inside the region, a arms the selected commit and Tab moves between commits', async ($, on) => {
    const world = worldOf(on)

    await $.session.start(SESSION)
    await $.command.run(COMMITS)
    await world.clock.settle()

    const ui = await $.ui.mount({
      plugin: Names.PLUGIN_NAME,
      surface: 'terminal',
      component: 'Pane',
      requestId: Names.PANE_ID,
      props: PANE.props,
      viewport: PANE.viewport,
    })

    await ui.resize({ columns: 78, rows: 26, in: 'diff' })

    await ui.key({ key: 'a', in: 'diff' })
    await world.clock.settle()

    expect(world.box.text).toBe(`${tokenOf('aaaaaaa')} `)

    await ui.key({ key: 'tab', in: 'diff' })
    await world.clock.settle()
    await ui.redraw()

    expect(textOf(await $.ui.render(PANE))).toContain('❯   bbbbbbb Start the plugin')
    expect(await ui.find({ in: 'diff', text: /Start the plugin/ })).toBeDefined()

    await ui.key({ key: 'a', in: 'diff' })
    await world.clock.settle()

    expect(world.box.text).toBe(`${tokenOf('aaaaaaa')} ${tokenOf('bbbbbbb')} `)

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
    const world = worldOf(on)
    const submitted: (readonly string[] | undefined)[] = []

    on('prompt.submit', ($, e) => {
      submitted.push(e.context)

      return { text: e.text, context: e.context }
    })

    await $.session.start(SESSION)
    await $.command.run(COMMITS)
    await world.clock.settle()

    const ui = await $.ui.mount({
      plugin: Names.PLUGIN_NAME,
      surface: 'terminal',
      component: 'Pane',
      requestId: Names.PANE_ID,
      props: PANE.props,
      viewport: PANE.viewport,
    })

    await ui.resize({ columns: 78, rows: 26, in: 'diff' })

    // content line 0 is the subject, 3 is the body line
    await ui.pointer({ type: 'down', x: 4, y: 0, button: 'left', in: 'diff' })
    await ui.pointer({ type: 'move', x: 4, y: 3, button: 'left', in: 'diff' })
    await ui.pointer({ type: 'up', x: 4, y: 3, button: 'left', in: 'diff' })
    await world.clock.settle()

    await $.prompt.submit({
      text: `${world.box.text}Reword?`,
      wait: false,
      origin: { kind: 'composer' },
    })

    const block = submitted[0]?.[0] ?? ''

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
