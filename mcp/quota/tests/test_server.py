"""Tests for the quota MCP server, and its contract with statusline.py.

Run from the repository root:

    uv run --with pytest --with "mcp>=2.2,<3" --with jsonschema pytest mcp/quota/tests

The server is called in-process through the MCP SDK's client, against a state directory of the
test's own. The contract test runs the real statusline.py to write that directory, checks what it
wrote against the published JSON Schemas, and then reads it back through the server.
"""

import asyncio
import importlib.util
import json
import os
import re
import subprocess
import sys
import time

import jsonschema
import pytest

from mcp import Client

HERE = os.path.dirname(os.path.abspath(__file__))
SERVER = os.path.join(HERE, "..", "server.py")
SCHEMAS = os.path.join(HERE, "..", "schemas")
STATUSLINE = os.path.join(HERE, "..", "..", "..", "statusline", "statusline.py")
SESSION = "aaaaaaaa-0000-4000-8000-000000000001"
ACCOUNT = "acc-a"


def load():
    spec = importlib.util.spec_from_file_location("quota_server", SERVER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def call(server, arguments):
    async def run():
        async with Client(server.app) as client:
            return await client.call_tool("get-quota", arguments)

    return asyncio.run(run())


def write(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(obj, f)


class State:
    """A fabricated recording: one session, its account's usage and history."""

    def __init__(self, root):
        self.root = root
        self.now = time.time()
        self.five_hour_reset = int(self.now + 3 * 3600)  # the window began two hours ago
        self.seven_day_reset = int(self.now + 3 * 86400)  # the window began four days ago

    def path(self, *parts):
        return os.path.join(self.root, *parts)

    def session(self, **changes):
        record = {
            "schema": 1,
            "session_id": SESSION,
            "account_uuid": ACCOUNT,
            "account_label": "Personal",
            "rendered_at": time.time(),
            "response_at": self.now - 5,
            "matches_login": True,
            "rate_limits": {
                "five_hour": {"used_percentage": 30, "resets_at": self.five_hour_reset},
                "seven_day": {"used_percentage": 12, "resets_at": self.seven_day_reset},
            },
        }
        record.update(changes)
        write(self.path("sessions", f"{SESSION}.json"), record)

    def fable(self, percent):
        resets = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(self.seven_day_reset))
        return {
            "kind": "weekly_scoped",
            "percent": percent,
            "resets_at": resets,
            "scope": {"model": {"display_name": "Fable"}},
        }

    def usage(self, percent=45, age=30):
        write(
            self.path("accounts", ACCOUNT, "usage.json"),
            {"fetched_at": time.time() - age, "usage": {"limits": [self.fable(percent)]}},
        )

    def history(self):
        def payload(minutes_ago, five, seven):
            return {
                "t": self.now - minutes_ago * 60,
                "source": "payload",
                "session_id": SESSION,
                "five_hour": {"used_percentage": five, "resets_at": self.five_hour_reset},
                "seven_day": {"used_percentage": seven, "resets_at": self.seven_day_reset},
            }

        rows = [
            payload(70, 18, 10),
            payload(20, 25, 11),
            payload(10, 28, 12),
            payload(1, 30, 12),
            {"t": self.now - 50 * 60, "source": "endpoint", "limits": [self.fable(40)]},
            {"t": self.now - 5 * 60, "source": "endpoint", "limits": [self.fable(44)]},
        ]
        day = time.strftime("%Y-%m-%d", time.gmtime(self.now))
        path = self.path("accounts", ACCOUNT, "history", f"{day}.jsonl")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.writelines(json.dumps(row) + "\n" for row in rows)


@pytest.fixture
def state(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_QUOTA_STATE", str(tmp_path / "state"))
    s = State(str(tmp_path / "state"))
    s.session()
    s.usage()
    s.history()
    return s


@pytest.fixture
def server(state):
    module = load()
    # No status line renders during a test, so the wait for one would only add a second a call.
    module.RENDER_WAIT = 0
    return module


def answer(server):
    result = call(server, {"session_id": SESSION})
    assert not result.is_error, result.content[0].text
    return result.structured_content


# ── refusals ──────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("arguments", "changes", "expected"),
    [
        ({}, None, "without a session_id"),
        ({"session_id": SESSION, "foo": 1}, None, "takes no arguments; received foo"),
        ({"session_id": "bbbbbbbb-0000-4000-8000-000000000002"}, None, "No quota recording"),
        ({"session_id": SESSION}, {"schema": 2}, "has schema 2"),
        ({"session_id": SESSION}, {"rendered_at": 0}, "cannot be vouched for"),
        ({"session_id": SESSION}, {"rate_limits": None}, "no quota data yet"),
        ({"session_id": SESSION}, {"response_at": None}, "no quota data yet"),
        ({"session_id": SESSION}, {"matches_login": False}, "not the logged-in account's"),
        ({"session_id": SESSION, "agent_id": "../x"}, None, "not a session ID and an agent ID"),
        (
            {"session_id": SESSION},
            {"rate_limits": {"five_hour": {"used_percentage": "lots", "resets_at": 1}}},
            "does not match mcp/quota/schemas/session.schema.json",
        ),
    ],
)
def test_refuses_what_it_cannot_vouch_for(server, state, arguments, changes, expected):
    if changes:
        state.session(**changes)
    result = call(server, arguments)
    assert result.is_error
    assert expected in result.content[0].text


# ── the answer ────────────────────────────────────────────────────────────────


def test_rates_are_measured_over_each_span_ending_now(server):
    plan = answer(server)["plan"]
    assert plan["five_hour"]["percent_per_minute"] == {
        "last_2_min": 1.0,  # 28% before the span began, 30% now
        "last_10_min": 0.2,  # 28% ten minutes ago
        "last_20_min": 0.25,  # 25% twenty minutes ago
    }
    assert "measured_minutes" not in plan["five_hour"]


def test_the_forecast_lists_every_window_at_the_2_minute_rate(server):
    forecast = answer(server)["exhaustion_at_last_2_min_rate"]
    assert forecast["five_hour"]["in_minutes"] == 70  # 70 points left at 1.0 a minute
    assert forecast["seven_day"]["in_minutes"] is None  # no movement in the last 2 minutes
    assert forecast["Fable"]["in_minutes"] == 110  # 55 points left at 0.5 a minute


def test_a_span_longer_than_the_history_reports_what_it_measured(server):
    fable = answer(server)["models"]["Fable"]
    assert fable["percent_per_minute"]["last_20_min"] == 0.25
    assert "measured_minutes" not in fable  # 40% was already in force 20 minutes ago


def test_reset_times_are_given_in_utc_and_local_time(server, state):
    five_hour = answer(server)["plan"]["five_hour"]
    assert five_hour["resets_at"].endswith("+00:00")
    assert five_hour["resets_at_local"] == server.iso_local(state.five_hour_reset)


def test_stale_per_model_data_is_left_out_with_a_note(server, state):
    state.usage(age=900)
    result = answer(server)
    assert "models" not in result
    assert re.search(r"usage endpoint was last read 90\d seconds ago", result["note"])
    assert "Fable" not in result["exhaustion_at_last_2_min_rate"]


def test_history_rows_that_do_not_match_the_schema_are_skipped(server, state):
    day = time.strftime("%Y-%m-%d", time.gmtime(state.now))
    with open(state.path("accounts", ACCOUNT, "history", f"{day}.jsonl"), "a") as f:
        f.write(json.dumps({"t": state.now - 30, "source": "payload"}) + "\n")  # no session_id
        f.write('{"t": 1, "source": "pay')  # cut short by a write in progress
    assert answer(server)["plan"]["five_hour"]["percent_per_minute"]["last_2_min"] == 1.0


def test_the_main_conversations_context_comes_from_the_recording(server, state):
    reading = {"used_tokens": 1000, "window_tokens": 200000, "compacts_at_tokens": 167000}
    state.session(context={"model": "claude-opus-5-5", **reading})
    assert answer(server)["context"] == {"agent": "main", "model": "claude-opus-5-5", **reading}


def test_a_subagents_context_comes_from_its_own_transcript(server, state, tmp_path):
    transcript = tmp_path / "project" / f"{SESSION}.jsonl"
    state.session(
        transcript_path=str(transcript),
        context={"used_tokens": 1000, "window_tokens": 200000, "compacts_at_tokens": 167000},
    )
    usage = {
        "input_tokens": 3,
        "cache_read_input_tokens": 20000,
        "cache_creation_input_tokens": 500,
    }
    records = [
        {"type": "assistant", "message": {"model": "claude-haiku-4-5", "usage": usage}},
        {"type": "assistant", "message": {"model": "<synthetic>", "usage": {"input_tokens": 0}}},
        {"type": "user", "message": {"content": "tool result"}},
    ]
    path = transcript.parent / SESSION / "subagents" / "agent-a1.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in records))
    result = call(server, {"session_id": SESSION, "agent_id": "a1"}).structured_content
    assert result["context"] == {
        "agent": "subagent",
        "model": "claude-haiku-4-5",
        "used_tokens": 20503,
    }
    result = call(server, {"session_id": SESSION, "agent_id": "a2"}).structured_content
    assert "context" not in result  # no transcript: left out, never the main conversation's


def test_the_answer_is_structured_content_with_a_matching_text_block(server):
    async def run():
        async with Client(server.app) as client:
            tools = (await client.list_tools()).tools
            return tools[0], await client.call_tool("get-quota", {"session_id": SESSION})

    tool, result = asyncio.run(run())
    assert tool.input_schema["properties"] == {}
    assert set(tool.output_schema["required"]) == {
        "plan_as_of",
        "exhaustion_at_last_2_min_rate",
        "plan",
    }
    assert json.loads(result.content[0].text) == result.structured_content


def test_rate_needs_a_minimum_span_and_starts_a_young_window_at_zero(server):
    now = 10_000.0
    assert server.rate([], now, 5, window_start=now - 60, span=10) is None  # 1 minute of window
    assert server.rate([], now, 6, window_start=now - 180, span=10) == (2.0, 3.0)
    late = [(now - 240, 4)]  # recording began 4 minutes ago, inside a 10-minute span
    assert server.rate(late, now, 6, window_start=0, span=10) == (0.5, 4.0)


def test_the_published_schemas_match_the_models(server, tmp_path):
    server.write_schemas(str(tmp_path))
    for name in ("session", "usage", "history-row"):
        with open(os.path.join(SCHEMAS, f"{name}.schema.json")) as f:
            published = json.load(f)
        with open(tmp_path / f"{name}.schema.json") as f:
            assert json.load(f) == published, f"run server.py --write-schemas ({name})"


# ── the contract with statusline.py ───────────────────────────────────────────


def test_what_statusline_writes_matches_the_schemas_and_the_server_reads_it(tmp_path, monkeypatch):
    state, config = tmp_path / "state", tmp_path / "config"
    config.mkdir()
    monkeypatch.setenv("CLAUDE_QUOTA_STATE", str(state))
    write(
        str(config / ".claude.json"),
        {"oauthAccount": {"accountUuid": ACCOUNT, "emailAddress": "a@b"}},
    )
    seven_day_reset = int(time.time() + 3 * 86400)
    usage = {
        "fetched_at": time.time(),
        "usage": {
            "seven_day": {
                "resets_at": time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(seven_day_reset))
            },
            "limits": [],
        },
    }
    write(str(state / "accounts" / ACCOUNT / "usage.json"), usage)
    env = dict(
        os.environ, CLAUDE_QUOTA_STATE=str(state), CLAUDE_CONFIG_DIR=str(config), COLUMNS="150"
    )
    for cost, five in ((0.0, 20), (0.5, 21), (1.0, 22)):
        payload = {
            "session_id": SESSION,
            "cost": {"total_cost_usd": cost},
            "context_window": {
                "total_input_tokens": int(cost * 1000),
                "total_output_tokens": 1,
                "context_window_size": 200000,
            },
            "rate_limits": {
                "five_hour": {"used_percentage": five, "resets_at": int(time.time() + 3600)},
                "seven_day": {"used_percentage": 10, "resets_at": seven_day_reset},
            },
        }
        subprocess.run(
            [sys.executable, STATUSLINE, "--record-only"],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            env=env,
            check=True,
        )

    def validator(name):
        with open(os.path.join(SCHEMAS, f"{name}.schema.json")) as f:
            return jsonschema.Draft202012Validator(json.load(f))

    with open(state / "sessions" / f"{SESSION}.json") as f:
        validator("session").validate(json.load(f))
    with open(state / "accounts" / ACCOUNT / "usage.json") as f:
        validator("usage").validate(json.load(f))
    history = sorted((state / "accounts" / ACCOUNT / "history").glob("*.jsonl"))
    rows = [json.loads(line) for path in history for line in path.read_text().splitlines()]
    assert [r["five_hour"]["used_percentage"] for r in rows] == [21, 22]
    for row in rows:
        validator("history-row").validate(row)

    result = answer(load())
    assert result["plan"]["five_hour"]["used_percent"] == 22
    assert result["context"]["used_tokens"] == 1000
