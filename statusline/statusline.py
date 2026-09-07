#!/usr/bin/env python

"""A Claude Code status line: context, plan quota, account, PR and model.

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
truncate at an arbitrary point. ACCOUNT_LABELS maps an email address to the name shown for it; those
two constants are the only things here meant to be edited.

Environment:

    CLAUDE_STATUSLINE_CHROME  columns the host's own chrome occupies on top of statusLine.padding.
                              Defaults to 4; raise it if the end of the line is clipped.
    CLAUDE_STATUSLINE_RULER   set to anything to emit a column ruler ending in '#', which is how
                              you measure the value above.
    CLAUDE_CONFIG_DIR         honoured when locating .claude.json for the account label.
    COLUMNS                   overrides terminal width detection.

Reads only the payload on stdin and .claude.json, the latter for the account label because the
payload does not carry one. Writes nothing, keeps no state.

Available data: https://code.claude.com/docs/en/statusline#available-data
Note that `cost` and `exceeds_200k_tokens` are present in the payload but absent from the
documented schema, so treat them as undocumented rather than guaranteed.

Contract worth remembering: Claude Code uses stdout only on a zero exit, and an empty result
blanks the line with no error shown. So this script catches everything and always prints
something -- an ugly status line beats an invisible one.
"""

import json
import os
import re
import sys
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
        return "↻now"                      # sub-minute would floor to a meaningless "0m"
    h, m = divmod(secs // 60, 60)
    if h >= 24:
        return f"↻{h // 24}d{h % 24}h"      # minutes are noise a day out
    if h:
        # Minutes are kept inside a day: dropping them made 4h50m read as 4h, and being told you
        # have four hours when you have nearly five is wrong in the direction that matters.
        return f"↻{h}h{m:02d}m" if m else f"↻{h}h"
    return f"↻{m}m"


def tokens(n):
    """Compact token count, e.g. 735K or 1M."""
    if n < 1_000_000:
        return f"{round(n / 1000)}K"
    m = n / 1_000_000
    return f"{m:.1f}M" if m % 1 else f"{m:.0f}M"


ACCOUNT_LABELS = {
    # Map your own names here; otherwise the org name or "Personal" is used.
}


def claude_config():
    """Parsed .claude.json for this config dir, or {} when it cannot be read.

    Re-read every render rather than cached: the file is around half a megabyte and parses in a few
    milliseconds against roughly 25 for the whole line, so a cache would buy a fraction of one
    render while adding a staleness window in which an account switch shows the wrong name -- the
    one moment the label matters. Parsed once and handed to both readers below, since parsing it
    twice would double the only measurable cost this script has.
    """
    path = os.path.join(os.environ.get("CLAUDE_CONFIG_DIR", HOME), ".claude.json")
    if not os.path.exists(path):
        path = os.path.join(HOME, ".claude.json")
    return read_json(path)


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


OUTPUT_RESERVE = 20000    # min(max_output_tokens, 20000) is held back for the reply
COMPACT_FLOOR = 13000     # auto-compaction fires this far below the usable window by default


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


DEFAULT_PADDING = 0       # what the host applies when statusLine.padding is unset


def status_padding():
    """Columns Claude Code indents the rendered line by, from statusLine.padding.

    Padding is applied by the host after this script returns, so justifying to the full terminal
    width overflows by that much and the tail gets clipped. Whether the host pads one side or both
    is not evident from the bundle, so this budgets for both -- overshooting costs two columns,
    undershooting costs the end of the line.
    """
    cfg = os.path.join(os.environ.get("CLAUDE_CONFIG_DIR", os.path.join(HOME, ".claude")),
                       "settings.json")
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
SEPARATORS = (" · ", " · ", " | ")

# Drop order when the line will not fit: the first name goes first.
DROP_ORDER = ("dirs", "think", "effort", "pr", "7d", "account", "5h", "ctx", "model")


def build(data):
    """Return [(priority, section, text)], priority being the item's place in DROP_ORDER."""
    g = data.get
    cfg = claude_config()
    out = []

    def add(name, section, text):
        if text:
            out.append((DROP_ORDER.index(name), section, text))

    # ── Left: gauges ──────────────────────────────────────────────────────────
    cw = g("context_window") or {}
    size = cw.get("context_window_size") or 0
    # Against the auto-compaction threshold, so 100% is the moment the session gets compacted, not
    # the moment the window is full. Falls back to the payload's own figure when the tokens are
    # missing, which is the case before the first API response.
    used = cw.get("total_input_tokens") or 0
    thresh, configured = compact_threshold(size)
    ctx = (round(used / thresh * 100) if used and thresh > 0
           else int(cw.get("used_percentage") or 0))
    # The percent is against the threshold, so the threshold is the denominator shown -- a percent
    # of a number the reader cannot see is not checkable. The window follows it because the pair is
    # what says how much of the model has been given away, and AC marks a threshold the environment
    # set rather than the built-in floor.
    scale = (f" {'AC ' if configured else ''}{tokens(thresh)}/{tokens(size)}"
             if thresh and size else "")
    add("ctx", LEFT, f"ctx {ctx}%{scale}")

    limits = g("rate_limits") or {}
    for key, short in (("five_hour", "5h"), ("seven_day", "7d")):
        w = limits.get(key)
        if not w:
            continue
        pct = int(w.get("used_percentage") or 0)
        left = until(w.get("resets_at"))
        add(short, LEFT, f"{short} {pct}% {left}" if left else f"{short} {pct}%")

    # ── Centre: where ─────────────────────────────────────────────────────────
    pr = g("pr") or {}
    if pr.get("number"):
        state = {"approved": "✓", "changes_requested": "✗",
                 "pending": "…", "draft": "◌"}.get(pr.get("review_state"), "")
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
    return [sep.join(t for _, sec, t in items if sec == s)
            for s, sep in enumerate(SEPARATORS)]


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
        return ""                          # no payload: nothing sensible to draw
    width = render_width()
    if os.environ.get("CLAUDE_STATUSLINE_RULER"):
        # Every tenth column is marked; the final column is '#'. Whether '#' survives tells you
        # exactly how many columns the host chrome is really taking.
        return "".join(str(c // 10 % 10) if c % 10 == 0 else "."
                       for c in range(1, width)) + "#"
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
