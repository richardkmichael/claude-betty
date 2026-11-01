import type { Register, SessionRateLimit, SessionUsage } from 'claude-code'

// The user's statusLine script, run with the windows it caches standing in for the rate limits the
// desktop session does not report, printing its parts for the band to lay out.
const STATUSLINE = ['sh', '-c', 'exec "$HOME/.claude/statusline.py" --cached-limits --sections']

// What `--sections` prints.
type Sections = { context: string; quota: string; where: string; who: string }

// Escape sequences the terminal would draw (colours, OSC 8 links); a Text shows them raw.
const ANSI = /\x1b\](?:.*?)(?:\x1b\\|\x07)|\x1b\[[0-9;]*[A-Za-z]/g

// The payload's rate_limits shape: epoch seconds, keyed by window.
function rateLimits(limits: SessionRateLimit[]): Record<string, object> | undefined {
  const entries = limits.map(limit => [
    limit.kind,
    {
      used_percentage: limit.percentUsed,
      resets_at: limit.resetsAt ? Date.parse(limit.resetsAt) / 1000 : undefined,
    },
  ])

  return entries.length > 0 ? Object.fromEntries(entries) : undefined
}

// The fields of the statusLine payload the script reads. There is intentionally no session_id, so
// the script records nothing for the quota server from a payload partly filled from its cache. The
// model is left out too, because Desktop shows it under the prompt.
function payload(usage: SessionUsage, cwd: string): object {
  return {
    workspace: { current_dir: cwd },
    context_window: {
      context_window_size: usage.context.window,
      total_input_tokens: usage.context.tokens,
      used_percentage: usage.context.percent,
    },
    cost: usage.cost ? { total_cost_usd: usage.cost.usd } : undefined,
    rate_limits: rateLimits(usage.rateLimits),
  }
}

export const register: Register = on => {
  // The figures change with each API response, so the band is redrawn once a turn ends.
  on('turn.complete', ($, e, next) => {
    $.ui.invalidate('ui.render')

    return next(e)
  })

  // The terminal draws the user's statusLine command; Desktop has no statusLine, so the band stands
  // in for it there alone.
  on('ui.render', { component: 'AbovePrompt' }, async ($, e, next) => {
    if (e.surface !== 'desktop' || e.props.hasSurvey) {
      return next(e)
    }

    const [usage, cwd] = await Promise.all([$.session.usage(), $.session.cwd()])
    const { exitCode, stdout, stderr } = await $.process.run(STATUSLINE, {
      stdin: JSON.stringify(payload(usage, cwd)),
      timeoutMs: 5000,
    })
    const output = stdout.replace(ANSI, '').trim()

    if (exitCode === 0 && output === '') {
      return next(e)
    }

    const { Box, Text } = $.ui.resolve(e)

    let parts: Sections
    try {
      parts = JSON.parse(output) as Sections
    } catch {
      // The script prints its own error line in place of the JSON when it fails.
      const error = exitCode === 0 ? output : `statusline.py exited ${exitCode}: ${stderr.trim()}`

      return <Text color="error">{error}</Text>
    }

    const centre = [parts.quota, parts.where].filter(Boolean).join('  ')

    // The two sides grow from no width alike, so they stay equal and the quota stays centred.
    return (
      <Box flexDirection="row" width="100%">
        <Box flexGrow={1} width={0}>
          <Text wrap="truncate">{parts.context}</Text>
        </Box>
        <Box flexShrink={0}>
          <Text tabular>{centre}</Text>
        </Box>
        <Box flexGrow={1} width={0} justifyContent="flex-end">
          <Text wrap="truncate">{parts.who}</Text>
        </Box>
      </Box>
    )
  })
}
