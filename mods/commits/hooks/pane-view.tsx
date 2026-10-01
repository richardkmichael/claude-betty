/* @jsxRuntime classic */
/* @jsx h */
/* @jsxFrag Fragment */
import type { ClientProps, ElementConstructor, ElementTable, RenderElement } from 'claude-code'

import type { DiffClientProps, LineRange } from './diff-client'
import type { Commit, FileDiff } from './git'
import { sanitize } from './git'
import {
  ASK_HOTKEY,
  ASK_LABEL,
  HELP_TEXT,
  LIST_DOWN_ACTION,
  LIST_UP_ACTION,
  PAGE_BACK_HOTKEY,
  PAGE_FORWARD_HOTKEY,
  countOf,
} from './names'

export type { LineRange } from './diff-client'

/**
 * The elements the pane draws with: the three every surface has, and the
 * `Client` the terminal and the desktop have, which draws the diff region;
 * without it the content is drawn as plain rows.
 */
export type PaneUi = Pick<ElementTable<'terminal'>, 'Box' | 'Text' | 'Button'> & {
  Client?: ElementConstructor<ClientProps>
}

/**
 * A commit's patch as the pane holds it.
 */
export type DiffState =
  | { kind: 'loading' }
  | { kind: 'loaded'; files: readonly FileDiff[] }
  | { kind: 'failed'; error: string }

/**
 * What the pane draws: the listing, the model's notes by full sha, each
 * loaded patch by sha, the selected commit, what is included to ride the next
 * prompt, and the content window's first row over the room the pane last
 * had.
 */
export type PaneModel = {
  label: string
  commits: readonly Commit[]
  notes: Readonly<Record<string, string>>
  diffs: Readonly<Record<string, DiffState>>
  selectedSha: string | null
  /** Each included thing: a whole commit, or with `range` some of its lines. */
  included: readonly IncludedMark[]
  top: number
  bodyRows: number
  bodyColumns: number
}

/** An included commit by sha, whole without a range, some of its lines with one. */
export type IncludedMark = {
  sha: string
  range?: LineRange
}

/**
 * What the pane's buttons do.
 */
export type PaneActions = {
  select: (sha: string) => void
  selectBy: (delta: number) => void
  pageBy: (pages: number) => void
  toggleAsk: (sha: string) => Promise<void>
}

/**
 * The keys of the header's Buttons that only carry a key or a chord: none is
 * drawn, so the ring is moved past any of them it lands on.
 */
export const CARRIER_KEYS: readonly string[] = [
  'ask',
  'page-forward',
  'page-back',
  'list-up',
  'list-down',
]

/**
 * One row of the content under the pinned list: its text and how it is
 * drawn. Every row is one terminal row, truncated, so the window's arithmetic
 * holds.
 */
export type Line = {
  text: string
  color?: string
  bold?: boolean
  dim?: boolean
  /** Which part of the commit the line belongs to. */
  kind: LineKind
  /** The file, for a file header, a hunk header, a diff line and a file's note. */
  path?: string
  /** The enclosing hunk's header, for a diff line. */
  hunk?: string
  /** The line's number in the file before the commit: a context or `-` line. */
  oldLine?: number
  /** The line's number in the file at the commit: a context or `+` line. */
  newLine?: number
}

/**
 * The parts of a commit's content as the pane lays them out: the subject
 * line, the author line, the message's lines, a note the pane adds (loading,
 * a rename, a cut), a file header, a hunk header, and the diff's own lines.
 */
export type LineKind =
  | 'subject'
  | 'author'
  | 'blank'
  | 'message'
  | 'note'
  | 'file'
  | 'hunk'
  | 'diff'

/** The selected row's mark, in the gutter's first column. */
export const POINTER = '❯'

/** An included row's mark, in the gutter's third column. */
export const INCLUDED_MARK = '⧉'

/** The mark's colour when the whole commit is included. */
export const WHOLE_INCLUDED_COLOR = 'green'

/** The mark's colour when only some of the commit's lines are included. */
export const LINES_INCLUDED_COLOR = 'yellow'

/** The gutter before a commit row: pointer, space, included mark, space. */
const GUTTER = `${POINTER} ${INCLUDED_MARK} `

/**
 * Rows drawn past the window's end, blank where the content runs out, so the
 * engine always has rows to scroll: an arrow then raises `ui.scroll` instead
 * of walking the ring through the list. The surface clips them.
 */
export const SCROLL_MARGIN_ROWS = 8

/** Rows of padding above the header, inside the pane's body, as the native diff sidebar has. */
export const TOP_PADDING_ROWS = 1

