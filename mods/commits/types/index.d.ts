/**
 * The `show` tool's input as the model calls it: declared on `McpToolInputs`
 * so the `tool.call` hook on `mcp__commits__show`, and a `$.tool.call` of
 * it, type against it. Nothing here is imported, so it stands on its own;
 * the hook still checks the input at run time, since the model may send
 * anything.
 */
export type CommitsShowInput = {
  /**
   * A git revision range to list, newest first (`main..HEAD`); absent with
   * `commits`, and with neither the branch is listed.
   */
  range?: string
  /**
   * Commits to show in this order, each by sha with an optional note drawn
   * under it.
   */
  commits?: readonly CommitsShowEntry[]
}

/**
 * One commit the model names: its sha, or a prefix of it, and the note.
 */
export type CommitsShowEntry = {
  sha: string
  note?: string
}

declare module 'claude-code' {
  interface McpToolInputs {
    mcp__commits__show: CommitsShowInput
    /** Takes no input. */
    mcp__commits__close: Record<string, unknown>
  }
}
