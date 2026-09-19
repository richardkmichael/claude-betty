import type {
  CommandSpec,
  On,
  PaneCloseArgs,
  PaneOpenArgs,
  ProcessRunInit,
  ProcessRunResult,
  PromptBox,
  PromptFillArgs,
  PromptFilled,
  ToolSpec,
  UiFocusArgs,
  UiFocusResult,
  UiPane,
} from 'claude-code'

import { askTextOf, linesTextOf } from './ask-text-of'
import type { DiffClientPost } from './diff-client'
import * as Git from './git'
import * as Names from './names'
import {
  EMPTY_MODEL,
  clientPropsOf,
  commitKeyOf,
  commitOfKey,
  contentLinesOf,
  paneView,
  windowOf,
  type DiffState,
  type LineRange,
  type PaneModel,
} from './pane-view'

/**
 * The engine as the plugin calls it: bound once at `session.start`, so every
 * `$` call the plugin makes is listed in one place.
 */
type Host = {
  run: (
    argv: readonly string[],
    init?: ProcessRunInit,
  ) => Promise<ProcessRunResult>
  openPane: (pane: PaneOpenArgs) => Promise<void>
  closePane: (pane: PaneCloseArgs) => Promise<void>
  invalidate: () => void
  uiLog: (text: string) => void
  registerCommand: (spec: CommandSpec) => Promise<unknown>
  registerTool: (spec: ToolSpec) => Promise<{ tool: string }>
  focus: (args: UiFocusArgs) => Promise<UiFocusResult>
  panes: () => Promise<readonly UiPane[]>
  promptRead: () => Promise<PromptBox>
  promptFill: (args: PromptFillArgs) => Promise<PromptFilled>
}

/**
 * One thing armed to ride the next prompt: a whole commit, or a range of
 * lines of one; its token for the prompt box, its block for the model, and
 * whether the token is in the box.
 */
type Armed = {
  key: string
  sha: string
  short: string
  token: string
  text: string
  isWritten: boolean
} & ({ kind: 'commit' } | { kind: 'lines'; range: LineRange })

/** An armed range of lines. */
type ArmedLines = Armed & { kind: 'lines' }

/** The model's `show` input once checked: what to list, and its notes by sha. */
type ShowInput = {
  selection: Git.Selection | null
  notes: Readonly<Record<string, string>>
}

const NOTE_MAX_CHARS = 300

/** What the prompt reads when only tokens were typed. */
const TOKENS_ONLY_TEXT = 'See the attached commit.'

/** Rows one wheel tick moves the content; an arrow moves one. */
const WHEEL_ROWS = 3

const TOOL_INPUT_SCHEMA = {
  type: 'object',
  properties: {
    range: {
      type: 'string',
      description:
        'A git revision range to list, newest first: main..HEAD, ' +
        'HEAD~3..HEAD. Leave out when giving `commits`.',
    },
    commits: {
      type: 'array',
      description:
        'Commits to show, in this order, each by sha with an optional note ' +
        'drawn under it.',
      items: {
        type: 'object',
        properties: {
          sha: { type: 'string' },
          note: {
            type: 'string',
            description:
              'A line the user sees under the commit: a question, or why ' +
              'it is shown.',
          },
        },
        required: ['sha'],
      },
    },
  },
}

/**
 * The token the prompt box carries for an armed commit; the person sees it
 * where the engine's own attachments show, and deleting it disarms.
 *
 * @param short the commit's abbreviated sha
 * @returns the token
 */
export function tokenOf(short: string): string {
  return `[⧉ commit ${short}]`
}

/**
 * The token for a range of a commit's content lines: the commit, a colon,
 * and the range, which keeps several ranges of one commit apart.
 *
 * @param short the commit's abbreviated sha
 * @param range the content lines, inclusive
 * @returns the token
 */
export function linesTokenOf(short: string, range: LineRange): string {
  return `[⧉ ${short}:${range.from}-${range.to}]`
}

/** Any token of this plugin's in the prompt's text, with the space after it. */
const TOKEN_PATTERN = /\[⧉ [^\]\n]*\] ?/g

