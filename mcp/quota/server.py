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
import sys
import time
from typing import Annotated, Literal
from uuid import UUID

from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    ValidationError,
    model_serializer,
)

RECORD_SCHEMA = 1  # the recording layout this reads; see schemas/ and statusline/RECORDING.md
FRESH = 60  # a session drawn this recently is still being recorded
RENDER_WAIT = 1.0  # seconds to wait for the render of the response that made this call
USAGE_STALE_AFTER = 300  # per-model quota data older than this is left out rather than reported
# Minutes each rate is measured over. The first is the current rate the exhaustion forecast uses:
# consumption can change within a couple of minutes, when several subagents start at once. The
# tool description names these spans, so change it with them.
RATE_SPANS = (2, 10, 20)
MIN_SPAN = 2  # minutes of history below which there is no rate at all
SAME_RESET = 60  # seconds apart within which two readings belong to the same window
WINDOW_SECONDS = {"five_hour": 5 * 3600, "seven_day": 7 * 86400}

# Shown to the model at session start, unlike the tool description, which a session with many tools
# loads only on demand. So what prompts a call, and how to act on the answer, belong here.
INSTRUCTIONS = """\
get-quota reports how much of the Claude plan's quota is left and how fast it is being spent. Call
it before starting large or parallel work, such as several subagents or a long multi-step task, and
when the user asks about quota or limits. It does not need calling on every turn.

Use it to decide what to start, never to slow down. Work as fast as the task allows: do not pause
agents, reduce parallelism or put work off to make quota last until a reset. Reaching a limit only
pauses work until that window resets, and quota left unused at a reset is lost. Work that as a
whole spans several five-hour windows is normal. The rates count every session on the account,
including other sessions and other machines you cannot see. Use you did not cause is normal: plan
around it, and do not investigate it.

Quota is spent by model requests, not by time. Each request re-sends the whole context, so a large
context, large tool output and parallel agents cost the most. Time spent waiting on builds, tests
or other processes costs nothing. A limit pauses only the model; processes already running carry
on. context in the answer is the calling conversation's own.

Before starting new work, estimate how long it will take, and compare that with the smallest
in_minutes in exhaustion_at_last_2_min_rate, shortened for the work you are about to add, since
each extra parallel agent raises the rate. Then:

1. If the seven-day window would run out before it resets, do not start: tell the user and ask
   first. That window takes days to reset.
2. If the work will finish before the limit, start it.
3. If it will not, do not start it now. Start work that will finish before the limit instead, or a
   part of it that reaches a checkpoint, and tell the user when the limit is expected and when the
   window resets, in local time. Work already running carries on.

A window's guidance field, when present, says which of these applies to it. Check the 2-minute
rate against the 10- and 20-minute rates before acting on it: one whole-percent step can make the
2-minute rate look like a burst.

When a window passes 90%, 95% or 99%, a note headed [quota] arrives after a tool call or with the
user's next message, carrying this answer. Act on it as above, then carry on.

In a subagent, the work is the rest of your task. If you stop early or narrow the task because of
quota, say so in your final report: what is done, what is left, and when the window resets."""

app = MCPServer("quota", instructions=INSTRUCTIONS)


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


def iso_local(epoch):
    """ISO 8601 in this machine's time zone for epoch seconds, to the second, offset included."""
    return datetime.datetime.fromtimestamp(epoch).astimezone().isoformat(timespec="seconds")


def iso_epoch(s):
    """Epoch seconds for an ISO 8601 timestamp, or 0 when it is absent or unparseable."""
    try:
        return datetime.datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except (AttributeError, ValueError):
        return 0


# ── what this server reads ────────────────────────────────────────────────────
#
# The recording's schema, written as the models it is validated against. `server.py
# --write-schemas` publishes them as JSON Schema under schemas/, so that a recorder other than
# statusline.py can write the same files. Fields a recorder keeps for itself are allowed through.


class PlanReading(BaseModel):
    model_config = ConfigDict(extra="allow")
    used_percentage: float
    resets_at: float = Field(description="Epoch seconds")


