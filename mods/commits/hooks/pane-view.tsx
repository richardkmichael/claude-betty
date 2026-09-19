/* @jsxRuntime classic */
/* @jsx h */
/* @jsxFrag Fragment */
import type { ClientProps, ElementConstructor, ElementTable, RenderElement } from 'claude-code'

import type { DiffClientProps } from './diff-client'
import type { Commit, FileDiff } from './git'
import { sanitize } from './git'
import {
  ASK_HOTKEY,
  ASK_LABEL,
  HELP_TEXT,
  LIST_DOWN_ACTION,
  LIST_UP_ACTION,
} from './names'

/**
 * The elements the pane draws with: the three every surface has, and the
 * `Client` the terminal and the desktop have, which draws the diff region;
 * without it the content is drawn as plain rows.
 */
export type PaneUi = Pick<ElementTable<'terminal'>, 'Box' | 'Text' | 'Button'> & {
  Client?: ElementConstructor<ClientProps>
}

/**
 * A range of content lines armed to ride the next prompt, by index into the
 * selected commit's content, inclusive.
 */
export type LineRange = {
  from: number
  to: number
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
 * loaded patch by sha, the selected commit, the commits armed to ride the
 * next prompt, and the content window's first row over the room the pane
 * last had.
 */
export type PaneModel = {
  label: string
  commits: readonly Commit[]
  notes: Readonly<Record<string, string>>
  diffs: Readonly<Record<string, DiffState>>
  selectedSha: string | null
  armedShas: readonly string[]
  /** The armed line ranges of the selected commit. */
  armedRanges: readonly LineRange[]
  top: number
  bodyRows: number
  bodyColumns: number
}

/**
 * What the pane's buttons do.
 */
export type PaneActions = {
  select: (sha: string) => void
  selectBy: (delta: number) => void
  toggleAsk: (sha: string) => Promise<void>
}

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
  /** The file a diff line belongs to; absent on the message and the file headers. */
  path?: string
}

/** The selected row's mark, in the gutter's first column. */
export const POINTER = '❯'

/** An armed row's mark, in the gutter's third column. */
export const ARMED_MARK = '⧉'

/** The gutter before a commit row: pointer, space, armed mark, space. */
const GUTTER = `${POINTER} ${ARMED_MARK} `

/**
 * Rows drawn past the window's end, blank where the content runs out, so the
 * engine always has rows to scroll: an arrow then raises `ui.scroll` instead
 * of walking the ring through the list. The surface clips them.
 */
export const SCROLL_MARGIN_ROWS = 8

