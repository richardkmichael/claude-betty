import type { Commit, FileDiff } from './git'
import { ATTACHED_LEAD, ATTACHED_LEAD_LINES } from './names'

/** The most characters an attached commit takes of the prompt's context. */
export const ASK_MAX_CHARS = 60000

const CUT_NOTE = '[cut: the diff was longer than the commits pane attaches]'

/**
 * The context block the next prompt carries for a commit: a lead saying
 * where it came from, the commit as `git log` prints it, then each file's
 * hunks, cut at ASK_MAX_CHARS.
 *
 * @param commit the commit
 * @param files its patch as the pane holds it, empty when not loaded
 * @returns the block
 */
export function askTextOf(commit: Commit, files: readonly FileDiff[]): string {
  const body =
    commit.body === ''
      ? []
      : ['', ...commit.body.split('\n').map(line => `    ${line}`)]

  const head = [
    `${ATTACHED_LEAD} ${commit.sha} from the commits pane to this prompt:`,
    `commit ${commit.sha}`,
    `Author: ${commit.author}`,
    `Date:   ${commit.date}`,
    '',
    `    ${commit.subject}`,
    ...body,
    '',
  ]

  const patch = files.flatMap(file => [
    `=== ${file.path} ===`,
    file.hunks === '' ? '(no text diff)' : file.hunks,
    ...(file.isCut ? ['(cut)'] : []),
  ])

  const text = [...head, ...patch].join('\n')

  if (text.length <= ASK_MAX_CHARS) {
    return text
  }

  const room = text.slice(0, ASK_MAX_CHARS - CUT_NOTE.length - 1)
  const kept = room.slice(0, Math.max(room.lastIndexOf('\n'), 0))

  return `${kept}\n${CUT_NOTE}`
}

/**
 * The context block the next prompt carries for lines the person dragged
 * over in a commit's content: a lead saying where they came from, the
 * commit, the file when the lines are a file's, then the lines themselves.
 *
 * @param commit the commit
 * @param lines the selected lines' text, in order
 * @param path the file the first selected line belongs to, if any
 * @returns the block
 */
export function linesTextOf(
  commit: Commit,
  lines: readonly string[],
  path: string | undefined,
): string {
  const count = lines.length === 1 ? '1 line' : `${lines.length} lines`

  return [
    `${ATTACHED_LEAD_LINES} ${count} from commit ${commit.sha} from the commits pane to this prompt:`,
    `commit ${commit.sha}`,
    `    ${commit.subject}`,
    ...(path === undefined ? [] : [`=== ${path} ===`]),
    ...lines,
  ].join('\n')
}