class ContextReading(BaseModel):
    model_config = ConfigDict(extra="allow")
    model: str | None = None
    used_tokens: int = Field(description="Tokens in context as of the last API response")
    window_tokens: int
    compacts_at_tokens: int = Field(description="Where auto-compaction fires")


class SessionRecord(BaseModel):
    """sessions/<session_id>.json: one session's latest reading, rewritten on every render."""

    model_config = ConfigDict(extra="allow", title="Quota recording: session")
    schema_version: Literal[1] = Field(alias="schema")
    session_id: str
    account_uuid: str | None = Field(description="The login at this render")
    account_label: str | None = Field(default=None, description="The account name to show")
    rendered_at: float = Field(description="Epoch seconds of this render")
    response_at: float | None = Field(
        description="Epoch seconds of the render that first showed the latest API response"
    )
    matches_login: bool = Field(description="Whether rate_limits is account_uuid's own reading")
    rate_limits: dict[Literal["five_hour", "seven_day"], PlanReading] | None
    transcript_path: str | None = Field(default=None, description="The main conversation's")
    context: ContextReading | None = Field(
        default=None, description="The main conversation's context; null before a response"
    )


class UsageLimit(BaseModel):
    model_config = ConfigDict(extra="allow")
    kind: str | None = None
    percent: float | None = None
    resets_at: str | None = Field(default=None, description="ISO 8601")
    scope: dict | None = Field(default=None, description="scope.model.display_name names a model")


class UsageAnswer(BaseModel):
    model_config = ConfigDict(extra="allow")
    limits: list[UsageLimit] | None = None


class UsageRecord(BaseModel):
    """accounts/<account_uuid>/usage.json: the usage endpoint's last answer for the account."""

    model_config = ConfigDict(extra="allow", title="Quota recording: usage")
    fetched_at: float = Field(description="Epoch seconds")
    usage: UsageAnswer = Field(description="The endpoint's answer, verbatim")


class PayloadRow(BaseModel):
    model_config = ConfigDict(extra="allow")
    t: float = Field(description="Epoch seconds")
    source: Literal["payload"]
    session_id: str
    five_hour: PlanReading | None = None
    seven_day: PlanReading | None = None


class EndpointRow(BaseModel):
    model_config = ConfigDict(extra="allow")
    t: float = Field(description="Epoch seconds")
    source: Literal["endpoint"]
    limits: list[UsageLimit] | None = None


HistoryRow = TypeAdapter(Annotated[PayloadRow | EndpointRow, Field(discriminator="source")])


def schema_errors(exc):
    """A ValidationError's first few problems, as one line."""
    return "; ".join(
        f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}"
        for error in exc.errors()[:3]
    )


def write_schemas(directory):
    """Publish the recording models as JSON Schema files in directory."""
    os.makedirs(directory, exist_ok=True)
    for name, schema in (
        ("session", SessionRecord.model_json_schema(by_alias=True)),
        ("usage", UsageRecord.model_json_schema(by_alias=True)),
        ("history-row", HistoryRow.json_schema(by_alias=True)),
    ):
        schema = {"$schema": "https://json-schema.org/draft/2020-12/schema", **schema}
        schema.setdefault("title", f"Quota recording: {name}")
        with open(os.path.join(directory, f"{name}.schema.json"), "w") as f:
            json.dump(schema, f, indent=2)
            f.write("\n")


# ── what this server returns ──────────────────────────────────────────────────


class OmitUnset(BaseModel):
    """A result model whose optional fields are left out when unset, rather than sent as null.

    measured_minutes, for one, means something only when present, and a null on every window
    would read as a value.
    """

    @model_serializer(mode="wrap")
    def _omit_unset(self, handler):
        return {key: value for key, value in handler(self).items() if value is not None}


class Exhaustion(BaseModel):
    in_minutes: int | None = Field(
        description="Minutes until 100% at the current rate; null when that rate is zero or "
        "unmeasured"
    )
    resets_in_minutes: int


