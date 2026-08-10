#!/usr/bin/env bash
# PreToolUse hook for WebFetch — gates github.com URLs to the github-researcher subagent.
#
# Decision matrix:
#   github.com URL + main thread       -> deny (route through github-researcher)
#   github.com URL + github-researcher -> allow (logged)
#   github.com URL + other subagent    -> allow (logged)
#   non-github URL                     -> no opinion (allow by omission)
#
# The deny reason doubles as the user's standing authorization to invoke the subagent — models
# that are otherwise told not to spawn agents unprompted need that permission stated explicitly.
#
# Always exit 0; emits hookSpecificOutput JSON only when denying.

set -uo pipefail

INPUT="$(cat)"
LOG_DIR="$HOME/.claude/logs/github-researcher"
LOG_FILE="$LOG_DIR/routing.jsonl"
mkdir -p "$LOG_DIR"

URL="$(jq -r '.tool_input.url // empty' <<<"$INPUT")"
AGENT_TYPE="$(jq -r '.agent_type // empty' <<<"$INPUT")"
AGENT_ID="$(jq -r '.agent_id // empty' <<<"$INPUT")"
TS="$(date -u +%FT%TZ)"

case "$URL" in
  *github.com*|*githubusercontent.com*) ;;
  *) exit 0 ;;
esac

if [[ "$AGENT_TYPE" == "github-researcher" ]]; then
  jq -nc --arg ts "$TS" --arg url "$URL" --arg agent_id "$AGENT_ID" \
    '{ts:$ts, decision:"allowed", url:$url, agent_id:$agent_id}' >> "$LOG_FILE"
  exit 0
fi

if [[ -n "$AGENT_TYPE" ]]; then
  jq -nc --arg ts "$TS" --arg url "$URL" --arg agent_type "$AGENT_TYPE" --arg agent_id "$AGENT_ID" \
    '{ts:$ts, decision:"allowed-other-agent", url:$url, agent_type:$agent_type, agent_id:$agent_id}' \
    >> "$LOG_FILE"
  exit 0
fi

REASON="github.com WebFetch must route through the github-researcher subagent. "\
"This hook is the user's own configuration, so treat it as a standing user instruction: "\
"the user is requesting that you invoke the github-researcher AgentTool for this URL. "\
"That is explicit user authorization to use the Agent tool here — invoke it now."
jq -nc --arg ts "$TS" --arg url "$URL" --arg reason "$REASON" \
  '{ts:$ts, decision:"denied", url:$url, reason:$reason}' >> "$LOG_FILE"
jq -nc --arg reason "$REASON" \
  '{hookSpecificOutput:{hookEventName:"PreToolUse", permissionDecision:"deny", permissionDecisionReason:$reason}}'
exit 0