export const EMPTY_MODEL: PaneModel = {
  label: '',
  commits: [],
  notes: {},
  diffs: {},
  selectedSha: null,
  armedShas: [],
  armedRanges: [],
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
 * The rows the header, the blank line under it, the list and the blank line
 * under that take, above the content window.
 *
 * @param model what the pane draws
 * @returns the row count
 */
export function pinnedRowsOf(model: PaneModel): number {
  const noteRows = model.commits.filter(
    commit => model.notes[commit.sha] !== undefined,
  ).length

  return 1 + 1 + Math.max(1, model.commits.length) + noteRows + 1
}

/**
 * The rows the content window shows at once: what the body leaves under the
 * pinned rows, at least one.
 *
 * @param model what the pane draws
 * @returns the row count
 */
export function visibleRowsOf(model: PaneModel): number {
  return Math.max(1, model.bodyRows - pinnedRowsOf(model))
}

/**
 * The content's rows for the selected commit: the message, then each file's
 * lines, one row each.
 *
 * @param model what the pane draws
 * @returns the rows, empty with no selection
 */
export function contentLinesOf(model: PaneModel): Line[] {
  const commit = model.commits.find(
    candidate => candidate.sha === model.selectedSha,
  )

  if (!commit) {
    return []
  }

  const lines: Line[] = [
    { text: `${commit.short} ${sanitize(commit.subject)}`, bold: true },
    { text: `${sanitize(commit.author)} · ${commit.date}`, dim: true },
  ]

  if (commit.body !== '') {
    lines.push({ text: '' })

    for (const line of commit.body.split('\n')) {
      lines.push({ text: sanitize(line) })
    }
  }

  lines.push({ text: '' })

  const state = model.diffs[commit.sha]

  if (state === undefined || state.kind === 'loading') {
    lines.push({ text: 'loading the diff…', dim: true })
  } else if (state.kind === 'failed') {
    lines.push({ text: `git show failed: ${sanitize(state.error)}`, color: 'red' })
  } else if (state.files.length === 0) {
    lines.push({ text: '(empty commit)', dim: true })
  } else {
    for (const file of state.files) {
      lines.push(...fileLinesOf(file))
    }
  }

  return lines
}

function fileLinesOf(file: FileDiff): Line[] {
  const lines: Line[] = [{ text: sanitize(file.path), bold: true }]

  if (file.hunks === '') {
    lines.push({ text: file.note ?? '(no text diff)', dim: true })
  } else {
    for (const line of file.hunks.replace(/\n$/, '').split('\n')) {
      lines.push({ ...diffLineOf(line), path: file.path })
    }
  }

  if (file.isCut) {
    lines.push({ text: '(cut)', dim: true })
  }

  return lines
}

function diffLineOf(text: string): Line {
  const lead = text[0]

  if (lead === '+') {
    return { text, color: 'green' }
  }

  if (lead === '-') {
    return { text, color: 'red' }
  }

  if (lead === '@') {
    return { text, color: 'cyan' }
  }

  return { text }
}

/**
 * The diff region's props: the window of content lines it draws, where the
 * window starts, and the armed ranges of the selected commit. What the pane
 * draws it with, and what the `ui.message` hook hands it after a post.
 *
 * @param model what the pane draws
 * @returns the props, plain data
 */
export function clientPropsOf(model: PaneModel): DiffClientProps {
  const lines = contentLinesOf(model)
  const visible = visibleRowsOf(model)
  const top = Math.min(model.top, Math.max(0, lines.length - visible))

  return {
    offset: top,
    lines: lines.slice(top, top + visible).map(line => ({
      text: line.text,
      ...(line.color !== undefined ? { color: line.color } : {}),
      ...(line.bold ? { bold: true } : {}),
      ...(line.dim ? { dim: true } : {}),
    })),
    armed: model.armedRanges.map(range => ({ ...range })),
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
  const lines = contentLinesOf(model)
  const visible = visibleRowsOf(model)
  const top = Math.min(model.top, Math.max(0, lines.length - visible))
  const wanted = visible + SCROLL_MARGIN_ROWS
  const shown = lines.slice(top, top + wanted)

  while (shown.length < wanted) {
    shown.push({ text: '' })
  }

  const content: RenderElement[] = Client
    ? [
        <Client
          key="diff"
          module="./diff-client.tsx"
          height={visible}
          props={clientPropsOf(model)}
        />,
        ...Array.from({ length: SCROLL_MARGIN_ROWS }, () => (
          <Text> </Text>
        )),
      ]
    : shown.map(line => lineRow(ui, line))

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
    <Box flexDirection="column">
      {headerRow(ui, model, actions)}
      <Text> </Text>
      {rows}
      <Text> </Text>
      {content}
    </Box>
  )
}

function headerRow(
  ui: PaneUi,
  model: PaneModel,
  actions: PaneActions,
): RenderElement {
  const { Box, Text, Button } = ui
  const count =
    model.commits.length === 1 ? '1 commit' : `${model.commits.length} commits`
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
          <Button
            key="ask"
            plain
            dimColor
            hotkey={ASK_HOTKEY}
            onPress={() => actions.toggleAsk(selected.sha)}
          >
            {ASK_LABEL}
          </Button>
        ) : null}
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
  const isArmed = model.armedShas.includes(commit.sha)
  const note = model.notes[commit.sha]
  const room = Math.max(8, model.bodyColumns - GUTTER.length - 1)
  const label = truncated(`${commit.short} ${sanitize(commit.subject)}`, room)

  const lines: RenderElement[] = [
    <Box flexDirection="row">
      <Text>{isSelected ? POINTER : ' '}</Text>
      <Text> </Text>
      <Text color="cyan">{isArmed ? ARMED_MARK : ' '}</Text>
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