class Window(OmitUnset):
    used_percent: int | float
    resets_at: str = Field(description="ISO 8601 UTC")
    resets_at_local: str = Field(description="The same instant in the user's time zone")
    resets_in_minutes: int
    percent_per_minute: dict[str, float] = Field(
        description="Rate of use over each span, keyed last_<n>_min, every span ending now"
    )
    measured_minutes: dict[str, float] | None = Field(
        default=None,
        description="Minutes actually covered, only for a span reaching back past the start of "
        "the window or of the recording",
    )
    fetched_seconds_ago: int | None = Field(
        default=None, description="Per-model windows only: age of the usage-endpoint reading"
    )
    guidance: str | None = Field(
        default=None, description="What to do about this window; absent when nothing applies"
    )


class ContextUse(OmitUnset):
    agent: Literal["main", "subagent"] = Field(description="Whose context this is: the caller's")
    model: str | None = None
    used_tokens: int
    window_tokens: int | None = Field(default=None, description="Main conversation only")
    compacts_at_tokens: int | None = Field(
        default=None, description="Main conversation only: where auto-compaction fires"
    )


class Quota(OmitUnset):
    """What get-quota returns. The SDK derives the tool's outputSchema from this."""

    account: str | None = None
    plan_as_of: str = Field(description="When this session's last API response arrived")
    # Named for RATE_SPANS[0]; change the name with it.
    exhaustion_at_last_2_min_rate: dict[str, Exhaustion]
    plan: dict[str, Window]
    models: dict[str, Window] | None = None
    context: ContextUse | None = None
    note: str | None = None


class InjectedArgs(BaseModel):
    """Private allow-list: exactly what hook.py injects, and nothing else."""

    model_config = ConfigDict(extra="forbid")
    session_id: UUID
    # A subagent's call only. The pattern keeps it to a file-name component, since it names the
    # subagent's transcript.
    agent_id: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]+$")


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
            f"get-quota received session_id {arguments['session_id']!r} and agent_id "
            f"{arguments.get('agent_id')!r}, which are not a session ID and an agent ID. Its "
            "PreToolUse hook (hook.py) sets them; nothing else should."
        ) from None


async def current_session(session_id, wait=None):
    """The session's recording, once the render of the response that made this call has landed.

    The response that issued this call triggers a render a few hundred milliseconds after it
    arrives, often after the call itself has started. Waiting briefly for it means the answer
    includes that response's figures rather than the one before it.
    """
    path = state_dir("sessions", f"{session_id}.json")
    called = time.time()
    deadline = called + (RENDER_WAIT if wait is None else wait)
    session = read_json(path)
    while session.get("rendered_at", 0) < called and time.time() < deadline:
        await asyncio.sleep(0.1)
        session = read_json(path)
    return session


