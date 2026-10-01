"""Tests for statusline.py's recording and usage-endpoint fetch.

Run from the repository root:

    uv run --with pytest pytest statusline/tests

Renders run the script as Claude Code does, a subprocess fed a payload on stdin, against a state
directory and a config directory of the test's own. The fetch tests point the endpoint at a local
server, so nothing here reaches the network or touches the real recording.
"""

import glob
import http.server
import importlib.util
import json
import os
import subprocess
import sys
import threading
import time

import pytest

SCRIPT = os.path.join(os.path.dirname(__file__), "..", "statusline.py")
WEEK = {"acc-a": 1791378000, "acc-b": 1791010800}  # each account's seven-day reset
FIVE_HOUR_RESET = 1790814000


def load():
    spec = importlib.util.spec_from_file_location("statusline", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Machine:
    """A state directory, a config directory and one session, rendered as Claude Code would."""

    session = "aaaaaaaa-0000-4000-8000-000000000001"

    def __init__(self, tmp_path):
        self.state = str(tmp_path / "state")
        self.config = str(tmp_path / "config")
        os.makedirs(self.config)
        self.cost = 0.0
        self.env = dict(
            os.environ, CLAUDE_QUOTA_STATE=self.state, CLAUDE_CONFIG_DIR=self.config, COLUMNS="150"
        )

    def login(self, account):
        with open(os.path.join(self.config, ".claude.json"), "w") as f:
            json.dump({"oauthAccount": {"accountUuid": account, "emailAddress": "x@y"}}, f)

    def fetched(self, account):
        """A fresh usage.json for account, which also keeps renders from starting a fetch."""
        path = os.path.join(self.state, "accounts", account, "usage.json")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        resets = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime(WEEK[account]))
        with open(path, "w") as f:
            json.dump({"fetched_at": time.time(), "usage": {"seven_day": {"resets_at": resets}}}, f)

    def render(self, reading_account, five_hour, response=True, args=(), payload=None):
        """Render once. response=False repeats the last response's payload, as an idle render."""
        if response:
            self.cost += 0.5
        if payload is None:
            payload = {
                "session_id": self.session,
                "model": {"display_name": "Opus"},
                "cost": {"total_cost_usd": self.cost},
                "context_window": {
                    "context_window_size": 200000,
                    "total_input_tokens": int(self.cost * 1000),
                    "total_output_tokens": 1,
                },
                "rate_limits": {
                    "five_hour": {"used_percentage": five_hour, "resets_at": FIVE_HOUR_RESET},
                    "seven_day": {"used_percentage": 10, "resets_at": WEEK[reading_account]},
                },
            }
        return subprocess.run(
            [sys.executable, SCRIPT, *args],
            input=payload if isinstance(payload, str) else json.dumps(payload),
            capture_output=True,
            text=True,
            env=self.env,
            check=False,
        )

    def session_file(self):
        with open(os.path.join(self.state, "sessions", f"{self.session}.json")) as f:
            return json.load(f)

    def history(self, account):
        rows = []
        for path in glob.glob(os.path.join(self.state, "accounts", account, "history", "*.jsonl")):
            with open(path) as f:
                rows += [json.loads(line) for line in f]
        return [r["five_hour"]["used_percentage"] for r in rows if r["source"] == "payload"]


@pytest.fixture
def machine(tmp_path):
    m = Machine(tmp_path)
    m.login("acc-a")
    m.fetched("acc-a")
    return m


def test_idle_renders_write_no_history(machine):
    machine.render("acc-a", 20, response=False)
    machine.render("acc-a", 21)
    for _ in range(5):
        machine.render("acc-a", 21, response=False)
    assert machine.history("acc-a") == [21]


def test_a_response_writes_a_row_only_when_the_reading_moves(machine):
    machine.render("acc-a", 20, response=False)
    machine.render("acc-a", 21)
    machine.render("acc-a", 21)
    machine.render("acc-a", 22)
    assert machine.history("acc-a") == [21, 22]


def test_the_old_logins_reading_is_not_trusted_or_filed_after_login(machine):
    machine.render("acc-a", 20, response=False)
    machine.render("acc-a", 21)
    machine.login("acc-b")
    machine.render("acc-a", 22)  # still acc-a's reading, and acc-b not fetched yet
    assert machine.session_file()["matches_login"] is False
    machine.fetched("acc-b")
    machine.render("acc-a", 23)  # still acc-a's reading, now with acc-b's to compare against
    assert machine.session_file()["matches_login"] is False
    assert machine.history("acc-b") == []
    machine.render("acc-b", 4)
    assert machine.session_file()["matches_login"] is True
    assert machine.history("acc-b") == [4]
    assert machine.history("acc-a") == [21]


def test_the_session_file_records_responses_but_not_idle_renders(machine):
    machine.render("acc-a", 20, response=False)
    assert machine.session_file()["response_at"] is None
    machine.render("acc-a", 21)
    responded = machine.session_file()["response_at"]
    machine.render("acc-a", 21, response=False)
    assert machine.session_file()["response_at"] == responded


def test_record_only_prints_nothing_and_exits_zero(machine):
    result = machine.render("acc-a", 20, args=["--record-only"])
    assert (result.stdout, result.returncode) == ("", 0)
    assert machine.session_file()["rate_limits"]["five_hour"]["used_percentage"] == 20


