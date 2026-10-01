"""Tests for notice.py, the hook that tells the model when a quota window passes a threshold.

Run from the repository root:

    uv run --with pytest pytest mcp/quota/tests/test_notice.py

Most tests call the hook in-process with server.py's answer stubbed out. The last one runs the hook
as Claude Code does, a subprocess fed the hook input on stdin, and lets it run the real server; it
needs uv, as the server does.
"""

import importlib.util
import io
import json
import os
import subprocess
import sys
import time

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
NOTICE = os.path.join(HERE, "..", "notice.py")
SESSION = "aaaaaaaa-0000-4000-8000-000000000001"
ACCOUNT = "acc-a"
FIVE_HOUR_RESET = int(time.time() + 3600)


def load():
    spec = importlib.util.spec_from_file_location("quota_notice", NOTICE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(obj, f)


@pytest.fixture
def state(tmp_path, monkeypatch):
    root = tmp_path / "state"
    monkeypatch.setenv("CLAUDE_QUOTA_STATE", str(root))

    def record(five_hour, fable=None, resets_at=FIVE_HOUR_RESET, **changes):
        session = {
            "schema": 1,
            "session_id": SESSION,
            "account_uuid": ACCOUNT,
            "rendered_at": time.time(),
            "response_at": time.time(),
            "matches_login": True,
            "rate_limits": {
                "five_hour": {"used_percentage": five_hour, "resets_at": resets_at},
                "seven_day": {"used_percentage": 10, "resets_at": FIVE_HOUR_RESET + 86400},
            },
            **changes,
        }
        write(str(root / "sessions" / f"{SESSION}.json"), session)
        limits = []
        if fable is not None:
            limits.append(
                {
                    "percent": fable,
                    "resets_at": "2026-10-07T13:00:00.123456+00:00",
                    "scope": {"model": {"display_name": "Fable"}},
                }
            )
        write(
            str(root / "accounts" / ACCOUNT / "usage.json"),
            {"fetched_at": time.time(), "usage": {"limits": limits}},
        )

    return record


@pytest.fixture
def notice(state, monkeypatch):
    module = load()
    module.answered = []

    def answer(session_id, agent_id):
        module.answered.append(agent_id)
        return '{"plan": {}}'

    monkeypatch.setattr(module, "answer", answer)
    return module


def run(module, monkeypatch, agent_id=None, tool="Bash", event="PostToolUse"):
    """The note the hook adds to the model's context, or None."""
    hook_input = {"session_id": SESSION, "hook_event_name": event, "tool_name": tool}
    if agent_id:
        hook_input["agent_id"] = agent_id
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(hook_input)))
    out = io.StringIO()
    monkeypatch.setattr(sys, "stdout", out)
    module.main()
    if not out.getvalue():
        return None
    output = json.loads(out.getvalue())["hookSpecificOutput"]
    assert output["hookEventName"] == event
    return output["additionalContext"]


def test_each_threshold_is_told_once(notice, state, monkeypatch):
    state(89)
    assert run(notice, monkeypatch) is None
    state(90)
    note = run(notice, monkeypatch)
    assert note.startswith("[quota] The five-hour window passed 90%. get-quota's answer:\n{")
    assert run(notice, monkeypatch) is None
    state(95)
    assert run(notice, monkeypatch).startswith("[quota] The five-hour window passed 95%.")


def test_a_window_first_seen_high_is_told_only_its_highest_threshold(notice, state, monkeypatch):
    state(96)
    assert run(notice, monkeypatch).startswith("[quota] The five-hour window passed 95%.")
    assert run(notice, monkeypatch) is None


def test_the_main_conversation_and_each_subagent_are_told_separately(notice, state, monkeypatch):
    state(91)
    assert run(notice, monkeypatch)
    assert run(notice, monkeypatch, agent_id="a1")
    assert run(notice, monkeypatch, agent_id="a2")
    assert run(notice, monkeypatch, agent_id="a1") is None
    assert notice.answered == ["", "a1", "a2"]


def test_a_new_window_is_told_again(notice, state, monkeypatch):
    state(92)
    assert run(notice, monkeypatch)
    state(92, resets_at=FIVE_HOUR_RESET + 5 * 3600)
    assert run(notice, monkeypatch)


def test_per_model_windows_are_watched(notice, state, monkeypatch):
    state(50, fable=99)
    assert run(notice, monkeypatch).startswith("[quota] The Fable weekly window passed 99%.")


def test_a_prompt_is_told_as_a_tool_call_is(notice, state, monkeypatch):
    state(90)
    note = run(notice, monkeypatch, event="UserPromptSubmit", tool=None)
    assert note.startswith("[quota] The five-hour window passed 90%.")


def test_a_get_quota_call_counts_as_told(notice, state, monkeypatch):
    state(90)
    assert run(notice, monkeypatch, tool=notice.QUOTA_TOOL) is None
    assert run(notice, monkeypatch) is None
    assert notice.answered == []


@pytest.mark.parametrize(
    "changes",
    [{"rendered_at": 0}, {"matches_login": False}, {"rate_limits": None}, {"schema": 2}],
)
def test_nothing_is_told_from_a_recording_the_server_would_refuse(
    notice, state, monkeypatch, changes
):
    state(99, **changes)
    assert run(notice, monkeypatch) is None


def test_the_hook_runs_the_server_for_its_answer(state, tmp_path):
    state(93)
    result = subprocess.run(
        [sys.executable, NOTICE],
        input=json.dumps({"session_id": SESSION, "hook_event_name": "PostToolUse"}),
        capture_output=True,
        text=True,
        env=dict(os.environ, CLAUDE_QUOTA_STATE=str(tmp_path / "state")),
        check=True,
    )
    note = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
    head, answer = note.split("\n", 1)
    assert head == "[quota] The five-hour window passed 90%. get-quota's answer:"
    assert json.loads(answer)["plan"]["five_hour"]["used_percent"] == 93
