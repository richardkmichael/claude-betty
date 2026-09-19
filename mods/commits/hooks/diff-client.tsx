/* @jsxRuntime classic */
/* @jsx h */
/* @jsxFrag Fragment */
import type {
  ClientKeyEvent,
  ClientModule,
  ClientPointerEvent,
  ClientSurface,
  RenderElement,
} from 'claude-code'

/**
 * One drawn row: its text and how it is drawn, as the pane's content lines
 * are shaped.
 */
export type ClientLine = {
  text: string
  color?: string
  bold?: boolean
  dim?: boolean
}

/**
 * A range of content lines, by index into the pane's content, inclusive.
 */
export type LineRange = {
  from: number
  to: number
}

/**
 * What the pane hands the diff region: the window of lines it draws, where
 * the window starts in the content, and the ranges already armed.
 */
export type DiffClientProps = {
  offset: number
  lines: ClientLine[]
  armed: LineRange[]
}

/**
 * What the region posts to the hooks module: a drag's range, a click on an
 * armed range, or a scroll asked for by keys.
 */
export type DiffClientPost =
  | { kind: 'select'; from: number; to: number }
  | { kind: 'click'; at: number }
  | { kind: 'scroll'; by: number }
  | { kind: 'move'; by: number; wrap?: true }
  | { kind: 'ask' }

type State = {
  anchor: number | null
  focus: number | null
  /** Whether a `move` came since the `down`: a drag, not a click. */
  isDrag: boolean
}

const IDLE: State = { anchor: null, focus: null, isDrag: false }

/** The gutter before each line: the armed mark or its blank. */
const ARMED_MARK = '⧉ '
const NO_MARK = '  '

let current: DiffClientProps = { offset: 0, lines: [], armed: [] }

/**
 * Draws the selected commit's content and turns a drag over its lines into a
 * range the hooks module arms: `down` anchors, `move` extends, `up` posts.
 * A click on an armed range posts it back to be disarmed; a click elsewhere
 * posts nothing and only gives the region the keys. Then arrow, page, `j`
 * and `k` keys post a scroll, Tab and ctrl+arrows post a move between
 * commits (Tab wrapping at the ends as the pane's ring does, ctrl+arrows
 * stopping there as the engine's list keys do), and `a` posts an ask for
 * the selected commit: the keys stay in the region through all of them.
 *
 * @param props the window of lines, its offset, and the armed ranges
 * @param surface the region's elements, state, size, input and port
 * @returns the rows
 */
const diffClient: ClientModule<DiffClientProps, State> = (props, surface) => {
  current = props

  const { Box, Text } = surface.elements

  if (surface.state === undefined) {
    surface.onPointer(event => onPointer(event, surface))
    surface.onKey(event => onKey(event, surface))
    surface.setState(IDLE)
  }

  const state = surface.state ?? IDLE
  const dragged = rangeOf(state)

  const rows: RenderElement[] = props.lines.map((line, index) => {
    const at = props.offset + index
    const isDragged = dragged !== null && at >= dragged.from && at <= dragged.to
    const isArmed = props.armed.some(range => at >= range.from && at <= range.to)

    return (
      <Box flexDirection="row">
        <Text color="cyan">{isArmed ? ARMED_MARK : NO_MARK}</Text>
        <Text
          wrap="truncate-end"
          {...(isDragged ? { inverse: true } : {})}
          {...(line.color !== undefined ? { color: line.color } : {})}
          {...(line.bold ? { bold: true } : {})}
          {...(line.dim ? { dimColor: true } : {})}
        >
          {line.text === '' ? ' ' : line.text}
        </Text>
      </Box>
    )
  })

  return <Box flexDirection="column">{rows}</Box>
}

function rangeOf(state: State): LineRange | null {
  if (state.anchor === null || state.focus === null) {
    return null
  }

  return {
    from: Math.min(state.anchor, state.focus),
    to: Math.max(state.anchor, state.focus),
  }
}

/** The content line under a region row, clamped to the window. */
function lineAt(y: number): number {
  const last = Math.max(0, current.lines.length - 1)

  return current.offset + Math.max(0, Math.min(last, y))
}

function onPointer(event: ClientPointerEvent, surface: ClientSurface<State>) {
  const state = surface.state ?? IDLE
  const at = lineAt(event.y)

  if (event.type === 'down' && event.button === 'left') {
    surface.setState({ anchor: at, focus: at, isDrag: false })

    return
  }

  if (state.anchor === null) {
    return
  }

  if (event.type === 'move' && event.button === 'left') {
    if (state.focus !== at || !state.isDrag) {
      surface.setState({ ...state, focus: at, isDrag: true })
    }

    return
  }

  if (event.type === 'up') {
    const from = Math.min(state.anchor, at)
    const to = Math.max(state.anchor, at)
    const isOnArmed = current.armed.some(range => at >= range.from && at <= range.to)

    if (state.isDrag) {
      surface.post({ kind: 'select', from, to } satisfies DiffClientPost)
    } else if (isOnArmed) {
      surface.post({ kind: 'click', at } satisfies DiffClientPost)
    }

    surface.setState(IDLE)
  }
}

function onKey(event: ClientKeyEvent, surface: ClientSurface<State>) {
  const post = postForKey(event, Math.max(1, surface.rows))

  if (post !== null) {
    surface.post(post)
  }
}

/**
 * What a key in the region asks of the hooks module, or null for a key
 * the region ignores.
 *
 * @param event the key
 * @param page the rows a page key moves
 * @returns the post, or null
 */
export function postForKey(event: ClientKeyEvent, page: number): DiffClientPost | null {
  if (event.key === 'a' && !event.ctrl && !event.meta) {
    return { kind: 'ask' }
  }

  if (event.key === 'tab') {
    return { kind: 'move', by: event.shift ? -1 : 1, wrap: true }
  }

  if (event.ctrl && (event.key === 'down' || event.key === 'up')) {
    return { kind: 'move', by: event.key === 'down' ? 1 : -1 }
  }

  const by =
    event.key === 'down' || event.key === 'j'
      ? 1
      : event.key === 'up' || event.key === 'k'
        ? -1
        : event.key === 'pagedown'
          ? page
          : event.key === 'pageup'
            ? -page
            : null

  return by === null ? null : { kind: 'scroll', by }
}

export default diffClient
