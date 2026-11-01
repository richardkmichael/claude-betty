#!/usr/bin/env python3
"""Turn Claude Code's OSC 777 notifications into desktop notifications, under tmux.

tmux's `pipe-pane -O` runs this with a copy of one pane's output on stdin, as set up by
start-notification-watcher.sh.  Claude Code, with `preferredNotifChannel` set to `ghostty`, writes
each notification as

    ESC ] 777 ; notify ; <title> ; <body> BEL

wrapped for tmux as `ESC P tmux; <sequence, every ESC doubled> ESC \\`.  The watcher finds each
one, wrapped or bare, and posts it with terminal-notifier (or a build of it) on macOS, or
notify-send elsewhere.  The subtitle comes from the pane (by default its directory, session,
window and pane), and clicking the notification switches tmux to the pane.

A copy of the output arrives whether or not the pane is visible, so notifications from panes in
other windows and sessions arrive too.

With --exit-with, the watcher closes the pane's pipe when that process (Claude Code) exits, for
any reason, including a crash.  tmux then closes the watcher's stdin, and the watcher exits.

Standard library only, and compatible with the system Python 3.9 on macOS.
"""

from __future__ import annotations

import argparse
import os
import select
import shlex
import subprocess
import threading
import time
from dataclasses import dataclass

ESC = b"\x1b"
BEL = b"\x07"
ST = b"\x1b\\"
PASSTHROUGH_START = b"\x1bPtmux;"
OSC_777_START = b"\x1b]777;"
MARKERS = (PASSTHROUGH_START, OSC_777_START)

# Longest sequence held while waiting for its end.  A notification is far shorter.  A longer
# passthrough sequence, such as an image, is discarded up to its end instead of buffered.
SEQUENCE_LIMIT = 64 * 1024

READ_SIZE = 64 * 1024

DEFAULT_SUBTITLE_FORMAT = "#{b:pane_current_path} · #{session_name}:#{window_index}.#{pane_index}"

# terminal-notifier's name for macOS's default notification sound.
DEFAULT_SOUND = "default"

# The pane option recording which Claude Code process the pane's watcher belongs to.
OWNER_OPTION = "@claude_notifications_pid"


@dataclass(frozen=True)
class Notification:
    title: str
    body: str


def parse_osc_777(payload: bytes) -> Notification | None:
    """Given the bytes between `ESC ] 777 ;` and the terminator, return the notification."""
    kind, _, fields = payload.partition(b";")
    if kind != b"notify":
        return None
    title, _, body = fields.partition(b";")
    return Notification(title.decode(errors="replace"), body.decode(errors="replace"))


def osc_777_notifications(data: bytes) -> list[Notification]:
    """Return every complete OSC 777 notification in data, which holds no passthrough wrapper."""
    notifications = []
    position = 0
    while (start := data.find(OSC_777_START, position)) != -1:
        payload_start = start + len(OSC_777_START)
        end, terminator_length = _osc_end(data, payload_start)
        if end == -1:
            break
        notification = parse_osc_777(data[payload_start:end])
        if notification is not None:
            notifications.append(notification)
        position = end + terminator_length
    return notifications


def _osc_end(data: bytes, position: int) -> tuple[int, int]:
    """Return the index and length of the terminator of the OSC whose payload starts at position.

    Returns (-1, 0) when no terminator has arrived yet.
    """
    bel = data.find(BEL, position)
    st = data.find(ST, position)
    if bel == -1 and st == -1:
        return -1, 0
    if st == -1 or (bel != -1 and bel < st):
        return bel, len(BEL)
    return st, len(ST)


def _passthrough_end(data: bytes, position: int) -> tuple[str, int]:
    """Scan a passthrough body from position, which follows an ESC pair or the opening marker.

    Inside the body every ESC is doubled, so an ESC that is not part of a pair starts the closing
    `ESC \\`.  Returns one of:

    - ("end", index of the closing ESC)
    - ("incomplete", index to resume from once more data arrives)
    - ("malformed", index just past the opening marker's ESC)
    """
    while True:
        escape = data.find(ESC, position)
        if escape == -1:
            return "incomplete", len(data)
        following = data[escape + 1 : escape + 2]
        if following == ESC:
            position = escape + 2
        elif following == b"\\":
            return "end", escape
        elif following == b"":
            return "incomplete", escape
        else:
            return "malformed", escape


def _next_marker(data: bytes, position: int) -> int:
    starts = [index for marker in MARKERS if (index := data.find(marker, position)) != -1]
    return min(starts, default=-1)


