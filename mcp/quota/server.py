#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["mcp>=2.2,<3"]
# ///

"""An MCP server with one tool, get-quota: the plan quota the status line shows, and its pace.

It fetches nothing. statusline.py records every figure it draws, and this server reads those
recordings, so the tool answers with exactly what the status line shows: the five-hour and
seven-day plan windows as of the session's last API response, and the per-model weekly windows as
of the last usage-endpoint fetch, about a minute old at most. statusline/RECORDING.md describes the
layout this reads.

Stale data is an error, never a number. A recording that stopped being updated reads as quota not
being spent at all, which is the most misleading answer this tool could give, so every figure is
checked for freshness before it is reported and the tool refuses when it cannot vouch for one.

The tool publishes no parameters. Which session is asking cannot be read from anywhere else: the
CLAUDE_CODE_SESSION_ID this process inherited is fixed when Claude Code starts it and goes stale on
`/clear` or an in-session `/resume`, and no MCP request carries the current one. So hook.py, a
PreToolUse hook matched to this tool, adds the calling session's ID to every call's arguments.
The published schema does not declare it, which leaves the model nothing to fill in or get wrong,
and InjectedArgs below is the private allow-list it is parsed against.

Run it with `uv run --script server.py`. README.md beside it has the settings to add.
"""

import asyncio
import datetime
import glob
import json
import os
import time
from uuid import UUID

from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import BaseModel, ConfigDict, ValidationError

RECORD_SCHEMA = 1  # the statusline.py recording layout this reads
FRESH = 60  # a session drawn this recently is still being recorded
RENDER_WAIT = 1.0  # seconds to wait for the render of the response that made this call
USAGE_STALE_AFTER = 300  # per-model quota data older than this is left out rather than reported
RATE_SPANS = (15, 60)  # minutes each rate is measured over
MIN_SPAN = 5  # minutes of history below which one whole-percent step would swamp a rate
SAME_RESET = 60  # seconds apart within which two readings belong to the same window
WINDOW_SECONDS = {"five_hour": 5 * 3600, "seven_day": 7 * 86400}

app = MCPServer("quota")


def state_dir(*parts):
    """A path under statusline.py's state directory, resolved the same way it resolves it."""
    root = os.environ.get("CLAUDE_QUOTA_STATE") or os.path.join(
        os.environ.get("XDG_STATE_HOME")
        or os.path.join(os.path.expanduser("~"), ".local", "state"),
        "claude-quota",
    )
    return os.path.join(root, *parts)


def read_json(path):
    """Parsed JSON object at path, or {} when it is missing, unreadable or not an object."""
    try:
        with open(path) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def iso(epoch):
    """ISO 8601 UTC for epoch seconds, to the second."""
    return datetime.datetime.fromtimestamp(epoch, datetime.UTC).isoformat(timespec="seconds")


def iso_epoch(s):
    """Epoch seconds for an ISO 8601 timestamp, or 0 when it is absent or unparseable."""
    try:
        return datetime.datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except (AttributeError, ValueError):
        return 0


class InjectedArgs(BaseModel):
    """Private allow-list: exactly what hook.py injects, and nothing else."""

    model_config = ConfigDict(extra="forbid")
    session_id: UUID


def injected_args(ctx: Context) -> InjectedArgs:
    """The hook-injected arguments, parsed from the raw request rather than the signature.

    The tool's signature declares no parameters, so the SDK passes it none; the raw arguments are
    still on the request context, which is where the hook's session_id arrives.
    """
    arguments = ctx.request_context.params.get("arguments") or {}
    if "session_id" not in arguments:
        raise ToolError(
            "get-quota was called without a session_id, so its PreToolUse hook (hook.py) is not "
            "installed. See mcp/quota/README.md."
        )
    unexpected = sorted(set(arguments) - set(InjectedArgs.model_fields))
    if unexpected:
        raise ToolError(f"get-quota takes no arguments; received {', '.join(unexpected)}.")
    try:
        return InjectedArgs.model_validate(arguments)
    except ValidationError:
        raise ToolError(
            f"get-quota received session_id {arguments['session_id']!r}, which is not a session "
            "ID. Its PreToolUse hook (hook.py) sets it; nothing else should."
        ) from None