/**
 * Registers the commits pane: `/commits` and the model's `show` tool open
 * it, the Pane drawing lists the commits over a window on the selected one's
 * content, and the ask button rides commits on the next prompt.
 *
 * Git runs only when a door opens the pane (one `rev-parse`, the default
 * branch probes, one `log`) and when a commit is selected (one `show`).
 *
 * @param on the engine's registrar
 */
export function register(on: On) {
  let host: Host | null = null
  let cwd = ''
  let isOpen = false
  let model: PaneModel = EMPTY_MODEL

  const loads = new Map<string, Promise<void>>()

  /**
   * The commits armed to ride the next prompt, by full sha, in arming order.
   * Outlives the pane: closing it keeps them, sending the prompt or a new
   * session clears them. `isWritten` says the token is in the prompt box.
   */
  const armed = new Map<string, Armed>()

  /** The armed ranges of a commit's lines. */
  function linesArmedOf(sha: string | null): ArmedLines[] {
    return [...armed.values()].filter(
      (entry): entry is ArmedLines => entry.kind === 'lines' && entry.sha === sha,
    )
  }

  /** Brings the model's armed marks in step with `armed`. */
  function syncArmed() {
    model = { ...model, armed: [...armed.values()] }
  }

  function reset() {
    model = EMPTY_MODEL
    loads.clear()
  }

  async function forget(engine: Host | null) {
    armed.clear()
    reset()

    if (engine) {
      await syncBox(engine).catch(() => undefined)
    }
  }

  async function showCommits(
    engine: Host,
    selection: Git.Selection,
    notes: Readonly<Record<string, string>>,
  ): Promise<{ count: number } | { failed: string }> {
    if ((await Git.toplevelOf(engine.run, cwd)) === null) {
      return { failed: Names.NOT_IN_REPOSITORY_TEXT }
    }

    let commits: Git.Commit[]

    try {
      commits = await Git.logOf(engine.run, cwd, selection)
    } catch (error) {
      return { failed: `git log failed: ${messageOf(error)}` }
    }

    reset()

    const selectedSha = commits[0]?.sha ?? null

    model = {
      ...EMPTY_MODEL,
      label: selection.label,
      commits,
      notes: notesFor(commits, notes),
      selectedSha,
      armed: [...armed.values()],
    }

    try {
      await engine.openPane({
        id: Names.PANE_ID,
        title: Names.PANE_TITLE,
        focus: true,
        closeOnEscape: true,
      })
    } catch (error) {
      return { failed: `the commits pane did not open: ${messageOf(error)}` }
    }

    isOpen = true

    if (selectedSha !== null) {
      void loadDiff(engine, selectedSha)
    }

    engine.invalidate()

    return { count: commits.length }
  }

  function loadDiff(engine: Host, sha: string): Promise<void> {
    const pending = loads.get(sha)

    if (pending) {
      return pending
    }

    if (model.diffs[sha]?.kind === 'loaded') {
      return Promise.resolve()
    }

    const load = (async () => {
      setDiff(engine, sha, { kind: 'loading' })

      let state: DiffState

      try {
        state = { kind: 'loaded', files: await Git.diffOf(engine.run, cwd, sha) }
      } catch (error) {
        state = { kind: 'failed', error: messageOf(error) }
      }

      setDiff(engine, sha, state)
    })().finally(() => {
      loads.delete(sha)
    })

    loads.set(sha, load)

    return load
  }

  function setDiff(engine: Host, sha: string, state: DiffState) {
    const isListed = model.commits.some(commit => commit.sha === sha)

    if (isListed) {
      model = { ...model, diffs: { ...model.diffs, [sha]: state } }
      engine.invalidate()
    }
  }

  /**
   * Selects a commit and, unless the focus ring is what brought it, parks
   * the ring on its row so Tab and the highlight continue from there.
   */
  function select(engine: Host, sha: string, source: 'ring' | 'other') {
    const commit = model.commits.find(candidate => candidate.sha === sha)

    if (!commit || sha === model.selectedSha) {
      return
    }

    model = { ...model, selectedSha: sha, top: 0 }
    engine.invalidate()
    void loadDiff(engine, sha)

    if (source === 'other') {
      void engine
        .focus({ requestId: Names.PANE_ID, key: commitKeyOf(commit) })
        .catch(() => undefined)
    }
  }

  /**
   * Moves the selection by `delta` rows: past an end it stops, or with
   * `wrap` continues from the other end, as the ring's Tab does.
   */
  function selectBy(engine: Host, delta: number, wrap = false) {
    const count = model.commits.length
    const at = model.commits.findIndex(commit => commit.sha === model.selectedSha)

    if (count === 0 || at < 0) {
      return
    }

    const index = wrap
      ? (((at + delta) % count) + count) % count
      : Math.max(0, Math.min(count - 1, at + delta))
    const next = model.commits[index]

    if (next) {
      select(engine, next.sha, 'other')
    }
  }

  async function toggleAsk(engine: Host, sha: string) {
    const commit = model.commits.find(candidate => candidate.sha === sha)

    if (!commit) {
      return
    }

    const entry = armed.get(sha)

    if (entry) {
      await disarm(engine, entry)

      return
    }

    await loadDiff(engine, sha)

    if (!model.commits.some(candidate => candidate.sha === sha)) {
      return
    }

    const state = model.diffs[sha]
    const files = state?.kind === 'loaded' ? state.files : []

    for (const entry of linesArmedOf(sha)) {
      armed.delete(entry.key)
    }

    armed.set(sha, {
      key: sha,
      kind: 'commit',
      sha,
      short: commit.short,
      token: tokenOf(commit.short),
      text: askTextOf(commit, files),
      isWritten: false,
    })
    syncArmed()
    engine.invalidate()
    await syncBox(engine).catch(() => undefined)
  }

  /**
   * Arms a range of the selected commit's content lines, as a drag over
   * the diff region posted it, and writes its token.
   */
  async function armLines(engine: Host, from: number, to: number) {
    const commit = model.commits.find(candidate => candidate.sha === model.selectedSha)

    if (!commit) {
      return
    }

    const lines = contentLinesOf(model)
    const chosen = lines.slice(Math.max(0, from), Math.max(0, to) + 1)

    if (chosen.length === 0) {
      return
    }

    const range: LineRange = { from: Math.max(0, from), to: Math.max(0, from) + chosen.length - 1 }
    const key = `${commit.sha}:${range.from}-${range.to}`

    if (armed.has(key)) {
      return
    }

    armed.set(key, {
      key,
      kind: 'lines',
      sha: commit.sha,
      short: commit.short,
      token: linesTokenOf(commit.short, range),
      text: linesTextOf(commit, chosen),
      isWritten: false,
      range,
    })
    syncArmed()
    engine.invalidate()
    await syncBox(engine).catch(() => undefined)
  }

  /** Disarms the armed range of the selected commit that holds a content line. */
  async function disarmLinesAt(engine: Host, at: number) {
    const entry = linesArmedOf(model.selectedSha).find(
      candidate => at >= candidate.range.from && at <= candidate.range.to,
    )

    if (entry) {
      await disarm(engine, entry)
    }
  }

  async function disarm(engine: Host, entry: Armed) {
    armed.delete(entry.key)
    syncArmed()
    engine.invalidate()
    await syncBox(engine).catch(() => undefined)
  }

  /**
   * Places the content window's first row, clamped to the content.
   *
   * @returns whether it moved
   */
  function setTop(top: number): boolean {
    const clamped = Math.max(0, Math.min(windowOf(model).maxTop, top))

    if (clamped === model.top) {
      return false
    }

    model = { ...model, top: clamped }

    return true
  }

  let syncing: Promise<void> = Promise.resolve()

  /**
   * Brings the prompt box in step with `armed`, in one read and at most one
   * write: an entry whose written token the person deleted is dropped, the
   * tokens of entries no longer armed leave the box, and the tokens not yet
   * in it are appended. On arming and disarming, at a redraw or a close for
   * a token a refused fill left behind, and at a fresh load for tokens of a
   * module state that is gone. Syncs run one after another, so a redraw
   * during an arming's own sync cannot append the same token twice.
   */
  function syncBox(engine: Host): Promise<void> {
    const run = syncing.then(() => syncBoxNow(engine))

    syncing = run.catch(() => undefined)

    return run
  }

  async function syncBoxNow(engine: Host) {
    const box = await engine.promptRead()

    reconcileArmed(engine, box.text)

    const tokens = new Set([...armed.values()].map(entry => entry.token))
    const kept = box.text.replace(TOKEN_PATTERN, match =>
      tokens.has(match.trimEnd()) ? match : '',
    )
    const pending = [...armed.values()].filter(entry => !entry.isWritten)

    if (kept === box.text && pending.length === 0) {
      return
    }

    const appended = pending.map(entry => `${entry.token} `).join('')
    const lead = appended === '' || kept === '' || /\s$/.test(kept) ? '' : ' '

    const { isFilled } = await engine.promptFill(
      kept === box.text
        ? { text: `${lead}${appended}`, mode: 'append' }
        : { text: `${kept}${lead}${appended}`, mode: 'replace' },
    )

    if (isFilled) {
      for (const entry of pending) {
        entry.isWritten = true
      }
    }
  }

  /**
   * Drops the armed commits whose written token is no longer in the prompt's
   * text, so the gutter agrees with the box: on each edit the person makes,
   * and at each sync as a backstop.
   */
  function reconcileArmed(engine: Host, text: string) {
    const gone = [...armed.values()].filter(
      entry => entry.isWritten && !text.includes(entry.token),
    )

    if (gone.length > 0) {
      for (const entry of gone) {
        armed.delete(entry.key)
      }

      syncArmed()
      engine.invalidate()
    }
  }

  function hasWritten(): boolean {
    return [...armed.values()].some(entry => entry.isWritten)
  }

  async function closePane(engine: Host) {
    await engine.closePane({ id: Names.PANE_ID })
    isOpen = false
  }

  on('session.start', async ($, e, next) => {
    cwd = e.cwd

    const engine: Host = {
      run: (argv, init) => $.process.run(argv, init),
      openPane: pane => $.ui.open(pane),
      closePane: pane => $.ui.close(pane),
      invalidate: () => $.ui.invalidate('ui.render'),
      uiLog: text => $.ui.log(text),
      registerCommand: spec => $.command.register(spec),
      registerTool: spec => $.tool.register(spec),
      focus: args => $.ui.focus(args),
      panes: () => $.ui.panes(),
      promptRead: () => $.prompt.read(),
      promptFill: args => $.prompt.fill(args),
    }

    try {
      await engine.registerCommand({
        name: Names.COMMAND_NAME,
        description: Names.COMMAND_DESCRIPTION,
        argumentHint: Names.ARGUMENT_HINT,
      })

      const { tool } = await engine.registerTool({
        name: Names.TOOL_NAME,
        description: Names.TOOL_DESCRIPTION,
        inputSchema: TOOL_INPUT_SCHEMA,
      })

      if (tool !== Names.TOOL_FULL_NAME) {
        engine.uiLog(
          `${Names.PLUGIN_NAME}: the tool registered as ${tool}, not ` +
            `${Names.TOOL_FULL_NAME}; the model's calls will not be answered`,
        )
      }

      await engine.registerTool({
        name: Names.CLOSE_TOOL_NAME,
        description: Names.CLOSE_TOOL_DESCRIPTION,
      })

      host = engine
    } catch (error) {
      engine.uiLog(`${Names.PLUGIN_NAME}: not registered: ${messageOf(error)}`)
    }

    await clearStale(engine).catch(() => undefined)

    return next(e)
  })

  /**
   * On a load after a reload or a worker respawn the module's state is
   * fresh while the engine may still show the pane and the prompt may still
   * hold tokens: closes the one and strips the others.
   */
  async function clearStale(engine: Host) {
    const panes = await engine.panes()

    if (panes.some(pane => pane.id === Names.PANE_ID)) {
      await engine.closePane({ id: Names.PANE_ID })
    }

    await syncBox(engine)
  }

  on('command.run', { command: Names.COMMAND_NAME }, async ($, e, next) => {
    if (!host) {
      return next(e)
    }

    const engine = host

    if (!e.presentation.isFullscreen) {
      return { text: Names.NEEDS_FULLSCREEN_TEXT }
    }

    if (isOpen) {
      await closePane(engine)
      reset()

      return { text: Names.PANEL_HIDDEN_TEXT }
    }

    const args = e.args.trim()

    if (args !== '' && !Git.isSafeRevspec(args)) {
      return {
        text: `${Names.COMMAND_NAME}: not a revision range: ${lineOf(args)}`,
      }
    }

    const selection =
      args === ''
        ? await Git.branchSelectionOf(engine.run, cwd)
        : Git.rangeSelectionOf(args)

    const shown = await showCommits(engine, selection, {})

    if ('failed' in shown) {
      return { text: shown.failed }
    }

    return {
      text: `${Names.PANEL_SHOWN_TEXT}: ${Names.countOf(shown.count)} in ${selection.label}`,
      context: [orientationOf(selection.label, shown.count)],
    }
  })

  on('tool.call', { tool: Names.TOOL_FULL_NAME }, async ($, e, next) => {
    if (!host) {
      return next(e)
    }

    const engine = host

    if (e.agentId !== undefined) {
      return { deny: Names.MAIN_LOOP_ONLY_TEXT }
    }

    const input = showInputOf(e)

    if (typeof input === 'string') {
      return { deny: input }
    }

    const selection =
      input.selection ?? (await Git.branchSelectionOf(engine.run, cwd))

    const shown = await showCommits(engine, selection, input.notes)

    if ('failed' in shown) {
      return { deny: shown.failed }
    }

    return {
      result:
        `Showing ${Names.countOf(shown.count)} (${selection.label}) in the ` +
        `commits pane. ${orientationOf(selection.label, shown.count)}`,
    }
  })

  on('tool.call', { tool: Names.CLOSE_TOOL_FULL_NAME }, async ($, e, next) => {
    if (!host) {
      return next(e)
    }

    if (!isOpen) {
      return { result: 'The commits pane was not open.' }
    }

    await closePane(host)
    reset()

    return { result: 'Closed the commits pane.' }
  })

  /**
   * The row the ring is meant for when it leaves the list at either end:
   * the last row from the first, the first from the last, else none.
   */
  function wrapTargetOf(): Git.Commit | null {
    const at = model.commits.findIndex(commit => commit.sha === model.selectedSha)
    const last = model.commits.length - 1

    if (at < 0 || last < 1) {
      return null
    }

    return at === 0 ? (model.commits[last] ?? null) : at === last ? (model.commits[0] ?? null) : null
  }

  on('ui.focus', { requestId: Names.PANE_ID }, ($, e, next) => {
    const engine = host

    if (!engine || e.origin.kind !== 'person') {
      return next(e)
    }

    const isOwn = e.plugin === Names.PLUGIN_NAME
    const landed = isOwn ? commitOfKey(e.element, model.commits) : null

    if (landed) {
      select(engine, landed.sha, 'ring')

      return next(e)
    }

    const isCarrier = isOwn && (e.element === 'list-up' || e.element === 'list-down')
    const wrap = isCarrier || !isOwn ? wrapTargetOf() : null

    if (!wrap) {
      return next(e)
    }

    if (isCarrier) {
      select(engine, wrap.sha, 'ring')

      return next({ ...e, element: commitKeyOf(wrap) })
    }

    select(engine, wrap.sha, 'other')

    return {}
  })

  on('ui.scroll', { requestId: Names.PANE_ID }, ($, e, next) => {
    if (e.origin.kind !== 'person' || !host) {
      return next(e)
    }

    const { visible, maxTop } = windowOf(model)
    const size = Math.abs(e.by)
    const isEnd = size >= e.contentRows && e.contentRows > e.bodyRows
    const isPage = !isEnd && size >= e.bodyRows
    const isWheel = e.pointer !== undefined

    const step = isEnd
      ? maxTop
      : isPage
        ? visible
        : size * (isWheel ? WHEEL_ROWS : 1)

    if (setTop(model.top + Math.sign(e.by) * step)) {
      host.invalidate()
    }

    return {}
  })

  on('ui.message', { requestId: Names.PANE_ID, element: 'diff' }, async ($, e, next) => {
    const engine = host
    const post = postOf(e.data)

    if (!engine || !post) {
      return next(e)
    }

    switch (post.kind) {
      case 'scroll':
        // only the region moves, and the answer below redraws it
        setTop(model.top + post.by)
        break
      case 'move':
        selectBy(engine, post.by, post.wrap === true)
        break
      case 'ask':
        if (model.selectedSha !== null) {
          await toggleAsk(engine, model.selectedSha)
        }
        break
      case 'click':
        await disarmLinesAt(engine, post.at)
        break
      case 'select':
        await armLines(engine, post.from, post.to)
        break
    }

    return { props: clientPropsOf(model) }
  })

  on('ui.render', { component: 'Pane' }, async ($, e, next) => {
    if (e.requestId !== Names.PANE_ID || !host || e.surface === 'mobile') {
      return next(e)
    }

    const engine = host
    const table = await $.ui.resolve(e)
    const { Box, Text, Button } = table
    const Client = 'Client' in table ? table.Client : undefined

    // While the composer holds the keys the person may have edited the box,
    // and a token a refused fill left behind can be written now.
    if (!e.props.isFocused && armed.size > 0) {
      await syncBox(engine).catch(() => undefined)
    }

    model = {
      ...model,
      bodyRows: e.props.scroll.bodyRows,
      bodyColumns: e.props.bodyColumns,
    }

    return paneView({ Box, Text, Button, ...(Client ? { Client } : {}) }, model, {
      select: sha => select(engine, sha, 'other'),
      selectBy: delta => selectBy(engine, delta),
      toggleAsk: sha => toggleAsk(engine, sha),
    })
  })

  on('ui.close', { id: Names.PANE_ID }, async ($, e, next) => {
    const result = await next(e)

    if (result.deny === undefined) {
      isOpen = false
      reset()

      if (host && armed.size > 0) {
        await syncBox(host).catch(() => undefined)
      }
    }

    return result
  })

  on('command.run', { command: ['clear', 'resume'] }, async ($, e, next) => {
    const result = await next(e)

    if (host && isOpen) {
      await closePane(host).catch(() => undefined)
    }

    await forget(host)

    return result
  })

  on('prompt.edit', async ($, e, next) => {
    const box = await next(e)

    if (host && hasWritten()) {
      reconcileArmed(host, box.text)
    }

    return box
  })

  on('prompt.submit', async ($, e, next) => {
    const engine = host

    if (!engine || armed.size === 0) {
      return next(e)
    }

    let text = e.text
    const blocks: string[] = []

    for (const entry of armed.values()) {
      const token = entry.token
      const isKept = !entry.isWritten || text.includes(token)

      if (isKept) {
        text = withoutToken(text, token)
        blocks.push(entry.text)
      }
    }

    const result =
      blocks.length === 0
        ? await next(e)
        : await next({
            ...e,
            text: text.trim() === '' ? TOKENS_ONLY_TEXT : text,
            context: [...(e.context ?? []), ...blocks],
          })

    if (result.drop === undefined) {
      armed.clear()
      syncArmed()
      engine.invalidate()
    }

    return result
  })
}