def check(session):
    """Raise ToolError unless the session's plan reading can be vouched for; see RECORDING.md."""
    if not session:
        raise ToolError(
            "No quota recording exists for this session. get-quota reads what a recorder writes, "
            "normally statusline.py as the statusLine command, or statusline.py --record-only to "
            "keep Claude Code's own footer. See mcp/quota/README.md."
        )
    if session.get("schema") != RECORD_SCHEMA:
        raise ToolError(
            f"This session's quota recording has schema {session.get('schema')!r}, and this server "
            f"reads schema {RECORD_SCHEMA}. Update whichever of the two is older."
        )
    try:
        SessionRecord.model_validate(session)
    except ValidationError as exc:
        raise ToolError(
            "This session's recording does not match mcp/quota/schemas/session.schema.json: "
            + schema_errors(exc)
        ) from None
    age = time.time() - (session.get("rendered_at") or 0)
    if age > FRESH:
        raise ToolError(
            f"This session's quota recording was last written {age:.0f} seconds ago, so its data "
            "cannot be vouched for. statusline.py writes it whenever the line is drawn; setting "
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
                    try:
                        HistoryRow.validate_python(row)
                    except ValidationError:
                        continue
                    rows.append(row)
        except OSError:
            continue
    return sorted(rows, key=lambda r: r["t"])


def rate(series, now, used, window_start, span):
    """(percentage points per minute, minutes measured) over the last span minutes, or None.

    series is [(epoch, percent)] for one window. The starting point is the reading in force when
    the span began, or the window's own start, where usage is zero by definition, when the window
    is younger than the span. Failing both, because recording began partway through, the earliest
    reading stands in. Either way the minutes actually measured are returned beside the rate.

    Percentages are whole numbers, so over a short span a single step reads as a steep rate: one
    step in two minutes is half a point a minute. Under MIN_SPAN minutes there is no rate.
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
    return round((used - base_p) / minutes, 3), round(minutes, 1)


def window_report(used, resets_at, series, now, window_seconds):
    """One window's reading and its rate over each span.

    measured_minutes is added only for a span that reaches back past the start of the window or of
    the recording, so that a rate measured over less than its span says so.
    """
    report = {
        "used_percent": used,
        "resets_at": iso(resets_at),
        "resets_at_local": iso_local(resets_at),
        "resets_in_minutes": round((resets_at - now) / 60),
        "percent_per_minute": {},
    }
    measured = {}
    for span in RATE_SPANS:
        found = rate(series, now, used, resets_at - window_seconds, span)
        if found is None:
            continue
        key = f"last_{span}_min"
        report["percent_per_minute"][key], minutes = found
        if minutes < span:
            measured[key] = minutes
    if measured:
        report["measured_minutes"] = measured
    return report


def exhaustion(windows):
    """Each window's minutes until 100% at its current rate, beside its minutes until it resets.

    A straight line from the current rate, and nothing more. The longer spans in each window show
    whether that rate is a burst or steady, which is the judgement the forecast leaves to the
    reader.
    """
    current = f"last_{RATE_SPANS[0]}_min"
    forecast = {}
    for name, window in windows.items():
        pace = window["percent_per_minute"].get(current)
        remaining = max(0, 100 - window["used_percent"])
        forecast[name] = {
            "in_minutes": round(remaining / pace) if pace and pace > 0 else None,
            "resets_in_minutes": window["resets_in_minutes"],
        }
    return forecast


# Each tells the model what to do, and carries no numbers: the window it sits in has them.
GUIDE_SHORT = (
    "Start only work that will finish before this window runs out. Work in progress carries on."
)
GUIDE_WEEKLY = "Ask the user before starting large work."
GUIDE_BURST = (
    "The 2-minute rate is a burst. At the 10-minute rate, this window lasts until it resets."
)


def runs_out(used, pace, resets_in_minutes):
    """Whether a window reaches 100% before it resets, at pace percentage points a minute."""
    return bool(pace and pace > 0) and max(0, 100 - used) / pace < resets_in_minutes


def guidance(window, weekly):
    """What to do about a window that runs out before it resets at the 2-minute rate, or None.

    A window that runs out at the 2-minute rate but not at the 10-minute rate is in a burst, and
    the guidance says so rather than raising an alarm the steadier rate does not support. weekly is
    true for the seven-day plan window and the per-model weekly windows, which take days to reset.
    """
    rates, used, left = (
        window["percent_per_minute"],
        window["used_percent"],
        window["resets_in_minutes"],
    )
    if not runs_out(used, rates.get(f"last_{RATE_SPANS[0]}_min"), left):
        return None
    steady = rates.get(f"last_{RATE_SPANS[1]}_min")
    if steady is not None and not runs_out(used, steady, left):
        return GUIDE_BURST
    return GUIDE_WEEKLY if weekly else GUIDE_SHORT


def subagent_context(transcript_path, session_id, agent_id):
    """A subagent's context as of its last API response, from its own transcript, or None.

    The tool call being answered was written to the transcript before it ran, with the usage of
    the response that made it, so the last assistant record is current. Only the tail is read.
    """
    if not transcript_path:
        return None
    path = os.path.join(
        os.path.dirname(transcript_path), session_id, "subagents", f"agent-{agent_id}.jsonl"
    )
    try:
        with open(path, "rb") as f:
            f.seek(max(0, os.path.getsize(path) - 256 * 1024))
            lines = f.read().decode(errors="replace").splitlines()
    except OSError:
        return None
    for line in reversed(lines):
        try:
            message = json.loads(line).get("message") or {}
        except (ValueError, AttributeError):
            continue  # the first line, cut short by the seek, or not an object
        usage = message.get("usage") if isinstance(message, dict) else None
        if not usage or message.get("model") == "<synthetic>":
            continue
        used = sum(
            usage.get(key) or 0
            for key in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")
        )
        return {"agent": "subagent", "model": message.get("model"), "used_tokens": used}
    return None


def context_report(session, injected):
    """The caller's context: the main conversation's from the recording, a subagent's from its
    transcript. Left out when it cannot be read, since a wrong figure would mislead."""
    if injected.agent_id:
        return subagent_context(
            session.get("transcript_path"), str(injected.session_id), injected.agent_id
        )
    reading = session.get("context")
    return {"agent": "main", **reading} if reading else None


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
    try:
        UsageRecord.model_validate(recorded)
    except ValidationError as exc:
        if recorded:
            return None, (
                "Per-model quota data is left out: usage.json does not match "
                "mcp/quota/schemas/usage.schema.json: " + schema_errors(exc)
            )
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
        "Claude plan quota for the account this session runs on, counting every session and "
        "subagent on it: the five-hour and seven-day plan windows and any per-model weekly "
        "windows. exhaustion_at_last_2_min_rate lists every window with the minutes until it "
        "reaches 100% if the last 2 minutes' rate continues, beside the minutes until it resets; "
        "in_minutes is null when that rate is zero or not yet measured. Each window also has "
        "percent used and percent_per_minute, its rate of use over the last 2, 10 and 20 "
        "minutes, every span ending now, which shows whether the current rate is a burst or "
        "steady. measured_minutes appears only for a span that reaches back past the start of "
        "the window or of the recording, and gives the minutes actually covered; it says nothing "
        "about when or how much a model was used. The data matches the status line. "
        "Percentages are whole numbers, so the 2-minute rate moves in steps of 0.5 points a "
        "minute."
    ),
    annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False),
)
async def get_quota(ctx: Context) -> Quota:
    return await quota(injected_args(ctx))


async def quota(injected, wait=None):
    """The answer for the session and agent named by injected; ToolError when it cannot be given."""
    session = await current_session(injected.session_id, wait)
    check(session)
    now = time.time()
    account = session["account_uuid"]
    rows = history(account)
    plan = plan_report(session, rows, now)
    models, omitted = model_report(account, rows)
    for name, window in plan.items():
        window["guidance"] = guidance(window, weekly=name == "seven_day")
    for window in (models or {}).values():
        window["guidance"] = guidance(window, weekly=True)
    answer = {
        "account": session.get("account_label"),
        "plan_as_of": iso(session["response_at"]),
        "exhaustion_at_last_2_min_rate": exhaustion({**plan, **(models or {})}),
        "plan": plan,
        "models": models,
        "context": context_report(session, injected),
    }
    if omitted:
        answer["note"] = omitted
    return Quota.model_validate(answer)


def print_answer(session_id, agent_id=None):
    """Print the answer get-quota would give the session or subagent, for notice.py to pass on.

    Exit status 1, with the refusal printed, when the tool would refuse. No render is waited for:
    the hook runs after the response it follows was drawn.
    """
    try:
        injected = InjectedArgs(session_id=session_id, agent_id=agent_id)
        answer = asyncio.run(quota(injected, wait=0))
    except (ToolError, ValidationError) as exc:
        print(exc)
        return 1
    print(answer.model_dump_json())
    return 0


if __name__ == "__main__":
    if sys.argv[1:2] == ["--write-schemas"]:
        write_schemas(os.path.join(os.path.dirname(os.path.abspath(__file__)), "schemas"))
    elif sys.argv[1:2] == ["--answer"] and len(sys.argv) in (3, 4):
        sys.exit(print_answer(*sys.argv[2:]))
    else:
        app.run()