def _partial_marker_start(data: bytes) -> int:
    """Return where a marker cut off by the end of data begins, or len(data) if there is none."""
    longest = max(len(marker) for marker in MARKERS)
    for start in range(max(0, len(data) - longest + 1), len(data)):
        tail = data[start:]
        if any(marker.startswith(tail) for marker in MARKERS):
            return start
    return len(data)


class Scanner:
    """Find notifications in a byte stream that arrives in chunks split at arbitrary points."""

    def __init__(self, limit: int = SEQUENCE_LIMIT) -> None:
        self._limit = limit
        self._pending = b""
        # Set while discarding the rest of an over-long passthrough sequence.
        self._discarding = False

    def feed(self, chunk: bytes) -> list[Notification]:
        data = self._pending + chunk
        notifications = []
        position = 0
        while True:
            if self._discarding:
                state, index = _passthrough_end(data, position)
                if state == "incomplete":
                    self._pending = data[index:]
                    return notifications
                self._discarding = False
                position = index + len(ST) if state == "end" else index
                continue

            start = _next_marker(data, position)
            if start == -1:
                self._pending = data[_partial_marker_start(data[position:]) + position :]
                return notifications

            if data.startswith(OSC_777_START, start):
                payload_start = start + len(OSC_777_START)
                end, terminator_length = _osc_end(data, payload_start)
                if end == -1:
                    if len(data) - start > self._limit:
                        position = payload_start
                        continue
                    self._pending = data[start:]
                    return notifications
                notification = parse_osc_777(data[payload_start:end])
                if notification is not None:
                    notifications.append(notification)
                position = end + terminator_length
                continue

            body_start = start + len(PASSTHROUGH_START)
            state, index = _passthrough_end(data, body_start)
            if state == "incomplete":
                if len(data) - start > self._limit:
                    self._discarding = True
                    position = index
                    continue
                self._pending = data[start:]
                return notifications
            if state == "malformed":
                position = start + 1
                continue
            unwrapped = data[body_start:index].replace(ESC + ESC, ESC)
            notifications.extend(osc_777_notifications(unwrapped))
            position = index + len(ST)


def wait_for_exit(pid: int) -> None:
    """Return once the process pid has exited, or at once if there is no such process.

    The process need not be a child of this one.  Waiting uses whichever mechanism the platform
    has: a pidfd on Linux, kqueue on macOS and the BSDs, and otherwise polling.
    """
    if hasattr(os, "pidfd_open"):
        try:
            _wait_pidfd(pid)
        except ProcessLookupError:
            return
        except OSError:
            pass  # A kernel older than Linux 5.3 has no pidfd_open.
        else:
            return
    if hasattr(select, "kqueue"):
        try:
            _wait_kqueue(pid)
        except ProcessLookupError:
            pass
        return
    _wait_polling(pid)


def _wait_pidfd(pid: int) -> None:
    pidfd = os.pidfd_open(pid)
    try:
        select.select([pidfd], [], [])  # A pidfd becomes readable when its process exits.
    finally:
        os.close(pidfd)


def _wait_kqueue(pid: int) -> None:
    exit_event = select.kevent(
        pid,
        filter=select.KQ_FILTER_PROC,
        flags=select.KQ_EV_ADD | select.KQ_EV_ONESHOT,
        fflags=select.KQ_NOTE_EXIT,
    )
    queue = select.kqueue()
    try:
        queue.control([exit_event], 1, None)
    finally:
        queue.close()


def _wait_polling(pid: int, interval: float = 1.0) -> None:
    while True:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        except PermissionError:
            pass  # The process exists, but belongs to another user.
        time.sleep(interval)