async def current_session(session_id):
    """The session's recording, once the render of the response that made this call has landed.

    The response that issued this call triggers a render a few hundred milliseconds after it
    arrives, often after the call itself has started. Waiting briefly for it means the answer
    includes that response's figures rather than the one before it.
    """
    path = state_dir("sessions", f"{session_id}.json")
    called = time.time()
    deadline = called + RENDER_WAIT
    session = read_json(path)
    while session.get("rendered_at", 0) < called and time.time() < deadline:
        await asyncio.sleep(0.1)
        session = read_json(path)
    return session


def check(session):
    """Raise ToolError unless the session's plan reading can be vouched for; see RECORDING.md."""
    if not session:
        raise ToolError(
            "No status line recording exists for this session. get-quota reads what statusline.py "
            "records, so the statusLine command must run statusline.py, or statusline.py "
            "--record-only to keep Claude Code's own footer. See mcp/quota/README.md."
        )
    if session.get("schema") != RECORD_SCHEMA:
        raise ToolError(
            f"The status line recording has schema {session.get('schema')!r}, and this server "
            f"reads schema {RECORD_SCHEMA}. Update whichever of the two is older."
        )
    age = time.time() - (session.get("rendered_at") or 0)
    if age > FRESH:
        raise ToolError(
            f"The status line last recorded this session {age:.0f} seconds ago, so its figures "
            "cannot be vouched for. It is recorded whenever the line is drawn; setting "
            "statusLine.refreshInterval keeps it drawn while nothing else changes."
        )
    if not session.get("rate_limits") or session.get("response_at") is None:
        raise ToolError(
            "This session has no quota data yet. Claude Code reports it with each API response, "
            "and has not since this session started recording or since the last /login. Call "
            "get-quota again after the next response. Sessions on an API key, Bedrock or Vertex "
            "never report it."
        )
    if not session.get("matches_login"):
        raise ToolError(
            "The quota data on hand is not the logged-in account's yet. After /login a session "
            "can go on reporting the previous account's quota for several responses, and the new "
            "account's is recognised once its first usage fetch lands, within a minute. Call "
            "get-quota again after the next response."
        )


def history(account):
    """Every history row recorded for the account, oldest first.

    All of it rather than the last hour: a rate needs the reading that was in force when its span
    began, and when nothing moved for a while that reading is older than the span. Rows are written
    only when a figure moves and are kept for nine days, so the whole of it stays small.
    """
    rows = []
    for path in glob.glob(state_dir("accounts", account, "history", "*.jsonl")):
        try:
            with open(path) as f:
                for line in f:
                    try:
                        row = json.loads(line)
                    except ValueError:
                        continue  # a line cut short by a write in progress
                    if isinstance(row, dict) and isinstance(row.get("t"), (int, float)):
                        rows.append(row)
        except OSError:
            continue
    return sorted(rows, key=lambda r: r["t"])


def rate(series, now, used, window_start, span):
    """Percentage points per minute over the last span minutes, or None with too little to go on.

    series is [(epoch, percent)] for one window. The starting point is the reading in force when
    the span began, or the window's own start, where usage is zero by definition, when the window
    is younger than the span. Failing both, because recording began partway through, the earliest
    reading stands in and the span actually measured is reported rather than the one asked for.

    Percentages are whole numbers, so over a minute or two a single step reads as a steep rate and
    a projection built on it as an imminent limit. Under MIN_SPAN minutes there is no rate.
    """
    since = max(now - span * 60, window_start)
    before = [p for t, p in series if t <= since]
    if before:
        base_t, base_p = since, before[-1]
    elif since == window_start:
        base_t, base_p = window_start, 0.0
    else:
        after = [(t, p) for t, p in series if t > since]
        if not after:
            return None
        base_t, base_p = after[0]
    minutes = (now - base_t) / 60
    if minutes < MIN_SPAN:
        return None
    return {
        "percent_per_minute": round((used - base_p) / minutes, 3),
        "over_minutes": round(minutes, 1),
    }