/**
 * What the diff region posted, checked field by field, or null for anything
 * else.
 */
function postOf(data: unknown): DiffClientPost | null {
  if (typeof data !== 'object' || data === null) {
    return null
  }

  const kind: unknown = Reflect.get(data, 'kind')
  const num = (name: string): number | null => {
    const value: unknown = Reflect.get(data, name)

    return typeof value === 'number' && Number.isInteger(value) ? value : null
  }

  if (kind === 'scroll') {
    const by = num('by')

    return by === null ? null : { kind, by }
  }

  if (kind === 'move') {
    const by = num('by')

    return by === null
      ? null
      : { kind, by, ...(Reflect.get(data, 'wrap') === true ? { wrap: true as const } : {}) }
  }

  if (kind === 'ask') {
    return { kind }
  }

  if (kind === 'click') {
    const at = num('at')

    return at === null ? null : { kind, at }
  }

  if (kind === 'select') {
    const from = num('from')
    const to = num('to')

    return from === null || to === null ? null : { kind, from, to }
  }

  return null
}

/**
 * The text with every copy of a token and the space after it removed.
 *
 * @param text the prompt's text
 * @param token the token
 * @returns the text without it
 */
export function withoutToken(text: string, token: string): string {
  return text.split(`${token} `).join('').split(token).join('')
}

