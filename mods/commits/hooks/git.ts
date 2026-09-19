import type { ProcessRunInit, ProcessRunResult } from 'claude-code'

/**
 * Runs a child process: `$.process.run` as the plugin binds it.
 */
export type Run = (
  argv: readonly string[],
  init?: ProcessRunInit,
) => Promise<ProcessRunResult>

/**
 * One commit as `git log` lists it.
 */
export type Commit = {
  sha: string
  short: string
  author: string
  date: string
  subject: string
  body: string
}

/**
 * One file's part of a commit's patch: its path and its hunks from the first
 * `@@`, empty when the file has no text diff (binary, a pure rename).
 */
export type FileDiff = {
  path: string
  hunks: string
  isCut: boolean
  /** Why there are no hunks, when the header says: a rename, a binary. */
  note?: string
}

/**
 * What `git log` is asked for: the label the pane shows and the arguments
 * after the format.
 */
export type Selection = {
  label: string
  argv: readonly string[]
}

/** Separators git expands in the log format, so a field may hold newlines. */
const FIELD = '\x1f'
const RECORD = '\x1e'
const LOG_FORMAT = '--format=%H%x1f%h%x1f%an%x1f%as%x1f%s%x1f%b%x1e'

/** The most characters a `Code` element draws. */
export const CODE_MAX_CHARS = 10000

/** How many commits are listed when no default branch resolves. */
const FALLBACK_COUNT = 20

/**
 * Whether a revision spec typed by a person or the model is safe to hand git
 * as one argument: not an option, no whitespace or control characters.
 *
 * @param text the spec
 * @returns true when git may take it as a revision
 */
export function isSafeRevspec(text: string): boolean {
  return (
    text.length > 0 &&
    text.length <= 200 &&
    !text.startsWith('-') &&
    !/[\s\x00-\x1f\x7f]/.test(text)
  )
}

/**
 * The repository's top-level directory, or null outside one.
 *
 * @param run the process runner
 * @param cwd the directory git runs in
 * @returns the top level, or null
 */
export async function toplevelOf(
  run: Run,
  cwd: string,
): Promise<string | null> {
  const result = await run(['git', 'rev-parse', '--show-toplevel'], { cwd })

  return result.exitCode === 0 ? result.stdout.trim() : null
}

/**
 * The branch's commits: those not on the default branch when one resolves
 * (`origin/HEAD`, then a local `main` or `master`), else the last few.
 *
 * @param run the process runner
 * @param cwd the directory git runs in
 * @returns the selection
 */
export async function branchSelectionOf(
  run: Run,
  cwd: string,
): Promise<Selection> {
  const base = await defaultBranchOf(run, cwd)

  if (base === null) {
    return {
      label: `last ${FALLBACK_COUNT} commits`,
      argv: ['-n', String(FALLBACK_COUNT), 'HEAD'],
    }
  }

  const range = `${base}..HEAD`

  return { label: range, argv: [range] }
}

async function defaultBranchOf(run: Run, cwd: string): Promise<string | null> {
  const remote = await run(
    ['git', 'symbolic-ref', '--quiet', '--short', 'refs/remotes/origin/HEAD'],
    { cwd },
  )

  if (remote.exitCode === 0 && remote.stdout.trim() !== '') {
    return remote.stdout.trim()
  }

  for (const name of ['main', 'master']) {
    const local = await run(
      ['git', 'rev-parse', '--verify', '--quiet', `refs/heads/${name}`],
      { cwd },
    )

    if (local.exitCode === 0) {
      return name
    }
  }

  return null
}

/**
 * A range as typed: `main..HEAD`, `HEAD~3..HEAD`.
 *
 * @param range the revision range
 * @returns the selection
 */
export function rangeSelectionOf(range: string): Selection {
  return { label: range, argv: [range] }
}

/**
 * Named commits, in the order given.
 *
 * @param shas the commits
 * @returns the selection
 */
export function commitsSelectionOf(shas: readonly string[]): Selection {
  const label = shas.length === 1 ? '1 commit' : `${shas.length} commits`

  return { label, argv: ['--no-walk=unsorted', ...shas] }
}

