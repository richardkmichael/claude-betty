#!/usr/bin/env python

"""PostToolUse and UserPromptSubmit hook: tell the model when a quota window passes 90%, 95% or 99%.

The model learns about quota only by calling get-quota, and it calls it before starting work, not
while the work runs. This hook watches the same recording on every tool call and every prompt, and
when a window passes a threshold it adds a note to the model's context: one line naming the window
and the threshold, followed by get-quota's answer, whose guidance fields say what to do.

Each conversation is told once per threshold per window: the main conversation and every subagent
separately, keyed by agent_id, since each acts on its own work. A subagent is reached only this
way, because while a foreground subagent runs, the main conversation makes no tool calls. Marker
files created with O_EXCL record what has been told, so hooks running in parallel for one tool
batch tell it once between them. A window that resets has a new reset time, and so new markers.

The check runs on every tool call, so it reads two small files and nothing else. Only a crossing
runs server.py, for the answer. Standard library only, and it never fails the call: on any error it
prints nothing.
"""

import contextlib
import datetime
import json
import os
import re
import shutil
import subprocess
import sys
import time

THRESHOLDS = (90, 95, 99)
FRESH = 60  # the server's limit: a recording older than this is not reported
USAGE_STALE_AFTER = 300  # the server's limit for the per-model windows
KEEP_NOTICES = 7 * 86400  # a session's markers untouched for this long are deleted
ANSWER_TIMEOUT = 20
QUOTA_TOOL = "mcp__quota__get-quota"  # its answer already carries what a notice would
SERVER = os.path.join(os.path.dirname(os.path.realpath(__file__)), "server.py")
NAME = re.compile(r"^[A-Za-z0-9_-]+$")  # a session or agent ID used as a file name


def state_dir(*parts):
    """A path under statusline.py's state directory, resolved the same way it resolves it."""
    root = os.environ.get("CLAUDE_QUOTA_STATE") or os.path.join(
        os.environ.get("XDG_STATE_HOME")
        or os.path.join(os.path.expanduser("~"), ".local", "state"),
        "claude-quota",
    )
    return os.path.join(root, *parts)


def read_json(path):
    try:
        with open(path) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def iso_epoch(s):
    try:
        return datetime.datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except (AttributeError, ValueError):
        return 0


def windows(session):
    """(key, label, percent used, reset epoch) for every window the server would report."""
    for key, label in (("five_hour", "five-hour window"), ("seven_day", "seven-day window")):
        reading = (session.get("rate_limits") or {}).get(key) or {}
        if isinstance(reading.get("used_percentage"), (int, float)):
            yield key, label, reading["used_percentage"], reading.get("resets_at") or 0
    usage = read_json(state_dir("accounts", session.get("account_uuid") or "", "usage.json"))
    if time.time() - (usage.get("fetched_at") or 0) > USAGE_STALE_AFTER:
        return
    for limit in (usage.get("usage") or {}).get("limits") or []:
        name = (((limit.get("scope") or {}).get("model") or {}).get("display_name") or "").strip()
        if name and NAME.match(name) and isinstance(limit.get("percent"), (int, float)):
            yield name, f"{name} weekly window", limit["percent"], iso_epoch(limit.get("resets_at"))


def trusted(session):
    """Whether the server would answer from this recording; see statusline/RECORDING.md."""
    return (
        session.get("schema") == 1
        and time.time() - (session.get("rendered_at") or 0) <= FRESH
        and bool(session.get("rate_limits"))
        and session.get("response_at") is not None
        and bool(session.get("matches_login"))
    )


def newly_passed(directory, key, used, resets_at):
    """The highest threshold used has reached that this conversation was not yet told of, or None.

    Every threshold reached is marked, so a window first seen at 96% is reported once, at 95, and
    not again at 90.
    """
    told = None
    for threshold in THRESHOLDS:
        if used < threshold:
            break
        # Minutes, since the usage endpoint reports a reset time that varies by microseconds.
        marker = os.path.join(directory, f"{key}-{round(resets_at / 60)}-{threshold}")
        try:
            os.close(os.open(marker, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600))
        except FileExistsError:
            continue
        told = threshold
    return told


def prune():
    """Delete the markers of sessions untouched for KEEP_NOTICES."""
    root = state_dir("notices")
    for name in os.listdir(root):
        path = os.path.join(root, name)
        with contextlib.suppress(OSError):
            if time.time() - os.stat(path).st_mtime > KEEP_NOTICES:
                shutil.rmtree(path)


def answer(session_id, agent_id):
    """get-quota's answer for the conversation, as JSON text, or None when it would refuse."""
    command = [SERVER, "--answer", session_id, *([agent_id] if agent_id else [])]
    result = subprocess.run(
        command, capture_output=True, text=True, timeout=ANSWER_TIMEOUT, check=False
    )
    return result.stdout.strip() if result.returncode == 0 else None


def main():
    event = json.load(sys.stdin)
    session_id, agent_id = event.get("session_id") or "", event.get("agent_id") or ""
    if not NAME.match(session_id) or (agent_id and not NAME.match(agent_id)):
        return
    session = read_json(state_dir("sessions", f"{session_id}.json"))
    if not trusted(session):
        return
    directory = state_dir("notices", session_id, agent_id or "main")
    os.makedirs(directory, mode=0o700, exist_ok=True)
    passed = []
    for key, label, used, resets_at in windows(session):
        threshold = newly_passed(directory, key, used, resets_at)
        if threshold:
            passed.append(f"The {label} passed {threshold}%.")
    # A get-quota call is marked as told: the model has just read the answer a notice would carry.
    if not passed or event.get("tool_name") == QUOTA_TOOL:
        return
    prune()
    text = "[quota] " + " ".join(passed)
    found = answer(session_id, agent_id)
    if found:
        text += " get-quota's answer:\n" + found
    output = {
        "hookSpecificOutput": {
            "hookEventName": event.get("hook_event_name") or "PostToolUse",
            "additionalContext": text,
        }
    }
    print(json.dumps(output))


if __name__ == "__main__":
    with contextlib.suppress(Exception):  # a broken hook must never block the call
        main()
    sys.exit(0)