@dataclass(frozen=True)
class Pane:
    """The tmux pane the watcher serves, and the programs used to reach it."""

    pane_id: str
    socket: str
    tmux: str

    def tmux_command(self, *arguments: str) -> list[str]:
        return [self.tmux, "-S", self.socket, *arguments]

    def describe(self, subtitle_format: str) -> str:
        result = subprocess.run(
            self.tmux_command("display-message", "-p", "-t", self.pane_id, subtitle_format),
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
        return result.stdout.strip() if result.returncode == 0 else ""

    def jump_command(self) -> str:
        """Return a shell command that switches the most recent tmux client to the pane."""
        tmux = shlex.join(self.tmux_command())
        target = shlex.quote(self.pane_id)
        return (
            f"{tmux} switch-client -t {target}"
            f" \\; select-window -t {target}"
            f" \\; select-pane -t {target}"
        )

    def close_pipe_if_owned_by(self, pid: int) -> None:
        """Close the pane's pipe and clear its owner option, if the option still records pid.

        tmux checks and closes in one command, so a pipe a newer session has opened since is left
        alone.
        """
        owned_by_pid = f"#{{==:#{{{OWNER_OPTION}}},{pid}}}"
        close = f"pipe-pane -t {self.pane_id} ; set-option -p -u -t {self.pane_id} {OWNER_OPTION}"
        subprocess.run(
            self.tmux_command("if-shell", "-F", "-t", self.pane_id, owned_by_pid, close),
            capture_output=True,
            timeout=5,
            check=False,
        )


class TerminalNotifier:
    """Posts with terminal-notifier on macOS, or a build of it such as claude-notifier.app."""

    def __init__(self, executable: str, activate: str | None, sound: str) -> None:
        self.executable = executable
        self.activate = activate
        self.sound = sound

    def post(self, notification: Notification, subtitle: str, group: str, on_click: str) -> None:
        command = [self.executable, "-title", notification.title, "-message", notification.body]
        command += ["-subtitle", subtitle, "-group", group, "-execute", on_click]
        if self.activate:
            command += ["-activate", self.activate]
        if self.sound:
            command += ["-sound", self.sound]
        _start_detached(command)


class NotifySend:
    """Posts with notify-send from libnotify, on Linux and the BSDs.

    notify-send has no subtitle, so the subtitle becomes the first line of the body.  A click runs
    on_click only if notify-send supports `--action` and `--wait` (libnotify 0.7.10 and later),
    and the watcher is still running.
    """

    def __init__(self, executable: str, icon: str | None) -> None:
        self.executable = executable
        self.icon = icon

    def post(self, notification: Notification, subtitle: str, group: str, on_click: str) -> None:
        body = f"{subtitle}\n{notification.body}" if subtitle else notification.body
        command = [self.executable, f"--app-name={notification.title}", "--action=default=Open"]
        command += ["--wait"]
        if self.icon:
            command += [f"--icon={self.icon}"]
        command += [notification.title, body]
        threading.Thread(target=self._wait_for_click, args=(command, on_click), daemon=True).start()

    @staticmethod
    def _wait_for_click(command: list[str], on_click: str) -> None:
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        if result.stdout.strip() == "default":
            _start_detached(["/bin/sh", "-c", on_click])


def notifier_for(executable: str, activate: str | None, icon: str | None, sound: str):
    if os.path.basename(executable) == "notify-send":
        return NotifySend(executable, icon)
    return TerminalNotifier(executable, activate, sound)


def _start_detached(command: list[str]) -> None:
    subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def close_pipe_when_exited(pane: Pane, pid: int) -> None:
    """Close the pane's pipe once pid exits, which ends this watcher when tmux closes its stdin."""
    wait_for_exit(pid)
    pane.close_pipe_if_owned_by(pid)


def parse_arguments(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--pane", required=True, help="the pane ID, e.g. %%5")
    parser.add_argument("--socket", required=True, help="the tmux server's socket path")
    parser.add_argument("--tmux", required=True, help="the tmux executable")
    parser.add_argument(
        "--notifier", required=True, help="terminal-notifier, a build of it, or notify-send"
    )
    parser.add_argument(
        "--exit-with",
        type=int,
        metavar="PID",
        help=f"close the pane's pipe when PID exits, if the pane's {OWNER_OPTION} records it",
    )
    parser.add_argument("--activate", help="macOS: bundle ID of the app to bring to the front")
    parser.add_argument("--icon", help="notify-send: icon file or name")
    parser.add_argument(
        "--sound",
        default=DEFAULT_SOUND,
        help=f"macOS: the sound to play, or '' for none (default: {DEFAULT_SOUND!r})",
    )
    parser.add_argument(
        "--subtitle-format",
        default=DEFAULT_SUBTITLE_FORMAT,
        help=f"tmux format for the subtitle (default: {DEFAULT_SUBTITLE_FORMAT!r})",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    arguments = parse_arguments(argv)
    pane = Pane(arguments.pane, arguments.socket, arguments.tmux)
    notifier = notifier_for(
        arguments.notifier, arguments.activate, arguments.icon, arguments.sound
    )
    if arguments.exit_with is not None:
        threading.Thread(
            target=close_pipe_when_exited, args=(pane, arguments.exit_with), daemon=True
        ).start()
    scanner = Scanner()
    while chunk := os.read(0, READ_SIZE):
        for notification in scanner.feed(chunk):
            notifier.post(
                notification,
                subtitle=pane.describe(arguments.subtitle_format),
                # A newer notification from the same pane replaces the older one.
                group=f"claude-code-{pane.pane_id}",
                on_click=pane.jump_command(),
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