def test_record_only_prints_nothing_even_when_rendering_fails(machine):
    result = machine.render("acc-a", 0, args=["--record-only"], payload="[1, 2]")
    assert (result.stdout, result.returncode) == ("", 0)
    drawn = machine.render("acc-a", 0, payload="[1, 2]")
    assert drawn.stdout.startswith("statusline error:")


# ── the usage-endpoint fetch ──────────────────────────────────────────────────


class Endpoint:
    """A local stand-in for the usage endpoint, answering with a set status."""

    def __init__(self):
        self.status, self.retry_after = 200, None
        self.body = {"limits": [{"kind": "weekly_all", "percent": 5}]}
        endpoint = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(endpoint.status)
                if endpoint.retry_after is not None:
                    self.send_header("Retry-After", endpoint.retry_after)
                self.end_headers()
                if endpoint.status == 200:
                    self.wfile.write(json.dumps(endpoint.body).encode())

            def log_message(self, *args):
                pass

        self.server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_port}/"


@pytest.fixture
def fetcher(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_QUOTA_STATE", str(tmp_path / "state"))
    sl = load()
    endpoint = Endpoint()
    monkeypatch.setattr(sl, "USAGE_ENDPOINT", endpoint.url)
    monkeypatch.setattr(sl, "access_token", lambda: "token")
    monkeypatch.setattr(sl, "claude_config", lambda: {"oauthAccount": {"accountUuid": "acc-a"}})
    lock = sl.state_dir("accounts", "acc-a", "fetch.lock")
    os.makedirs(os.path.dirname(lock))
    open(lock, "w").close()
    yield sl, endpoint, lock
    endpoint.server.shutdown()


def fetch_log(sl):
    rows = []
    for path in glob.glob(sl.state_dir("accounts", "acc-a", "fetches", "*.jsonl")):
        with open(path) as f:
            rows += [json.loads(line) for line in f]
    return [(r["status"], r.get("retry_after")) for r in rows]


def test_a_429_with_retry_after_zero_still_backs_off(fetcher):
    sl, endpoint, lock = fetcher
    endpoint.status, endpoint.retry_after = 429, "0"
    sl.fetch_usage("acc-a")
    wait = os.stat(lock).st_mtime - time.time() + sl.USAGE_FETCH_INTERVAL
    assert wait == pytest.approx(sl.USAGE_RATE_LIMIT_BACKOFF, abs=5)
    assert not os.path.exists(sl.state_dir("accounts", "acc-a", "usage.json"))
    assert fetch_log(sl) == [(429, "0")]


def test_an_answer_is_discarded_when_the_login_changed_during_the_fetch(fetcher, monkeypatch):
    sl, _, _ = fetcher
    monkeypatch.setattr(sl, "claude_config", lambda: {"oauthAccount": {"accountUuid": "other"}})
    sl.fetch_usage("acc-a")
    assert not os.path.exists(sl.state_dir("accounts", "acc-a", "usage.json"))
    assert fetch_log(sl) == [("discarded", None)]


def test_a_fetch_writes_usage_and_one_endpoint_row_per_change(fetcher):
    sl, endpoint, _ = fetcher
    sl.fetch_usage("acc-a")
    sl.fetch_usage("acc-a")
    endpoint.body = {"limits": [{"kind": "weekly_all", "percent": 6}]}
    sl.fetch_usage("acc-a")
    recorded = sl.read_json(sl.state_dir("accounts", "acc-a", "usage.json"))
    assert recorded["usage"] == endpoint.body
    rows = []
    for path in glob.glob(sl.state_dir("accounts", "acc-a", "history", "*.jsonl")):
        with open(path) as f:
            rows += [json.loads(line) for line in f]
    assert [r["limits"][0]["percent"] for r in rows] == [5, 6]
    assert fetch_log(sl) == [(200, None)] * 3


def test_reset_times_differing_by_microseconds_are_not_a_change():
    sl = load()
    before = [{"kind": "session", "percent": 23, "resets_at": "2026-10-01T00:20:00.185056+00:00"}]
    after = [{"kind": "session", "percent": 23, "resets_at": "2026-10-01T00:20:00.443728+00:00"}]
    moved = [{"kind": "session", "percent": 24, "resets_at": "2026-10-01T00:20:00.443728+00:00"}]
    assert not sl.limits_moved(before, after)
    assert sl.limits_moved(before, moved)
    assert sl.limits_moved(None, before)


def test_an_expired_token_is_not_used(monkeypatch):
    sl = load()
    blob = {"claudeAiOauth": {"accessToken": "t", "expiresAt": (time.time() - 5) * 1000}}

    class Found:
        returncode, stdout = 0, json.dumps(blob)

    monkeypatch.setattr(sl.sys, "platform", "darwin")
    monkeypatch.setattr(sl.subprocess, "run", lambda *a, **k: Found())
    assert sl.access_token() is None


def test_prune_deletes_only_what_has_aged_out(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_QUOTA_STATE", str(tmp_path / "state"))
    sl = load()
    old_session = sl.state_dir("sessions", "old.json")
    new_session = sl.state_dir("sessions", "new.json")
    old_day = sl.state_dir("accounts", "acc-a", "history", "2026-09-01.jsonl")
    for path in (old_session, new_session, old_day):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        open(path, "w").close()
    os.utime(old_session, (time.time() - 8 * 86400,) * 2)
    os.utime(old_day, (time.time() - 10 * 86400,) * 2)
    sl.prune("acc-a")
    assert [os.path.exists(p) for p in (old_session, new_session, old_day)] == [False, True, False]
