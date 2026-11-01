import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parent.parent / "notification-watcher.py"
spec = importlib.util.spec_from_file_location("notification_watcher", SCRIPT)
watcher = importlib.util.module_from_spec(spec)
sys.modules["notification_watcher"] = watcher
spec.loader.exec_module(watcher)

Notification = watcher.Notification


def osc_777(title: str, body: str, terminator: bytes = b"\x07") -> bytes:
    return b"\x1b]777;notify;" + f"{title};{body}".encode() + terminator


def tmux_wrapped(sequence: bytes) -> bytes:
    return b"\x1bPtmux;" + sequence.replace(b"\x1b", b"\x1b\x1b") + b"\x1b\\"


def feed_all(chunks: list[bytes], limit: int = watcher.SEQUENCE_LIMIT) -> list:
    scanner = watcher.Scanner(limit)
    notifications = []
    for chunk in chunks:
        notifications += scanner.feed(chunk)
    return notifications


PERMISSION = Notification("Claude Code", "Claude needs your permission")

# What a pane running Claude Code under tmux writes around a notification: drawing, a progress
# update and a title change, each also wrapped or bare, and the notification itself.
STREAM = (
    b"\x1b[?2026h\x1b[2K\x1b[1G\x1b[38;5;174m\xe2\x9c\xbb\x1b[39m Thinking\xe2\x80\xa6\r\n"
    + tmux_wrapped(b"\x1b]9;4;3;\x07")
    + b"\x1b]0;\xe2\x9c\xb3 Claude Code\x07"
    + tmux_wrapped(osc_777("Claude Code", "Claude needs your permission"))
    + b"\x1b[?2026l\x1b[2K Do you want to proceed?\r\n\x1b"
    + b"[1m 1. Yes\x1b[22m\r\n"
)


def test_tmux_wrapped_notification():
    assert feed_all([tmux_wrapped(osc_777("Claude Code", "Claude needs your permission"))]) == [
        PERMISSION
    ]


def test_bare_notification():
    assert feed_all([osc_777("Claude Code", "Claude needs your permission")]) == [PERMISSION]


@pytest.mark.parametrize("terminator", [b"\x07", b"\x1b\\"])
def test_both_terminators(terminator):
    sequence = osc_777("Claude Code", "Claude needs your permission", terminator)
    assert feed_all([tmux_wrapped(sequence)]) == [PERMISSION]
    assert feed_all([sequence]) == [PERMISSION]


def test_body_keeps_semicolons():
    sequence = tmux_wrapped(osc_777("Claude Code", "first; second; third"))
    assert feed_all([sequence]) == [Notification("Claude Code", "first; second; third")]


def test_utf8_text():
    sequence = tmux_wrapped(osc_777("Claude Code", "Usage limit reset — Claude is continuing"))
    assert feed_all([sequence]) == [
        Notification("Claude Code", "Usage limit reset — Claude is continuing")
    ]


def test_other_sequences_are_ignored():
    stream = (
        tmux_wrapped(b"\x1b]9;4;3;\x07")
        + tmux_wrapped(b"\x1b]777;progress;50\x07")
        + b"\x1b]777;other;field\x07"
        + b"\x1b]0;title\x07"
    )
    assert feed_all([stream]) == []


def test_stream_split_at_every_byte():
    for split in range(len(STREAM) + 1):
        assert feed_all([STREAM[:split], STREAM[split:]]) == [PERMISSION], split


def test_stream_one_byte_at_a_time():
    assert feed_all([bytes([byte]) for byte in STREAM]) == [PERMISSION]


def test_several_notifications_in_one_chunk():
    stream = STREAM + tmux_wrapped(osc_777("Claude Code", "Claude is waiting for your input"))
    assert feed_all([stream]) == [
        PERMISSION,
        Notification("Claude Code", "Claude is waiting for your input"),
    ]


def test_long_passthrough_is_discarded_and_scanning_resumes():
    image = tmux_wrapped(b"\x1b_Gf=100;" + b"A" * 5000 + b"\x1b\\")
    stream = image + STREAM
    for split in range(0, len(stream) + 1, 97):
        assert feed_all([stream[:split], stream[split:]], limit=1024) == [PERMISSION], split


def test_pending_data_stays_bounded():
    scanner = watcher.Scanner(limit=1024)
    scanner.feed(b"\x1bPtmux;")
    for _ in range(100):
        scanner.feed(b"A" * 1000)
    assert len(scanner._pending) <= 1024 + 1000
    assert scanner.feed(b"\x1b\\" + STREAM) == [PERMISSION]


