/**
 * The plugin's names and the lines it prints, in one place.
 */
export const PLUGIN_NAME = 'commits'
export const COMMAND_NAME = 'commits'
export const TOOL_NAME = 'show'
export const TOOL_FULL_NAME = `mcp__${PLUGIN_NAME}__${TOOL_NAME}`
export const CLOSE_TOOL_NAME = 'close'
export const CLOSE_TOOL_FULL_NAME = `mcp__${PLUGIN_NAME}__${CLOSE_TOOL_NAME}`
export const PANE_ID = 'commits'
export const PANE_TITLE = 'Commits'

export const COMMAND_DESCRIPTION =
  "Show the branch's commits in a pane beside the transcript"
export const ARGUMENT_HINT = '[range]'

export const TOOL_DESCRIPTION =
  'Open the commits pane beside the transcript to show the user commits. ' +
  'With no input it shows the current branch: the commits not on the default ' +
  'branch. `range` is a git revision range such as main..HEAD or ' +
  'HEAD~3..HEAD. `commits` lists commits to show by sha, in order, each with ' +
  'an optional `note` drawn under it: a question for the user, or why it is ' +
  'shown. The user reads the commits, selects one, and may attach its ' +
  'message and diff to their next prompt.'

export const CLOSE_TOOL_DESCRIPTION =
  'Close the commits pane, if it is open.'

export const NOT_IN_REPOSITORY_TEXT =
  "The commits pane isn't in a git repository"
export const NEEDS_FULLSCREEN_TEXT =
  'The commits pane needs the fullscreen layout; it does not open on the main screen'
export const PANEL_SHOWN_TEXT = 'Commits panel shown'
export const PANEL_HIDDEN_TEXT = 'Commits panel hidden'
export const MAIN_LOOP_ONLY_TEXT =
  'The commits pane opens for the main conversation only, not from a subagent'

/** The key that arms or disarms the selected commit while the pane has the keys. */
export const ASK_HOTKEY = 'a'

/** The key help centred in the header. */
export const HELP_TEXT = 'Tab or ctrl+↑/↓ to select, arrows to read'

/**
 * The engine's list-scrolling actions (ctrl+up/down, opt+up/down from the
 * prompt), declared on two empty Buttons so their chords move the selection.
 * The built-in diff pane declares the same two; with both panes open the
 * engine gives the chord to the pane drawn last.
 */
export const LIST_UP_ACTION = 'app:diffFileListUp'
export const LIST_DOWN_ACTION = 'app:diffFileListDown'

/**
 * The ask button's label, static help text; the engine draws the hotkey and
 * a colon before it. An armed commit shows only by its gutter mark.
 */
export const ASK_LABEL = 'ask about commit'

/** How an attached commit's context block begins; the model is told to expect it. */
export const ATTACHED_LEAD = 'The user attached commit'

/** How an attached line range's context block begins. */
export const ATTACHED_LEAD_LINES = 'The user attached'