/**
 * The model's `show` input checked field by field: a range or a list of
 * commits with notes, never both; or the reason it is refused.
 *
 * @param e the tool call, its input as own fields
 * @returns the input, or the refusal
 */
function showInputOf(e: object): ShowInput | string {
  const raw: Record<string, unknown> = { ...e }
  const { range, commits } = raw

  if (range !== undefined && typeof range !== 'string') {
    return 'range must be a string'
  }

  if (range !== undefined && !Git.isSafeRevspec(range.trim())) {
    return `not a revision range: ${lineOf(range)}`
  }

  if (commits !== undefined && !Array.isArray(commits)) {
    return 'commits must be an array of { sha, note? }'
  }

  const shas: string[] = []
  const notes: Record<string, string> = {}

  for (const item of commits ?? []) {
    if (typeof item !== 'object' || item === null) {
      return 'each commit is an object with a sha'
    }

    const sha: unknown = Reflect.get(item, 'sha')
    const note: unknown = Reflect.get(item, 'note')

    if (typeof sha !== 'string' || !/^[0-9a-f]{4,40}$/i.test(sha)) {
      return `not a sha: ${lineOf(String(sha))}`
    }

    if (note !== undefined && typeof note !== 'string') {
      return 'a note is a string'
    }

    shas.push(sha)

    if (note !== undefined && note.trim() !== '') {
      notes[sha.toLowerCase()] = lineOf(note).slice(0, NOTE_MAX_CHARS)
    }
  }

  if (range !== undefined && shas.length > 0) {
    return 'give either range or commits, not both'
  }

  const selection =
    range !== undefined
      ? Git.rangeSelectionOf(range.trim())
      : shas.length > 0
        ? Git.commitsSelectionOf(shas)
        : null

  return { selection, notes }
}