def test_malformed_passthrough_does_not_hide_a_later_notification():
    stream = b"\x1bPtmux;\x1bX junk" + STREAM
    assert feed_all([stream]) == [PERMISSION]


def test_plain_output_keeps_no_pending_data():
    scanner = watcher.Scanner()
    scanner.feed(b"plain text\r\n" * 1000)
    assert scanner._pending == b""


def test_jump_command_quotes_for_the_shell():
    pane = watcher.Pane("%5", "/tmp/tmux socket/default", "/opt/homebrew/bin/tmux")
    assert pane.jump_command() == (
        "/opt/homebrew/bin/tmux -S '/tmp/tmux socket/default' switch-client -t %5"
        " \\; select-window -t %5 \\; select-pane -t %5"
    )


@pytest.mark.parametrize(
    ("sound", "expected"),
    [("default", ["-sound", "default"]), ("Glass", ["-sound", "Glass"]), ("", [])],
)
def test_terminal_notifier_plays_the_sound(monkeypatch, sound, expected):
    commands = []
    monkeypatch.setattr(watcher, "_start_detached", commands.append)
    notifier = watcher.notifier_for("/path/to/terminal-notifier", None, None, sound)
    notifier.post(PERMISSION, "project · main:1.2", "claude-code-%5", "true")
    command = commands[0]
    assert command[command.index("-execute") + 2 :] == expected


def test_sound_is_on_by_default():
    required = ["--pane", "%5", "--socket", "s", "--tmux", "t", "--notifier", "n"]
    arguments = watcher.parse_arguments(required)
    assert arguments.sound == "default"


def test_watcher_runs_on_the_python_it_is_given(tmp_path):
    """The watcher exits cleanly at end of input, posting through the given notifier."""
    log = tmp_path / "notifier.log"
    notifier = tmp_path / "notifier"
    notifier.write_text(f'#!/bin/sh\nprintf "%s\\n" "$@" >> "{log}"\n')
    notifier.chmod(0o755)
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--pane", "%0", "--socket", "/nonexistent",
         "--tmux", "/usr/bin/false", "--notifier", str(notifier)],
        input=STREAM,
        capture_output=True,
        timeout=10,
        check=False,
    )  # fmt: skip
    assert result.returncode == 0, result.stderr
    for _ in range(50):
        if log.exists() and "Claude needs your permission" in log.read_text():
            break
        subprocess.run(["sleep", "0.1"], check=True)
    arguments = log.read_text().splitlines()
    assert arguments[arguments.index("-title") + 1] == "Claude Code"
    assert arguments[arguments.index("-message") + 1] == "Claude needs your permission"
    assert arguments[arguments.index("-group") + 1] == "claude-code-%0"


def run_in_thread(target, *arguments):
    import threading

    thread = threading.Thread(target=target, args=arguments, daemon=True)
    thread.start()
    return thread


@pytest.mark.parametrize(
    "wait",
    [
        watcher.wait_for_exit,
        pytest.param(
            watcher._wait_kqueue,
            marks=pytest.mark.skipif(not hasattr(watcher.select, "kqueue"), reason="no kqueue"),
        ),
        pytest.param(
            watcher._wait_pidfd,
            marks=pytest.mark.skipif(not hasattr(watcher.os, "pidfd_open"), reason="no pidfd"),
        ),
        lambda pid: watcher._wait_polling(pid, interval=0.05),
    ],
    ids=["wait_for_exit", "kqueue", "pidfd", "polling"],
)
def test_wait_returns_when_the_process_exits(wait):
    process = subprocess.Popen(["sleep", "0.5"])
    # The process is a child here, so reap it as its exit happens.  Claude Code is not the
    # watcher's child, and its own parent reaps it.
    reaper = run_in_thread(process.wait)
    waiter = run_in_thread(wait, process.pid)
    waiter.join(0.2)
    assert waiter.is_alive(), "returned while the process was still running"
    waiter.join(5)
    assert not waiter.is_alive(), "did not return after the process exited"
    # A pidfd and kqueue report the exit before the reaper has collected it, so wait for both.
    reaper.join(5)
    assert process.returncode is not None


def test_wait_returns_at_once_for_a_process_that_no_longer_exists():
    process = subprocess.Popen(["true"])
    process.wait()
    waiter = run_in_thread(watcher.wait_for_exit, process.pid)
    waiter.join(2)
    assert not waiter.is_alive()
