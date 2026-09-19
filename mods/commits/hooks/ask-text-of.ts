import type { Commit, FileDiff } from './git'
import { cutAtLine } from './git'
import type { Line, LineKind } from './pane-view'
import { ATTACHED_LEAD, ATTACHED_LEAD_LINES } from './names'

/** The most characters an attached commit takes of the prompt's context. */
export const ASK_MAX_CHARS = 60000

const CUT_NOTE = '[cut: the diff was longer than the commits pane attaches]'

/** What ends a whole-commit block cut short, so its elements still close. */
const CUT_TAIL = `${CUT_NOTE}\n</commit-diff>\n</commit>`

/**
 * The context block the next prompt carries for a commit: a lead saying
 * where it came from, then a `commit` element holding the subject, the
 * message and one `commit-diff` element per file with its hunks as `git
 * show` prints them, cut at ASK_MAX_CHARS.
 *
 * @param commit the commit
 * @param files its patch as the pane holds it, empty when not loaded
 * @returns the block
 */
export function askTextOf(commit: Commit, files: readonly FileDiff[]): string {
  const message =
    commit.body === '' ? [] : ['<commit-message>', commit.body, '</commit-message>']

  const text = [
    `${ATTACHED_LEAD} ${commit.short} from the commits pane to this prompt; each ` +
      `commit-diff holds one file's hunks as git show prints them.`,
    `<commit sha="${commit.sha}" author="${attributeOf(commit.author)}" date="${commit.date}">`,
    `<commit-subject>${commit.subject}</commit-subject>`,
    ...message,
    ...files.flatMap(fileElementOf),
    '</commit>',
  ].join('\n')

  if (text.length <= ASK_MAX_CHARS) {
    return text
  }

  return `${cutAtLine(text, ASK_MAX_CHARS - CUT_TAIL.length - 1)}\n${CUT_TAIL}`
}

function fileElementOf(file: FileDiff): string[] {
  const path = `path="${attributeOf(file.path)}"`

  if (file.hunks === '') {
    const note = file.note === undefined ? 'no text diff' : bareNoteOf(file.note)

    return [`<commit-diff ${path} note="${attributeOf(note)}"/>`]
  }

  const cut = file.isCut ? ' cut="true"' : ''

  return [`<commit-diff ${path}${cut}>`, file.hunks.replace(/\n$/, ''), '</commit-diff>']
}

/** The lead's second sentence, present when a run of diff lines follows. */
const LINES_LEGEND =
  'In commit-diff, new-lines are line numbers in the file at new-rev (its + and context ' +
  'lines) and old-lines in the file at old-rev (its - and context lines); git show ' +
  '<rev>:<path> prints either version.'

/**
 * The context block the next prompt carries for lines the person dragged
 * over in a commit's content: a lead naming the commit, then a
 * `commit-lines` element holding one element per run of lines, named for
 * the part of the commit it is from. A `commit-diff` run carries its file
 * and, computed from the hunk header, the lines' numbers in the file at the
 * commit and, when the run has removed lines, at the parent, so the model
 * can read around them with `git show` without finding them in the diff
 * again.
 *
 * @param commit the commit
 * @param lines the selected lines, in order, as the pane laid them out
 * @returns the block
 */
export function linesTextOf(commit: Commit, lines: readonly Line[]): string {
  const runs = runsOf(lines.filter(isKept))
  const hasNumbers = runs.some(run => run.kind === 'diff')

  return [
    `${ATTACHED_LEAD_LINES} lines of commit ${commit.short} from the commits pane to this prompt.` +
      (hasNumbers ? ` ${LINES_LEGEND}` : ''),
    `<commit-lines sha="${commit.short}" subject="${attributeOf(commit.subject)}">`,
    ...runs.flatMap(run => elementOf(run, commit)),
    '</commit-lines>',
  ].join('\n')
}

/** A content line the block quotes: every kind but the pane's blank rows. */
type KeptLine = Line & { kind: Exclude<LineKind, 'blank'> }