/** The border drawn around the content window, and the rows its top and bottom edges take. */
const CONTENT_BORDER_STYLE = 'round'
export const CONTENT_BORDER_ROWS = 2

export const EMPTY_MODEL: PaneModel = {
  label: '',
  commits: [],
  notes: {},
  diffs: {},
  selectedSha: null,
  included: [],
  top: 0,
  bodyRows: 0,
  bodyColumns: 80,
}

/**
 * The key a commit's row is drawn under: what `ui.focus` and `ui.press` name.
 *
 * @param commit the commit
 * @returns the key
 */
export function commitKeyOf(commit: Pick<Commit, 'short'>): string {
  return `commit:${commit.short}`
}

/**
 * The commit a row key names, or null for another element's key.
 *
 * @param key an element's key
 * @param commits the listed commits
 * @returns the commit, or null
 */
export function commitOfKey(
  key: string | undefined,
  commits: readonly Commit[],
): Commit | null {
  return commits.find(commit => commitKeyOf(commit) === key) ?? null
}

/**
 * The rows the header, the gap under it, the list and the gap under that
 * take, above the content window.
 *
 * @param model what the pane draws
 * @returns the row count
 */
export function pinnedRowsOf(model: PaneModel): number {
  const noteRows = model.commits.filter(
    commit => model.notes[commit.sha] !== undefined,
  ).length

  return TOP_PADDING_ROWS + 1 + 1 + Math.max(1, model.commits.length) + noteRows + 1
}

/**
 * The rows the content window shows at once: what the body leaves under the
 * pinned rows and inside the content's border, at least one.
 *
 * @param model what the pane draws
 * @returns the row count
 */
export function visibleRowsOf(model: PaneModel): number {
  return Math.max(1, model.bodyRows - pinnedRowsOf(model) - CONTENT_BORDER_ROWS)
}

/** The content last laid out and what it was laid out from, by identity. */
let laidOut: {
  commits: readonly Commit[]
  selectedSha: string | null
  diff: DiffState | undefined
  lines: Line[]
} | null = null

/**
 * The content's rows for the selected commit: the message, then each file's
 * lines, one row each. Laid out once per listing, selection and loaded
 * patch, which the model replaces whole when they change; every redraw,
 * scroll and post reads the same rows, so none may change them.
 *
 * @param model what the pane draws
 * @returns the rows, empty with no selection
 */
export function contentLinesOf(model: PaneModel): Line[] {
  const diff = model.selectedSha === null ? undefined : model.diffs[model.selectedSha]

  if (
    laidOut === null ||
    laidOut.commits !== model.commits ||
    laidOut.selectedSha !== model.selectedSha ||
    laidOut.diff !== diff
  ) {
    laidOut = { commits: model.commits, selectedSha: model.selectedSha, diff, lines: layoutOf(model) }
  }

  return laidOut.lines
}

function layoutOf(model: PaneModel): Line[] {
  const commit = model.commits.find(
    candidate => candidate.sha === model.selectedSha,
  )

  if (!commit) {
    return []
  }

  const lines: Line[] = [
    { text: `${commit.short} ${sanitize(commit.subject)}`, bold: true, kind: 'subject' },
    { text: `${sanitize(commit.author)} · ${commit.date}`, dim: true, kind: 'author' },
  ]

  if (commit.body !== '') {
    lines.push({ text: '', kind: 'blank' })

    for (const line of commit.body.split('\n')) {
      lines.push({ text: sanitize(line), kind: 'message' })
    }
  }

  lines.push({ text: '', kind: 'blank' })

  const state = model.diffs[commit.sha]

  if (state === undefined || state.kind === 'loading') {
    lines.push({ text: 'loading the diff…', dim: true, kind: 'note' })
  } else if (state.kind === 'failed') {
    lines.push({ text: `git show failed: ${sanitize(state.error)}`, color: 'red', kind: 'note' })
  } else if (state.files.length === 0) {
    lines.push({ text: '(empty commit)', dim: true, kind: 'note' })
  } else {
    for (const file of state.files) {
      lines.push(...fileLinesOf(file))
    }
  }

  return lines
}

/** A hunk header's old and new start lines: `@@ -10,7 +10,8 @@ …`. */
const HUNK_HEADER = /^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@/