def window_report(used, resets_at, series, now, window_seconds):
    """One window's reading, its rates, and when it runs out at the shortest-span rate."""
    reset_minutes = (resets_at - now) / 60
    rates = {
        f"last_{span}_min": rate(series, now, used, resets_at - window_seconds, span)
        for span in RATE_SPANS
    }
    report = {
        "used_percent": used,
        "resets_at": iso(resets_at),
        "resets_in_minutes": round(reset_minutes),
        "rate": rates,
    }
    pace = next((r["percent_per_minute"] for r in rates.values() if r), None)
    if pace and pace > 0:
        to_limit = (100 - used) / pace
        report["minutes_to_limit_at_this_rate"] = round(to_limit)
        report["reaches_limit_before_reset"] = to_limit < reset_minutes
    return report


def plan_report(session, rows, now):
    """The five-hour and seven-day windows, as of the session's last API response."""
    report = {}
    for key, window in session["rate_limits"].items():
        resets_at, used = window.get("resets_at") or 0, window.get("used_percentage") or 0
        series = [
            (r["t"], r[key]["used_percentage"])
            for r in rows
            if r.get("source") == "payload"
            and abs(((r.get(key) or {}).get("resets_at") or 0) - resets_at) <= SAME_RESET
        ]
        report[key] = window_report(used, resets_at, series, now, WINDOW_SECONDS[key])
    return report


def model_report(account, rows):
    """The per-model weekly windows as of the last fetch, or None and the reason they are left out.

    Left out once the last fetch is older than USAGE_STALE_AFTER, the same age at which the status
    line shows STL in their place.
    """
    recorded = read_json(state_dir("accounts", account, "usage.json"))
    fetched_at = recorded.get("fetched_at") or 0
    age = time.time() - fetched_at
    if age > USAGE_STALE_AFTER:
        return None, (
            "Per-model quota data is left out: the usage endpoint was last read "
            + (f"{age:.0f} seconds ago." if fetched_at else "never.")
        )
    report = {}
    for limit in (recorded.get("usage") or {}).get("limits") or []:
        name = (((limit.get("scope") or {}).get("model") or {}).get("display_name") or "").strip()
        used, resets_at = limit.get("percent"), iso_epoch(limit.get("resets_at"))
        if not name or not isinstance(used, (int, float)) or not resets_at:
            continue
        series = [
            (r["t"], entry.get("percent"))
            for r in rows
            if r.get("source") == "endpoint"
            for entry in r.get("limits") or []
            if (((entry.get("scope") or {}).get("model") or {}).get("display_name") or "") == name
            and abs(iso_epoch(entry.get("resets_at")) - resets_at) <= SAME_RESET
            and isinstance(entry.get("percent"), (int, float))
        ]
        window = window_report(used, resets_at, series, fetched_at, WINDOW_SECONDS["seven_day"])
        window["fetched_seconds_ago"] = round(age)
        if name not in report or used > report[name]["used_percent"]:
            report[name] = window
    return report, None


@app.tool(
    name="get-quota",
    description=(
        "Claude plan quota for the account this session runs on: the five-hour and seven-day plan "
        "windows and any per-model weekly windows, each with percent used, reset time, recent "
        "rate of use in percentage points per minute, and minutes until the limit at that rate. "
        "Figures match the status line. Percentages are whole numbers, so short-span rates move "
        "in steps. A rate is null until five minutes of history have been recorded for its window."
    ),
    annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False),
)
async def get_quota(ctx: Context) -> str:
    injected = injected_args(ctx)
    session = await current_session(injected.session_id)
    check(session)
    now = time.time()
    account = session["account_uuid"]
    rows = history(account)
    models, omitted = model_report(account, rows)
    answer = {
        "account": session.get("account_label"),
        "plan_as_of": iso(session["response_at"]),
        "plan": plan_report(session, rows, now),
        "models": models,
    }
    if omitted:
        answer["note"] = omitted
    return json.dumps(answer, indent=2)


if __name__ == "__main__":
    app.run()