/**
 * The notes by each listed commit's full sha, from notes keyed by the sha
 * or a prefix of it as the model gave it.
 */
function notesFor(
  commits: readonly Git.Commit[],
  given: Readonly<Record<string, string>>,
): Record<string, string> {
  const notes: Record<string, string> = {}

  for (const commit of commits) {
    const key = Object.keys(given).find(prefix =>
      commit.sha.toLowerCase().startsWith(prefix),
    )

    if (key !== undefined) {
      notes[commit.sha] = given[key] ?? ''
    }
  }

  return notes
}

/** A hidden note the model reads once the pane is open, whichever door opened it. */
function orientationOf(label: string, count: number): string {
  return (
    `The commits pane is open beside the transcript, listing ` +
    `${Names.countOf(count)} (${label}). The user can select a commit and press ` +
    `${Names.ASK_HOTKEY} to attach its message and diff to their next prompt, ` +
    `shown in their prompt as "${tokenOf('<sha>')}", or drag over lines of it ` +
    `to attach those lines alone, shown as "[⧉ <sha>:<from>-<to>]"; a whole ` +
    `commit's attachment begins "${Names.ATTACHED_LEAD}" and wraps a <commit> element, ` +
    `a lines attachment "${Names.ATTACHED_LEAD_LINES} lines of commit" and wraps ` +
    `<commit-lines>. Escape closes the pane while it has the ` +
    `keys, as does /commits again; the ${Names.CLOSE_TOOL_FULL_NAME} tool ` +
    `closes it too.`
  )
}

/** Text the model or the user typed, safe on one transcript line. */
function lineOf(text: string): string {
  return Git.sanitize(text).replace(/\s+/g, ' ').trim()
}

function messageOf(error: unknown): string {
  return error instanceof Error ? error.message : String(error)
}