function fileLinesOf(file: FileDiff): Line[] {
  const lines: Line[] = [
    { text: sanitize(file.path), bold: true, kind: 'file', path: file.path },
  ]

  if (file.hunks === '') {
    lines.push({ text: file.note ?? '(no text diff)', dim: true, kind: 'note', path: file.path })
  } else {
    let hunk: string | undefined
    let oldAt = 0
    let newAt = 0

    for (const text of file.hunks.replace(/\n$/, '').split('\n')) {
      if (text.startsWith('@')) {
        const header = HUNK_HEADER.exec(text)

        hunk = text
        oldAt = Number(header?.[1] ?? 0)
        newAt = Number(header?.[2] ?? 0)
        lines.push({ text, color: 'cyan', kind: 'hunk', path: file.path, hunk: text })
      } else {
        const lead = text[0]
        const numbers =
          lead === '+'
            ? { newLine: newAt++ }
            : lead === '-'
              ? { oldLine: oldAt++ }
              : lead === '\\'
                ? {}
                : { oldLine: oldAt++, newLine: newAt++ }

        lines.push({
          ...diffLineOf(text),
          kind: 'diff',
          path: file.path,
          ...(hunk === undefined ? {} : { hunk }),
          ...numbers,
        })
      }
    }
  }

  if (file.isCut) {
    lines.push({ text: '(cut)', dim: true, kind: 'note', path: file.path })
  }

  return lines
}

function diffLineOf(text: string): Pick<Line, 'text' | 'color'> {
  const lead = text[0]

  if (lead === '+') {
    return { text, color: 'green' }
  }

  if (lead === '-') {
    return { text, color: 'red' }
  }

  return { text }
}

/**
 * The window over the selected commit's content: its rows, how many show at
 * once, the first shown, and the furthest first row the content allows.
 */
export type ContentWindow = {
  lines: readonly Line[]
  visible: number
  top: number
  maxTop: number
}

/**
 * The content window as the model places it, the top clamped to the content.
 *
 * @param model what the pane draws
 * @returns the window
 */
export function windowOf(model: PaneModel): ContentWindow {
  const lines = contentLinesOf(model)
  const visible = visibleRowsOf(model)
  const maxTop = Math.max(0, lines.length - visible)

  return { lines, visible, top: Math.min(model.top, maxTop), maxTop }
}

/**
 * The diff region's props: the window of content lines it draws, where the
 * window starts, and the included ranges of the selected commit. What the pane
 * draws it with, and what the `ui.message` hook hands it after a post.
 *
 * @param model what the pane draws
 * @param window the content window, when the caller has it
 * @returns the props, plain data
 */
export function clientPropsOf(
  model: PaneModel,
  window: ContentWindow = windowOf(model),
): DiffClientProps {
  const { lines, visible, top } = window

  return {
    offset: top,
    lines: lines.slice(top, top + visible).map(line => ({
      text: line.text,
      ...(line.color !== undefined ? { color: line.color } : {}),
      ...(line.bold ? { bold: true } : {}),
      ...(line.dim ? { dim: true } : {}),
    })),
    included: model.included.flatMap(mark =>
      mark.sha === model.selectedSha && mark.range !== undefined ? [{ ...mark.range }] : [],
    ),
  }
}

/**
 * Draws the pane: the header with the ask button, one row per commit with the
 * model's note under it, then the window over the selected commit's content.
 *
 * @param ui the elements
 * @param model what to draw
 * @param actions what the buttons do
 * @returns the tree
 */
export function paneView(
  ui: PaneUi,
  model: PaneModel,
  actions: PaneActions,
): RenderElement {
  const { Box, Text, Client } = ui
  const window = windowOf(model)

  const shown = Client ? (
    <Client
      key="diff"
      module="./diff-client.tsx"
      height={window.visible}
      props={clientPropsOf(model, window)}
    />
  ) : (
    plainRowsOf(ui, window)
  )
  const margin = Array.from({ length: SCROLL_MARGIN_ROWS }, () => <Text> </Text>)

  const rows: RenderElement[] = model.commits.map(commit =>
    commitRow(ui, model, actions, commit),
  )

  if (model.commits.length === 0) {
    rows.push(
      <Text dimColor wrap="truncate-end">
        {`No commits in ${sanitize(model.label)}`}
      </Text>,
    )
  }

  return (
    <Box flexDirection="column" gap={1} paddingTop={TOP_PADDING_ROWS}>
      {headerRow(ui, model, actions)}
      <Box flexDirection="column">{rows}</Box>
      <Box flexDirection="column">
        <Box flexDirection="column" borderStyle={CONTENT_BORDER_STYLE} borderDimColor>
          {shown}
        </Box>
        {margin}
      </Box>
    </Box>
  )
}

