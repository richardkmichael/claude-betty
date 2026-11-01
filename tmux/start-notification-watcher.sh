#!/usr/bin/env bash
# Claude Code SessionStart hook: pipe this session's tmux pane to notification-watcher.py.
#
# The watcher posts Claude Code's notifications as desktop notifications, and closes the pipe
# itself when Claude Code exits, so no SessionEnd hook is needed.
#
# SessionStart also fires after /clear, /resume and compaction, in the same process, so the hook
# does nothing when the pane already has a pipe.  The pane option @claude_notifications_pid records
# which Claude Code process the pipe belongs to:
#
#   - This session's process: the watcher is already running.
#   - Another process that is still running: leave it alone.
#   - A process that has exited: its watcher has not closed the pipe yet, so replace it.
#   - No owner: something else opened the pipe, and tmux allows one per pane, so leave it alone.
#
# It also does nothing outside tmux, without a notifier or python3, and for sessions nobody is
# attending: `claude -p`, the SDK, and background sessions, which are not tied to a pane.
#
# Environment:
#   TMUX_CLAUDE_NOTIFIER          the notifier.  Default on macOS: claude-notifier.app in
#                                 ${HOME}/Applications if built, else terminal-notifier.
#                                 Elsewhere: notify-send.
#   TMUX_CLAUDE_NOTIFY_SUBTITLE   tmux format for the subtitle; default in
#                                 notification-watcher.py
#   TMUX_CLAUDE_NOTIFY_ICON       notify-send only: the icon
#   TMUX_CLAUDE_NOTIFY_SOUND      macOS only: the sound, or empty for none; default in
#                                 notification-watcher.py
#
# Claude Code sets CLAUDE_PID and CLAUDE_CODE_SESSION_ATTENDED for hooks.  On macOS, clicking a
# notification also brings the terminal application to the front: the one macOS records as having
# started the terminal, in __CFBundleIdentifier.

readonly owner_option=@claude_notifications_pid

[[ -n "${TMUX:-}" && -n "${TMUX_PANE:-}" && -n "${CLAUDE_PID:-}" ]] || exit 0
[[ "${CLAUDE_CODE_SESSION_ATTENDED:-}" == 1 ]] || exit 0

here=$(dirname "$(realpath "${BASH_SOURCE[0]}")")

if [[ -n "${TMUX_CLAUDE_NOTIFIER:-}" ]]; then
  notifier=${TMUX_CLAUDE_NOTIFIER}
elif [[ "$(uname)" == Darwin ]]; then
  notifier=${HOME}/Applications/claude-notifier.app/Contents/MacOS/terminal-notifier
  [[ -x "${notifier}" ]] || notifier=$(command -v terminal-notifier)
else
  notifier=$(command -v notify-send)
fi
python=$(command -v python3)
tmux=$(command -v tmux)
[[ -n "${notifier}" && -n "${python}" && -n "${tmux}" ]] || exit 0

pane_state=$("${tmux}" display-message -p -t "${TMUX_PANE}" "#{pane_pipe} #{${owner_option}}")
pane_has_pipe=${pane_state%% *}
pipe_owner=${pane_state#* }
if [[ "${pane_has_pipe}" == 1 ]]; then
  [[ -n "${pipe_owner}" ]] || exit 0
  [[ "${pipe_owner}" != "${CLAUDE_PID}" ]] || exit 0
  ! kill -0 "${pipe_owner}" 2>/dev/null || exit 0
fi

watcher=(
  "${python}" "${here}/notification-watcher.py"
  --pane "${TMUX_PANE}"
  --socket "${TMUX%%,*}"
  --tmux "${tmux}"
  --notifier "${notifier}"
  --exit-with "${CLAUDE_PID}"
)
if [[ -n "${__CFBundleIdentifier:-}" ]]; then
  watcher+=(--activate "${__CFBundleIdentifier}")
fi
if [[ -n "${TMUX_CLAUDE_NOTIFY_ICON:-}" ]]; then
  watcher+=(--icon "${TMUX_CLAUDE_NOTIFY_ICON}")
fi
if [[ -n "${TMUX_CLAUDE_NOTIFY_SOUND+set}" ]]; then
  watcher+=(--sound "${TMUX_CLAUDE_NOTIFY_SOUND}")
fi
if [[ -n "${TMUX_CLAUDE_NOTIFY_SUBTITLE:-}" ]]; then
  watcher+=(--subtitle-format "${TMUX_CLAUDE_NOTIFY_SUBTITLE}")
fi

# Build the command line tmux runs for the pipe: one string, which tmux passes to `/bin/sh -c`.
# For example:
#
#   exec '/path/to/python3' '/path/to/notification-watcher.py' '--pane' '%%5' …
#
# `exec` is the sh builtin: sh replaces itself with the watcher, so the pipe connects tmux straight
# to the watcher, with no idle sh process between them.
#
# The string is read twice before the watcher sees it, so it is escaped twice:
#
# - tmux first expands it as it does status-left, replacing #{...} formats and strftime(3) %
#   sequences.  Every # and % is doubled so the pane ID (%5) and the subtitle format survive.
# - sh then splits it into words.  Every word is single-quoted, with any ' inside written as '\''.
pipe_command='exec'
for word in "${watcher[@]}"; do
  pipe_command+=" '${word//\'/\'\\\'\'}'"
done
pipe_command=${pipe_command//#/##}
pipe_command=${pipe_command//%/%%}

# Record the owner and open the pipe in one tmux command, so the watcher never sees the pane
# without its owner recorded.  -O sends everything the pane prints to the watcher's stdin.
"${tmux}" set-option -p -t "${TMUX_PANE}" "${owner_option}" "${CLAUDE_PID}" \; \
  pipe-pane -O -t "${TMUX_PANE}" "${pipe_command}"