/**
 * The commits a selection names, newest first for a range.
 *
 * @param run the process runner
 * @param cwd the directory git runs in
 * @param selection what to list
 * @returns the commits; rejects with git's message when the log fails
 */
export async function logOf(
  run: Run,
  cwd: string,
  selection: Selection,
): Promise<Commit[]> {
  const result = await run(
    ['git', 'log', '--no-color', LOG_FORMAT, ...selection.argv],
    { cwd },
  )

  if (result.exitCode !== 0) {
    throw new Error(result.stderr.trim() || `git log exited ${result.exitCode}`)
  }

  return parseLog(result.stdout)
}

/**
 * The commits in `git log`'s output under LOG_FORMAT: one record per commit,
 * fields separated by FIELD, records by RECORD.
 *
 * @param stdout the log's output
 * @returns the commits
 */
export function parseLog(stdout: string): Commit[] {
  return stdout
    .split(RECORD)
    .map(record => record.replace(/^\n/, ''))
    .filter(record => record.includes(FIELD))
    .map(record => {
      const [sha = '', short = '', author = '', date = '', subject = '', body = ''] =
        record.split(FIELD)

      return { sha, short, author, date, subject, body: body.trim() }
    })
}

/**
 * A commit's patch, one entry per file.
 *
 * @param run the process runner
 * @param cwd the directory git runs in
 * @param sha the commit
 * @returns the files; rejects with git's message when the show fails
 */
export async function diffOf(
  run: Run,
  cwd: string,
  sha: string,
): Promise<FileDiff[]> {
  const result = await run(
    ['git', 'show', '--no-color', '--format=', '--patch', sha],
    { cwd },
  )

  if (result.exitCode !== 0) {
    throw new Error(result.stderr.trim() || `git show exited ${result.exitCode}`)
  }

  return parseShow(result.stdout)
}

/**
 * The files in `git show --patch` output, each with its hunks from the first
 * `@@`, cut at CODE_MAX_CHARS on a line boundary.
 *
 * @param stdout the show's output
 * @returns the files
 */
export function parseShow(stdout: string): FileDiff[] {
  return stdout
    .replace(/\r/g, '')
    .split(/^diff --git /m)
    .filter(chunk => chunk.trim() !== '')
    .map(chunk => {
      const newline = chunk.indexOf('\n')
      const header = newline === -1 ? chunk : chunk.slice(0, newline)
      const at = chunk.indexOf('\n@@')
      const hunks = at === -1 ? '' : sanitize(chunk.slice(at + 1))
      const isCut = hunks.length > CODE_MAX_CHARS
      const note = hunks === '' ? noteOf(chunk) : undefined

      return {
        path: pathOf(header),
        hunks: isCut ? cutAtLine(hunks, CODE_MAX_CHARS) : hunks,
        isCut,
        ...(note === undefined ? {} : { note }),
      }
    })
}

/**
 * What a file's extended header says about a diff with no hunks.
 *
 * @param chunk the file's part of the patch
 * @returns the note, or undefined when the header says nothing
 */
function noteOf(chunk: string): string | undefined {
  const renamed = /^rename from (.+)$/m.exec(chunk)

  if (renamed) {
    return `(renamed from ${sanitize(renamed[1] ?? '')})`
  }

  if (/^Binary files /m.test(chunk)) {
    return '(binary)'
  }

  return undefined
}

function pathOf(header: string): string {
  const at = header.lastIndexOf(' b/')

  return at === -1 ? header : header.slice(at + 3)
}

/**
 * The text with every control character but tab and newline replaced, since
 * `Code` and `Text` refuse them.
 *
 * @param text the text
 * @returns the text with `?` in each one's place
 */
export function sanitize(text: string): string {
  return text.replace(/[\x00-\x08\x0b-\x1f\x7f]/g, '?')
}

/**
 * The text's first lines that fit in `max` characters.
 *
 * @param text the text
 * @param max the most characters kept
 * @returns the kept lines
 */
export function cutAtLine(text: string, max: number): string {
  const cut = text.slice(0, max)
  const at = cut.lastIndexOf('\n')

  return at <= 0 ? cut : cut.slice(0, at)
}
