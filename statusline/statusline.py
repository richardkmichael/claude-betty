#!/usr/bin/env python

"""A Claude Code status line: context, plan and per-model quota, account, PR and model.

Point settings.json at it:

    "statusLine": {
      "type": "command",
      "command": "~/.claude/statusline.py",
      "padding": 2
    }

Vim mode is deliberately not rendered here. Claude Code shows it on its own line beneath the status
line, which costs no width -- so leave hideVimModeIndicator unset. The PR badge is the opposite
case: this script draws it, so `prStatusFooterEnabled: false` turns off the duplicate in Claude
Code's footer. To combine with another producer, pipe that in: `producer | ~/.claude/statusline.py`.

The PR badge appears only when the branch has an open pull request and the GitHub CLI is
authenticated; the payload omits the block entirely otherwise, so a missing badge is not an error.

Three sections -- gauges left, workspace centre, model right. When the line will not fit, items are
dropped one at a time in DROP_ORDER, because choosing what to lose beats letting the terminal
truncate at an arbitrary point. Items are divided by " | " throughout, except that gauges reporting
one window take " · " between them and share a single reset marker, which is how the seven-day plan
gauge and the per-model weekly gauges beside it are drawn. ACCOUNT_LABELS maps an email address to
the name shown for it; those two constants are the only things here meant to be edited.

Environment:

    CLAUDE_STATUSLINE_CHROME  columns the host's own chrome occupies on top of statusLine.padding.
                              Defaults to 4; raise it if the end of the line is clipped.
    CLAUDE_STATUSLINE_RULER   set to anything to emit a column ruler ending in '#', which is how
                              you measure the value above.
    CLAUDE_CONFIG_DIR         honoured when locating .claude.json for the account label and the
                              per-model quota windows.
    COLUMNS                   overrides terminal width detection.

Reads the payload on stdin and .claude.json. That file supplies the account label, which the
payload does not carry, and Claude Code's cached copy of the usage endpoint, which is where the
per-model quota windows live -- the payload's rate_limits covers the plan's own five-hour and
seven-day windows and nothing else.

Nothing in Claude Code refreshes those cached figures on a schedule. They are written only when
`/usage` runs, and `/login` clears them outright as part of logging the old account out. So when
they age out, go missing or turn out to belong to another account, this script starts
`claude -p /usage` in the background to replace them, which is the one thing here that reaches
beyond reading a file: it touches the network indirectly, and it keeps a lock file in the temp
directory to stop every render of every open session spawning its own. A cache that is missing is
refreshed only when the payload carries rate_limits, which is what says the account has plan limits
worth fetching. Both the spawn and the lock are confined to refresh_usage below. A figure too old
to trust is still shown, reading STL in place of the percentage, rather than hidden.

Available data: https://code.claude.com/docs/en/statusline#available-data
Note that `cost` and `exceeds_200k_tokens` are present in the payload but absent from the
documented schema, so treat them as undocumented rather than guaranteed.

Contract worth remembering: Claude Code uses stdout only on a zero exit, and an empty result
blanks the line with no error shown. So this script catches everything and always prints
something -- an ugly status line beats an invisible one.
"""

import datetime
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

# ── helpers ───────────────────────────────────────────────────────────────────

HOME = os.path.expanduser("~")

ANSI = re.compile(r"\x1b\](?:.*?)(?:\x1b\\|\x07)|\x1b\[[0-9;]*[A-Za-z]")


def visible_len(s):
    """Width as rendered: escape sequences occupy no columns."""
    return len(ANSI.sub("", s))


def link(text, url):
    """OSC 8 hyperlink. Terminals that do not support it just show the text."""
    if not url:
        return text
    return f"\x1b]8;;{url}\x1b\\{text}\x1b]8;;\x1b\\"


def env_num(name, default, cast=int):
    """Numeric environment variable, or the default when unset, blank or unparseable."""
    try:
        return cast(os.environ.get(name) or default)
    except ValueError:
        return default


