#!/usr/bin/env python

"""PreToolUse hook for get-quota: pass the calling session's ID to the tool as a hidden argument.

The quota server needs to know which session is asking, and nothing it can read says so reliably:
its own CLAUDE_CODE_SESSION_ID is fixed when Claude Code starts it and goes stale on `/clear` or an
in-session `/resume`. A PreToolUse hook's input always carries the current session_id, and its
updatedInput is merged into the call's arguments before the call is sent, so the server receives
the ID the moment it is asked. A call from a subagent carries the parent session's ID, which is
the session whose status line is recorded.

Standard library only, and it never fails the call: on any error it prints nothing, the call goes
ahead without a session_id, and the server reports the hook as missing.
"""

import contextlib
import json
import sys


def main():
    event = json.load(sys.stdin)
    session_id = event.get("session_id")
    if not session_id:
        return
    output = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "updatedInput": {"session_id": session_id},
        }
    }
    print(json.dumps(output))


if __name__ == "__main__":
    with contextlib.suppress(Exception):  # a broken hook must never block the call
        main()
    sys.exit(0)
