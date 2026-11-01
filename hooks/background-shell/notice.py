#!/usr/bin/env python

"""PostToolUse hook: tell the model what a background Bash command runs under.

A Bash command in the background returns at once with a task id.  It gets there in several ways:
the model asks for it with `run_in_background`, the user presses Ctrl+B, the command reaches its
timeout, or a queued message is sent at once.  Each sets `backgroundTaskId` in the tool response,
while the tool input still holds what the model sent, so the response is what this hook tests.

The harness runs the command as `bash -c` and tracks that shell: the task ends when the shell
exits, whatever the processes it started are still doing.  The shell has job control on, because
Claude Code's shell snapshot always writes `set -o monitor`.  So each job is in a process group of
its own, and when a job stops, the shell's wait returns: it runs its next command, exits, and the
task ends.  The stopped group, its parent gone, is orphaned, and the kernel sends it SIGHUP and
SIGCONT, which ends it.  Pausing background work with SIGSTOP therefore kills it.  This hook adds a
note saying so to the model's context after every Bash call that ends up in the background.

Standard library only, and it never fails the call: on any error it prints nothing.
"""

import json
import sys

NOTE = (
    "This Bash command runs in the background. The harness runs it as `bash -c` and tracks that "
    "shell, not the work inside it: the task ends when the shell exits. The shell has job control "
    "on (`set -o monitor`), so each job it starts is in a process group of its own, and a signal "
    "sent to a process group reaches every process in it. When a job stops (SIGSTOP, SIGTSTP), the "
    "shell's wait returns: it runs its next command and exits, ending the task, and the stopped "
    "group, left without its parent, is orphaned and gets SIGHUP and then SIGCONT, which ends it. "
    "So pausing this work kills it. Before signalling, pausing or killing anything this command "
    "started, list the process tree under its shell and work out what each process and its parent "
    "will do."
)


def main() -> None:
    try:
        event = json.load(sys.stdin)
        if event.get("tool_name") != "Bash":
            return
        response = event.get("tool_response")
        if not isinstance(response, dict) or not response.get("backgroundTaskId"):
            return
        print(
            json.dumps(
                {
                    "hookSpecificOutput": {
                        "hookEventName": "PostToolUse",
                        "additionalContext": NOTE,
                    }
                }
            )
        )
    except Exception:
        return


if __name__ == "__main__":
    main()