def read_json(path):
    """Parsed JSON object at path, or {} when it is missing, unreadable or not an object."""
    try:
        with open(path) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def until(epoch):
    """Compact time-to-reset, e.g. ↻3h or ↻2d4h.

    The glyph is not decoration: a bare "1m" next to "61%" reads as one million. It also marks the
    token as belonging to the window beside it rather than being a third independent number.
    """
    if not epoch:
        return ""
    secs = int(epoch - time.time())
    if secs < 60:
        return "↻now"  # sub-minute would floor to a meaningless "0m"
    h, m = divmod(secs // 60, 60)
    if h >= 24:
        return f"↻{h // 24}d{h % 24}h"  # minutes are noise a day out
    if h:
        # Minutes are kept inside a day: dropping them made 4h50m read as 4h, and being told you
        # have four hours when you have nearly five is wrong in the direction that matters.
        return f"↻{h}h{m:02d}m" if m else f"↻{h}h"
    return f"↻{m}m"


def iso_epoch(s):
    """Epoch seconds for an ISO 8601 timestamp, or 0 when it is absent or unparseable.

    The plan windows in the payload carry epoch integers, but the usage endpoint answers in ISO
    8601, so the two have to be brought to the same units before until() can format either.
    """
    if not isinstance(s, str):
        return 0
    try:
        return datetime.datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0


def tokens(n):
    """Compact token count, e.g. 735K or 1M."""
    if n < 1_000_000:
        return f"{round(n / 1000)}K"
    m = n / 1_000_000
    return f"{m:.1f}M" if m % 1 else f"{m:.0f}M"


ACCOUNT_LABELS = {
    # Map your own names here; otherwise the org name or "Personal" is used.
}


def config_dir():
    """Directory Claude Code keeps its state in; the home directory by default.

    Named separately from the file below because the refresh lock is keyed by the directory rather
    than by the file. The directory does not identify the account on its own, since one directory
    serves whichever account is logged in, so the account uuid is keyed alongside it.
    """
    return os.environ.get("CLAUDE_CONFIG_DIR") or HOME


def claude_config():
    """Parsed .claude.json for this config dir, or {} when it cannot be read.

    Re-read every render rather than cached: the file is around half a megabyte and parses in a few
    milliseconds against roughly 25 for the whole line, so a cache would buy a fraction of one
    render while adding a staleness window in which an account switch shows the wrong name -- the
    one moment the label matters. Parsed once and handed to both readers below, since parsing it
    twice would double the only measurable cost this script has.
    """
    path = os.path.join(config_dir(), ".claude.json")
    if not os.path.exists(path):
        path = os.path.join(HOME, ".claude.json")
    return read_json(path)


def account_uuid(cfg):
    """Claude Code's own identifier for the account this config dir is authenticated as, or None.

    The uuid rather than the email address, because it is what the cached usage figures are stamped
    with, so it is the value both the cache check and the lock key below have to compare against.
    """
    return (cfg.get("oauthAccount") or {}).get("accountUuid")


def claude_account(cfg):
    """Which account this config dir is authenticated as; the payload does not say.

    The identity lives in plaintext in the config JSON, the credential itself being in the Keychain.
    """
    a = cfg.get("oauthAccount") or {}
    email, org = a.get("emailAddress", ""), a.get("organizationName", "")
    label = ACCOUNT_LABELS.get(email)
    if label:
        return label
    if a.get("organizationType") in ("claude_team", "claude_enterprise"):
        return org
    if org.endswith("'s Organization"):
        return "Personal"
    return email.split("@")[0]


REFRESH_AFTER = 360  # spawn a refresh once the cached figures are older than this
STALE_MARK = 900  # past this the refresh has plainly failed, so mark the figure


def usage_cache(cfg):
    """(cached usage figures, age in seconds), or ({}, inf) when they cannot be trusted at all.

    Claude Code polls the usage endpoint and leaves the answer in .claude.json under
    cachedUsageUtilization, which is what makes the per-model gauge affordable: the figures are
    already on disk in a file that is being parsed anyway.

    The one guard applied here is the account. A percentage belonging to someone else's quota is
    the number that must never appear beside the account label, so a cache written under a
    different login is discarded outright rather than shown. Age is returned rather than judged,
    because how old is too old differs between the caller that renders a figure and the caller that
    decides whether to fetch a new one.

    A cache that is absent or discarded is reported as infinitely old, which is the literal truth:
    there is no reading here of any age. That answer also serves both callers without a special
    case for it. Nothing is drawn, because there are no windows to draw from, and a refresh is owed,
    because the oldest possible reading is older than any threshold that could be set against it.
    """
    cache = cfg.get("cachedUsageUtilization") or {}
    if not cache or cache.get("accountUuid") != account_uuid(cfg):
        return {}, math.inf
    age = time.time() - (cache.get("fetchedAtMs") or 0) / 1000
    return cache, max(0, age)


def refresh_usage(age, has_limits, account):
    """Start a detached `claude -p /usage` when the cached figures need replacing.

    Nothing refreshes this cache on its own. Claude Code writes it from exactly one place, reached
    only by `/usage` or an SDK request, and `/login` clears it as part of logging the old account
    out, so left alone the figures are stale almost always and fresh for an hour after a command
    the user rarely runs. `/usage` in its non-interactive form fixes that: it costs no tokens and
    starts no session, because it reads the usage endpoint and local transcripts rather than
    calling a model.

    The spawn is detached with its streams closed, so a render never waits on it and a failure --
    offline, expired credentials -- costs nothing but the stale figure already on screen.

    A lock file rate-limits the whole machine rather than this render. The line is drawn several
    times a minute in every open session, so without one each of them would spawn its own refresh.
    Claiming it with O_CREAT|O_EXCL makes the winner unambiguous when several renders race, and
    nothing ever deletes it: its age is the record of when a refresh was last attempted, which is
    what makes a failing refresh retry on the same slow cadence as a working one.

    The lock is keyed by config directory and account together, so an account switched to partway
    through a session gets its first refresh straight away rather than waiting out the lock taken
    for the account before it. A config carrying no account uuid keys on the directory alone.

    has_limits reports whether the payload carried rate_limits, and it is the whole test for
    whether a fetch is worth making: the block is present only on a subscription session, so an API
    key, Bedrock or Vertex session has no plan limits to read and no cache age gets it here. Given
    a subscription session, a cache that is missing or belongs to another account is as good a
    reason to fetch as one that has aged out, which is what fills the gauge after `/login` and on a
    machine that has never run `/usage` at all.
    """
    if not has_limits or age <= REFRESH_AFTER:
        return
    claude = shutil.which("claude") or os.path.join(HOME, ".local/bin/claude")
    if not os.path.exists(claude):
        return
    identity = f"{config_dir()}\0{account}" if account else config_dir()
    key = hashlib.sha256(identity.encode()).hexdigest()[:12]
    lock = os.path.join(tempfile.gettempdir(), f"claude-statusline-usage-{key}.lock")
    try:
        if time.time() - os.stat(lock).st_mtime <= REFRESH_AFTER:
            return  # a refresh was attempted recently enough
        os.unlink(lock)
    except OSError:
        pass  # absent, or already taken by a racing render
    try:
        os.close(os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600))
    except OSError:
        return  # another render claimed it first
    try:
        subprocess.Popen(
            [claude, "-p", "/usage"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            close_fds=True,
        )
    except OSError:
        pass


def model_windows(cache):
    """[(model name, percent used, epoch it resets)] for each per-model quota window.

    Some plans meter a model against its own allowance as well as the plan's. That allowance runs
    out well before the seven-day plan window does, so the plan gauge alone gives a reassuring
    number right up to the point the model stops answering. The payload's rate_limits carries only
    the five-hour and seven-day plan windows, and no per-model figure reaches this script any other
    way.

    A window whose reset has passed is dropped. Age alone is not grounds for that: a weekly figure
    moves slowly, so an old reading is roughly right and worth showing marked. A reading from a
    window that has since rolled over is not old but wrong, and the percentage it reports belongs
    to a week that has already ended.

    Windows are returned least-used first. Every one of them shares a place in DROP_ORDER, so on a
    narrow terminal the list is trimmed from the front, and the window closest to running out is
    the last to go. That ordering is also what lets the shared reset marker sit on the last window
    of the run below: the window carrying it is the last one dropped.

    A weekly window resets at the same instant as the plan's own seven-day window, to well under a
    second, so the caller draws the pair as one gauge rather than repeating the reset.
    """
    # The schema does not stop a model appearing under more than one window. Only the tightest is
    # worth a slot on the line, and its reset time is what says which window that slot is showing.
    worst = {}
    for limit in (cache.get("utilization") or {}).get("limits") or []:
        name = (((limit.get("scope") or {}).get("model") or {}).get("display_name") or "").strip()
        pct, reset = limit.get("percent"), iso_epoch(limit.get("resets_at"))
        if not name or not isinstance(pct, (int, float)) or 0 < reset <= time.time():
            continue
        if name not in worst or pct > worst[name][0]:
            worst[name] = (pct, reset)
    return sorted(((n, p, r) for n, (p, r) in worst.items()), key=lambda w: w[1])


OUTPUT_RESERVE = 20000  # min(max_output_tokens, 20000) is held back for the reply
COMPACT_FLOOR = 13000  # auto-compaction fires this far below the usable window by default


def compact_threshold(size):
    """(tokens at which auto-compaction fires, whether the env override set that figure).

        usable    = context_window_size - min(max_output_tokens, 20000)
        threshold = min(floor(usable * pct/100), usable - 13000)   # pct set
                  = usable - 13000                                 # pct absent

    Context is reported against this rather than the raw window because the raw figure answers a
    question nobody has. What matters is how close the session is to being compacted, and that
    happens well before the window fills. Both constants are read out of the bundle, so they are
    worth re-checking after an upgrade.

    The second element is true only when the override is the binding constraint, not merely set: at
    a high enough percentage the built-in floor is the lower of the two and the override changes
    nothing, so labelling the threshold as configured would be wrong.
    """
    if not size:
        return 0, False
    max_out = env_num("CLAUDE_CODE_MAX_OUTPUT_TOKENS", OUTPUT_RESERVE)
    usable = size - min(max_out, OUTPUT_RESERVE)
    built_in = usable - COMPACT_FLOOR
    pct = env_num("CLAUDE_AUTOCOMPACT_PCT_OVERRIDE", 0, float)
    threshold = min(int(usable * pct / 100), built_in) if 0 < pct <= 100 else built_in
    return max(0, threshold), threshold < built_in


def terminal_width():
    """Columns of the controlling terminal, or 0 if it cannot be determined.

    shutil.get_terminal_size() inspects stdout, which Claude Code always hands us as a pipe, so it
    reports nothing useful here. The controlling terminal has to be asked directly. Returning 0 is
    a safe answer: the layout then skips justification rather than padding to a guessed width.
    """
    cols = env_num("COLUMNS", 0)
    if cols > 0:
        return cols
    try:
        with open("/dev/tty") as tty:
            return os.get_terminal_size(tty.fileno()).columns
    except Exception:
        return 0


DEFAULT_PADDING = 0  # what the host applies when statusLine.padding is unset


def status_padding():
    """Columns Claude Code indents the rendered line by, from statusLine.padding.

    Padding is applied by the host after this script returns, so justifying to the full terminal
    width overflows by that much and the tail gets clipped. Whether the host pads one side or both
    is not evident from the bundle, so this budgets for both -- overshooting costs two columns,
    undershooting costs the end of the line.
    """
    cfg = os.path.join(
        os.environ.get("CLAUDE_CONFIG_DIR", os.path.join(HOME, ".claude")), "settings.json"
    )
    try:
        pad = (read_json(cfg).get("statusLine") or {}).get("padding")
        return max(0, int(pad)) if pad is not None else DEFAULT_PADDING
    except Exception:
        return DEFAULT_PADDING


def render_width():
    """Columns this script may actually fill.

    Two separate insets eat into the terminal width. statusLine.padding is applied by the host as
    paddingX, costing twice its value; ancestors in the layout add more, which is not discoverable
    from the payload -- it carries no width and no padding -- so it is measured instead. Overshoot
    by even one column and the host, which renders with wrap="truncate", eats the tail of the line.

    To re-measure: CLAUDE_STATUSLINE_RULER=1 emits a column ruler ending in '#'. If the '#' shows,
    the budget is right; if it is clipped, raise CLAUDE_STATUSLINE_CHROME by the missing count.
    """
    w = terminal_width()
    if w <= 0:
        return 0
    chrome = env_num("CLAUDE_STATUSLINE_CHROME", 4)
    return max(20, w - 2 * status_padding() - max(0, chrome))


# ── layout ────────────────────────────────────────────────────────────────────

LEFT, CENTRE, RIGHT = 0, 1, 2

# One separator per section. They read alike today, and the reader is better served by a line that
# divides every item the same way than by a section boundary drawn in punctuation.
SEPARATORS = (" | ", " | ", " | ")

# Two gauges reporting one window are joined with this instead of their section's separator. The bar
# divides independent items the whole line over, so a different mark is what says these two are not
# independent, and that the reset marker written once at the end of the run covers all of them. It
# is three columns wide, the same as the separator, so joining costs the layout nothing.
SHARED_JOIN = " · "
WEEKLY = "weekly"  # group tag for the gauges the plan's seven-day reset is shared across
SAME_RESET = 60  # seconds apart within which two windows reset at the same instant

# Drop order when the line will not fit: the first name goes first.
DROP_ORDER = ("dirs", "think", "effort", "pr", "7d", "model_quota", "account", "5h", "ctx", "model")


def build(data):
    """Return [(priority, section, text, group)], priority being the item's place in DROP_ORDER.

    The group is None for an item that stands on its own, and a tag shared with the neighbours it
    reports one window alongside.
    """
    g = data.get
    cfg = claude_config()
    out = []

    def add(name, section, text, group=None):
        if text:
            out.append((DROP_ORDER.index(name), section, text, group))

    # ── Left: gauges ──────────────────────────────────────────────────────────
    cw = g("context_window") or {}
    size = cw.get("context_window_size") or 0
    # Against the auto-compaction threshold, so 100% is the moment the session gets compacted, not
    # the moment the window is full. Falls back to the payload's own figure when the tokens are
    # missing, which is the case before the first API response.
    used = cw.get("total_input_tokens") or 0
    thresh, configured = compact_threshold(size)
    ctx = round(used / thresh * 100) if used and thresh > 0 else int(cw.get("used_percentage") or 0)
    # The percent is against the threshold, so the threshold is the denominator shown -- a percent
    # of a number the reader cannot see is not checkable. The window follows it because the pair is
    # what says how much of the model has been given away, and AC marks a threshold the environment
    # set rather than the built-in floor.
    scale = (
        f" {'AC ' if configured else ''}{tokens(thresh)}/{tokens(size)}" if thresh and size else ""
    )
    add("ctx", LEFT, f"ctx {ctx}%{scale}")

    # The per-model weekly windows reset at the same instant as the plan's seven-day window, so the
    # gauges beside each other were reporting one reset twice. The matching windows are joined to
    # the 7d gauge and the marker is written once, at the end of the run. Only a run starting at the
    # first window can be joined: a window on some other cadence sits between the gauges as an
    # ordinary item, and a marker past it would no longer read as the 7d gauge's reset.
    cache, age = usage_cache(cfg)
    windows = model_windows(cache)
    limits = g("rate_limits") or {}
    weekly_reset = (limits.get("seven_day") or {}).get("resets_at") or 0
    shared = 0
    for _, _, reset in windows:
        if not weekly_reset or not reset or abs(reset - weekly_reset) > SAME_RESET:
            break
        shared += 1

    for key, short in (("five_hour", "5h"), ("seven_day", "7d")):
        w = limits.get(key)
        if not w:
            continue
        pct = int(w.get("used_percentage") or 0)
        # The 7d gauge gives up its marker to the run it is joined to. Nothing is lost when the line
        # narrows, because DROP_ORDER takes 7d before any model gauge: the run outlives it.
        joined = short == "7d" and shared > 0
        left = "" if joined else until(w.get("resets_at"))
        add(
            short,
            LEFT,
            f"{short} {pct}% {left}" if left else f"{short} {pct}%",
            WEEKLY if joined else None,
        )

    # Lower-cased to read as a gauge alongside 5h and 7d rather than as a second model name; the
    # one on the right is what is answering, this is what it is spending.
    #
    # STL replaces the reading rather than qualifying it. A figure this old is one the refresh
    # below should already have replaced, so the honest report is that the number is unknown
    # rather than a number carrying a warning, which still invites being read. It is the width of
    # the percentage it stands in for, so the line does not shift as it comes and goes.
    stale = age > STALE_MARK
    for i, (name, pct, reset) in enumerate(windows):
        gauge = f"{name.lower()} {'STL' if stale else str(int(pct)) + '%'}"
        # A window joined to the 7d gauge shows no marker until the end of the run, where the reset
        # is taken from the payload rather than the cache -- the payload arrives fresh every render,
        # so the marker stays right even when the percentage beside it has gone stale.
        joined = i < shared
        if joined:
            left = until(weekly_reset) if i == shared - 1 else ""
        else:
            left = until(reset)
        add("model_quota", LEFT, f"{gauge} {left}" if left else gauge, WEEKLY if joined else None)
    # The payload's rate_limits is the test for whether this account has plan limits at all, so it
    # is what says a refresh is worth spawning, whatever state the cache is in.
    refresh_usage(age, bool(limits), account_uuid(cfg))

    # ── Centre: where ─────────────────────────────────────────────────────────
    pr = g("pr") or {}
    if pr.get("number"):
        state = {"approved": "✓", "changes_requested": "✗", "pending": "…", "draft": "◌"}.get(
            pr.get("review_state"), ""
        )
        label = f"{state} PR {pr['number']}" if state else f"PR {pr['number']}"
        add("pr", CENTRE, link(label, pr.get("url")))

    extra = len((g("workspace") or {}).get("added_dirs") or [])
    add("dirs", CENTRE, f"+{extra}dir" if extra else "")

    # ── Right: what is answering, and on whose quota ──────────────────────────
    # The payload never says which account is authenticated, and mistaking one for the other means
    # spending the wrong quota -- so this outranks the repo and PR badges.
    add("account", RIGHT, claude_account(cfg))

    add("model", RIGHT, (g("model") or {}).get("display_name"))
    add("effort", RIGHT, (g("effort") or {}).get("level"))
    add("think", RIGHT, "think" if (g("thinking") or {}).get("enabled") else "")
    return out


def assemble(items):
    """Each section's text, its items joined by its separator.

    Two neighbours tagged with the same group take SHARED_JOIN instead, which is why the tag is
    consulted here rather than baked into the text: an item whose partner has been dropped is no
    longer beside it, and falls back to the ordinary separator on its own.
    """
    sections = []
    for s, sep in enumerate(SEPARATORS):
        text, previous = "", None
        for _, sec, item, group in items:
            if sec != s:
                continue
            if text:
                text += SHARED_JOIN if group and group == previous else sep
            text += item
            previous = group
        sections.append(text)
    return sections


def justify(left, centre, right, width):
    """Left/centre/right across the terminal, degrading gracefully when it will not fit."""
    plain = "  ".join(p for p in (left, centre, right) if p)
    if width <= 0 or visible_len(plain) >= width:
        return plain
    left_w, centre_w, right_w = visible_len(left), visible_len(centre), visible_len(right)
    if not centre:
        return left + " " * max(1, width - left_w - right_w) + right
    lead = max(1, (width - centre_w) // 2 - left_w)
    trail = max(1, width - left_w - lead - centre_w - right_w)
    return left + " " * lead + centre + " " * trail + right


def fit(data, width):
    """Drop the least important items until it fits.

    A status line that overflows wraps or truncates at an arbitrary point, so choosing what to lose
    beats letting the terminal choose.
    """
    items = build(data)
    while items:
        line = justify(*assemble(items), width)
        if width <= 0 or visible_len(line) <= width:
            return line
        items.remove(min(items, key=lambda i: i[0]))
    return ""


def main():
    try:
        data = json.load(sys.stdin)
    except Exception:
        return ""  # no payload: nothing sensible to draw
    width = render_width()
    if os.environ.get("CLAUDE_STATUSLINE_RULER"):
        # Every tenth column is marked; the final column is '#'. Whether '#' survives tells you
        # exactly how many columns the host chrome is really taking.
        return "".join(str(c // 10 % 10) if c % 10 == 0 else "." for c in range(1, width)) + "#"
    return fit(data, width)


if __name__ == "__main__":
    try:
        line = main()
    except Exception as exc:
        # Never blank the line over a rendering bug; show enough to debug it.
        line = f"statusline error: {type(exc).__name__}: {exc}"[:200]
    if line:
        print(line)
    sys.exit(0)