function isKept(line: Line): line is KeptLine {
  return line.kind !== 'blank'
}

/**
 * Consecutive lines of one part of the commit. A `file` run holds a file
 * header and any hunk headers the drag took without lines of theirs; when
 * lines of that file follow, the run becomes theirs.
 */
type Run = {
  kind: Exclude<LineKind, 'blank' | 'hunk'>
  path: string | undefined
  hunk: string | undefined
  lines: Line[]
}

function runsOf(lines: readonly KeptLine[]): Run[] {
  const runs: Run[] = []

  for (const line of lines) {
    const last = runs.at(-1)

    if (line.kind === 'file' || line.kind === 'hunk') {
      const quoted = line.kind === 'hunk' ? [line] : []

      if (last?.kind === 'file' && last.path === line.path) {
        last.lines.push(...quoted)
      } else {
        runs.push({ kind: 'file', path: line.path, hunk: undefined, lines: quoted })
      }
    } else if (last?.kind === 'file' && last.path === line.path && line.path !== undefined) {
      last.kind = line.kind
      last.hunk = line.hunk
      last.lines = [line]
    } else if (last?.kind === line.kind && last.path === line.path && last.hunk === line.hunk) {
      last.lines.push(line)
    } else {
      runs.push({ kind: line.kind, path: line.path, hunk: line.hunk, lines: [line] })
    }
  }

  return runs
}

function elementOf(run: Run, commit: Commit): string[] {
  const texts = run.lines.map(line => line.text)
  const path = run.path === undefined ? '' : ` path="${attributeOf(run.path)}"`

  switch (run.kind) {
    case 'subject':
      return [`<commit-subject>${commit.subject}</commit-subject>`]
    case 'author':
      return [`<commit-author>${commit.author}, ${commit.date}</commit-author>`]
    case 'message':
      return ['<commit-message>', ...texts, '</commit-message>']
    case 'note': {
      const note = attributeOf(bareNoteOf(texts.join(' ')))

      return run.path === undefined
        ? [`<commit-note>${note}</commit-note>`]
        : [`<commit-diff${path} note="${note}"/>`]
    }
    case 'file':
      return texts.length === 0
        ? [`<commit-diff${path}/>`]
        : [`<commit-diff${path}>`, ...texts, '</commit-diff>']
    case 'diff':
      return [`<commit-diff${path}${whereOf(run, commit)}>`, ...texts, '</commit-diff>']
  }
}

/**
 * The attributes placing a run of diff lines in the file, one pair per
 * version the run touches: the lines' span at the commit (its `+` and
 * context lines) and, when the run removes lines, their span at the parent,
 * `<sha>^` (its `-` and context lines).
 */
function whereOf(run: Run, commit: Commit): string {
  const at = spanOf(run.lines.flatMap(line => (line.newLine === undefined ? [] : [line.newLine])))
  const was = spanOf(run.lines.flatMap(line => (line.oldLine === undefined ? [] : [line.oldLine])))
  const hasRemoved = run.lines.some(line => line.text.startsWith('-'))

  const atCommit = at === undefined ? '' : ` new-rev="${commit.short}" new-lines="${at}"`
  const atParent =
    was === undefined || !hasRemoved ? '' : ` old-rev="${commit.short}^" old-lines="${was}"`

  return `${atCommit}${atParent}`
}

function spanOf(numbers: readonly number[]): string | undefined {
  if (numbers.length === 0) {
    return undefined
  }

  const from = Math.min(...numbers)
  const to = Math.max(...numbers)

  return from === to ? `${from}` : `${from}-${to}`
}

/** A pane note without the parentheses it shows in: `(binary)` → `binary`. */
function bareNoteOf(note: string): string {
  return note.replace(/^\((.*)\)$/, '$1')
}

/** Text safe inside a double-quoted attribute. */
function attributeOf(text: string): string {
  return text.replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;')
}