/** The window's rows as plain text, padded to the window's height like the region. */
function plainRowsOf(ui: PaneUi, window: ContentWindow): RenderElement[] {
  const shown = window.lines.slice(window.top, window.top + window.visible)

  while (shown.length < window.visible) {
    shown.push({ text: '', kind: 'blank' })
  }

  return shown.map(line => lineRow(ui, line))
}

function headerRow(
  ui: PaneUi,
  model: PaneModel,
  actions: PaneActions,
): RenderElement {
  const { Box, Text, Button } = ui
  const count = countOf(model.commits.length)
  const selected = model.commits.find(
    commit => commit.sha === model.selectedSha,
  )

  return (
    <Box flexDirection="row" justifyContent="space-between">
      <Text bold wrap="truncate-end">
        {'Commits '}
        <Text dimColor>{`${sanitize(model.label)} · ${count}`}</Text>
      </Text>
      <Text dimColor wrap="truncate-end">
        {HELP_TEXT}
      </Text>
      <Box flexDirection="row" paddingRight={2}>
        {selected ? (
          <Text dimColor wrap="truncate-end">
            {`${ASK_HOTKEY}: ${ASK_LABEL}`}
          </Text>
        ) : null}
        {/* Only a Button's hotkey reaches the pane, and a plain one is drawn
            as `a: label`, so the Buttons carrying keys are hidden and their
            help is Text. */}
        <Box display="none">
          {selected ? (
            <Button
              key="ask"
              plain
              hotkey={ASK_HOTKEY}
              onPress={() => actions.toggleAsk(selected.sha)}
            >
              {''}
            </Button>
          ) : null}
          <Button
            key="page-forward"
            plain
            hotkey={PAGE_FORWARD_HOTKEY}
            onPress={() => actions.pageBy(1)}
          >
            {''}
          </Button>
          <Button
            key="page-back"
            plain
            hotkey={PAGE_BACK_HOTKEY}
            onPress={() => actions.pageBy(-1)}
          >
            {''}
          </Button>
        </Box>
        <Button
          key="list-up"
          plain
          action={LIST_UP_ACTION}
          onPress={() => actions.selectBy(-1)}
        >
          {''}
        </Button>
        <Button
          key="list-down"
          plain
          action={LIST_DOWN_ACTION}
          onPress={() => actions.selectBy(1)}
        >
          {''}
        </Button>
      </Box>
    </Box>
  )
}

function commitRow(
  ui: PaneUi,
  model: PaneModel,
  actions: PaneActions,
  commit: Commit,
): RenderElement {
  const { Box, Text, Button } = ui
  const isSelected = commit.sha === model.selectedSha
  const marks = model.included.filter(mark => mark.sha === commit.sha)
  const isWholeIncluded = marks.some(mark => mark.range === undefined)
  const isPartlyIncluded = marks.length > 0
  const note = model.notes[commit.sha]
  const room = Math.max(8, model.bodyColumns - GUTTER.length - 1)
  const label = truncated(`${commit.short} ${sanitize(commit.subject)}`, room)

  const lines: RenderElement[] = [
    <Box flexDirection="row">
      <Text>{isSelected ? POINTER : ' '}</Text>
      <Text> </Text>
      <Text color={isWholeIncluded ? WHOLE_INCLUDED_COLOR : LINES_INCLUDED_COLOR}>
        {isWholeIncluded || isPartlyIncluded ? INCLUDED_MARK : ' '}
      </Text>
      <Text> </Text>
      <Button
        key={commitKeyOf(commit)}
        plain
        dimColor={!isSelected}
        {...(isSelected ? { autoFocus: true as const } : {})}
        onPress={() => actions.select(commit.sha)}
      >
        {label}
      </Button>
    </Box>,
  ]

  if (note !== undefined) {
    lines.push(
      <Text color="yellow" wrap="truncate-end">
        {`${' '.repeat(GUTTER.length)}note: ${sanitize(note)}`}
      </Text>,
    )
  }

  return <Box flexDirection="column">{lines}</Box>
}

function lineRow(ui: PaneUi, line: Line): RenderElement {
  const { Text } = ui

  return (
    <Text
      wrap="truncate-end"
      {...(line.color !== undefined ? { color: line.color } : {})}
      {...(line.bold ? { bold: true } : {})}
      {...(line.dim ? { dimColor: true } : {})}
    >
      {line.text === '' ? ' ' : line.text}
    </Text>
  )
}

function truncated(text: string, room: number): string {
  return text.length <= room ? text : `${text.slice(0, Math.max(0, room - 1))}…`
}
