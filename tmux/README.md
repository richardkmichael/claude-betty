# Claude Code notifications under tmux

Claude Code's desktop notifications (a permission prompt waiting, idle for input, a background agent
finished) reach the desktop from inside tmux, with any terminal application. Each notification names
the pane's project, session, window and pane, and clicking it switches tmux to that pane. It works
on macOS and Linux.

## How it works

Claude Code writes a notification to its terminal as an escape sequence, and leaves displaying it to
the terminal. Most terminals, Alacritty among them, ignore the sequence. Under tmux, Claude Code
also wraps the sequence for tmux, which passes it on only with `allow-passthrough` set.

Here the sequence goes to a separate program instead:

```
claude  ->  tmux pane  ->  pipe-pane copy  ->  notification-watcher.py  ->  notifier  ->  desktop
```

- `start-notification-watcher.sh` runs as Claude Code's `SessionStart` hook. It tells tmux to copy
  the session's pane output to `notification-watcher.py` with `pipe-pane`.
- `notification-watcher.py` finds each notification in the copy and posts it: with
  [terminal-notifier](https://github.com/julienXX/terminal-notifier) or a build of it on macOS, and
  with `notify-send` elsewhere.
- When Claude Code exits, for any reason including a crash, the watcher closes the pane's pipe and
  exits. It waits on Claude Code's process directly, so no `SessionEnd` hook is needed.

tmux copies the output before processing it, so notifications from panes in windows and sessions you
are not looking at arrive too, and no tmux configuration is needed.

A notification has four parts:

| Part     | Content                                     | From             |
| -------- | ------------------------------------------- | ---------------- |
| Header   | The notifier app's name and icon            | The notifier app |
| Title    | Claude Code                                 | Claude Code      |
| Subtitle | The pane's directory, session, window, pane | The watcher      |
| Body     | For example "Claude needs your permission"  | Claude Code      |

`notify-send` has no subtitle, so on Linux the subtitle is the first line of the body. On macOS, a
newer notification from a pane replaces the older one.

## Requirements

- tmux
- Python 3.9 or later, as `python3` on `PATH`. Standard library only.
- Claude Code 2.1.78 or later
- A notifier: terminal-notifier on macOS, `notify-send` (libnotify) elsewhere. Clicking a
  notification on Linux needs libnotify 0.7.10 or later.

## Setup

### 1. Configure Claude Code

Add to `~/.claude/settings.json`, with the path to this directory:

```json
{
  "preferredNotifChannel": "ghostty",
  "hooks": {
    "SessionStart": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "/path/to/this-repository/tmux/start-notification-watcher.sh"
          }
        ]
      }
    ]
  }
}
```

`preferredNotifChannel` makes Claude Code write the format Ghostty uses (OSC 777), which carries the
title and body as separate fields. With the default, `auto`, Claude Code writes nothing under tmux.
Outside tmux, Ghostty displays the same format itself, and other terminals ignore it.

`SessionStart` also fires after `/clear`, `/resume` and compaction. The hook starts one watcher per
session and leaves it running on those. It does nothing outside tmux, nor for non-interactive
sessions: `claude -p`, the SDK, and background sessions, which are not tied to a pane.

### 2. Install a notifier

The notifier app decides what the header shows: on macOS, a notification's icon and name always come
from the posting app.

On macOS, either:

- Build `claude-notifier.app`, shown as "Claude Code" with the Claude icon:

  ```
  make -C tmux/claude-notifier install
  ```

  terminal-notifier is a submodule in `claude-notifier/`, pinned to a release. The first build
  fetches it, so a plain clone of this repository is enough. Building needs only the Command Line
  Tools (`xcode-select --install`). The app is installed in `~/Applications`, where the hook finds
  it. To install it elsewhere, give the directory as `INSTALL_DIR=`, and set `TMUX_CLAUDE_NOTIFIER`
  to the app's executable, `<directory>/claude-notifier.app/Contents/MacOS/terminal-notifier`.

- Or install the stock notifier, shown as "terminal-notifier" with its own icon:

  ```
  brew install terminal-notifier
  ```

The first notification asks for permission. If it was refused, turn it on in System Settings >
Notifications.

On Linux, install `notify-send` from your distribution's libnotify package (`libnotify-bin` on
Debian and Ubuntu). Set `TMUX_CLAUDE_NOTIFY_ICON` to an image file for the icon, such as
`claude-notifier/ClaudeIcon-Rounded.png`.

The Claude icon comes from Anthropic's press kit, linked under "Media assets" on
[anthropic.com/news](https://www.anthropic.com/news). This project is not affiliated with Anthropic.

## Configuration

Environment variables read by `start-notification-watcher.sh`, from Claude Code's environment:

| Variable                      | Sets                                                      |
| ----------------------------- | --------------------------------------------------------- |
| `TMUX_CLAUDE_NOTIFIER`        | The notifier executable, if installed with `INSTALL_DIR=` |
| `TMUX_CLAUDE_NOTIFY_SUBTITLE` | The subtitle, as a tmux format                            |
| `TMUX_CLAUDE_NOTIFY_ICON`     | The icon, for `notify-send` only                          |

The default notifier on macOS is `claude-notifier.app` in `~/Applications`, else
`terminal-notifier`, and `notify-send` elsewhere.

The default subtitle is `#{b:pane_current_path} · #{session_name}:#{window_index}.#{pane_index}`,
expanded for the pane when each notification arrives: the directory's name, then the pane in tmux's
`session:window.pane` target form, such as `main:1.2`.

On macOS, clicking a notification also brings the terminal application to the front. The hook reads
which one from `__CFBundleIdentifier`, which macOS sets for programs an application starts.

tmux allows one pipe per pane. The pane option `@claude_notifications_pid` records which Claude Code
process a pane's pipe belongs to, and the hook leaves a pipe that something else opened alone.

## Tests

From the repository root:

```
uv run --with pytest pytest tmux/tests
```

The tests include a stream modelled on Claude Code's output, split at every byte position, which
checks that a notification divided between two reads is still found exactly once. Waiting for a
process to exit is tested with each mechanism the platform has: a pidfd on Linux, kqueue on macOS,
and polling.
