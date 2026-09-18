#!/usr/bin/env python
"""Search and read Claude Code session transcripts.

One driver with three verbs:

  history.py search [--query Q] [target] [filters] [output]
  history.py fetch  --index N [N ...] [target-single]
  history.py find   --name X | --uuid Y

Session target (search/fetch). Default for search is every session in the current
project; default for fetch is the current session:

  --name X      session named X (matches any name it ever had via /rename)
  --uuid Y      session UUID Y (a unique prefix is accepted)
  --file P      an explicit transcript path
  --project N   every session in the project whose directory name contains N
  --current     only the current session

Names resolve current-project-first, widening to all projects on a miss; the most
recently modified session wins a tie. The current session and project are located
from --session-id (the current session UUID) when given, else inferred from $PWD.

search filters: --type --tool --subtype --before N --after N --limit N
search output:  summaries (default) | --full | --count | --field PATH

A search line is prefixed with the session name and record index, so a hit can be
read in context afterward:

  [git-wright-revision:91] assistant: ...the attribution trailer...
  history.py fetch --uuid e1308f50 --index 90 91 92
"""

import argparse
import difflib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

PROJECTS_ROOT = Path(os.path.expanduser("~/.claude/projects"))

UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE)
# A UUID prefix must be >= 4 hex chars so a short query word is never a selector.
HEX_PREFIX_RE = re.compile(r"^[0-9a-f][0-9a-f-]{3,}$", re.IGNORECASE)

# How Claude Code names a project directory: every non-alphanumeric character
# becomes a dash, and a name longer than PROJECT_KEY_MAX is truncated and given
# a suffix derived from the whole original path.
PROJECT_KEY_MAX = 200
NON_ALNUM_RE = re.compile(r"[^a-zA-Z0-9]")
BASE36_DIGITS = "0123456789abcdefghijklmnopqrstuvwxyz"

TITLE_MARKER = '"type":"custom-title"'
_SCAN_BATCH = 800
WIDEN_CAP = 30  # default match cap when a search auto-widens to all projects

# Leading text marking a non-human user record (for `search --type user`).
NON_HUMAN_PREFIXES = (
    "[Request interrupted",
    "This session is being continued",
    "<teammate-message",
    "<local-command",
    "<command-name",
    "<command-message",
    "Base directory for this skill:",
)


# --- transcript parsing -----------------------------------------------------

def extract_text(content):
    """Readable text from a message.content value, summarizing non-text blocks."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if not isinstance(block, dict):
                continue
            btype = block.get("type", "")
            if btype == "text":
                parts.append(block.get("text", ""))
            elif btype == "tool_use":
                name = block.get("name", "?")
                inp = block.get("input", {})
                hint = ""
                if isinstance(inp, dict):
                    hint = (inp.get("command") or inp.get("pattern") or inp.get("query")
                            or inp.get("file_path") or inp.get("prompt") or "")
                parts.append(f"(tool_use {name}): {hint}" if hint else f"(tool_use {name})")
            elif btype == "tool_result":
                parts.append("(tool_result)")
            elif btype == "thinking":
                parts.append("(thinking)")
            else:
                parts.append(f"({btype})")
        return "\n".join(parts)
    return str(content) if content else ""


def extract_full(content):
    """Full content for `fetch`: text verbatim, other blocks tagged with ids."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if not isinstance(block, dict):
                continue
            btype = block.get("type", "")
            if btype == "text":
                parts.append(block.get("text", ""))
            elif btype == "tool_use":
                parts.append(f"(tool_use name={block.get('name', '?')} "
                             f"input={json.dumps(block.get('input', {}))[:2000]})")
            elif btype == "tool_result":
                body = block.get("content")
                parts.append(f"(tool_result id={block.get('tool_use_id', '?')}): "
                             f"{extract_text(body)}")
            elif btype == "thinking":
                parts.append(f"(thinking) {block.get('thinking', '')}")
            else:
                parts.append(f"({btype})")
        return "\n".join(parts)
    return f"(content type: {type(content).__name__})"


def get_content(record):
    return (record.get("message", {}).get("content")
            or record.get("content")
            or record.get("attachment", ""))


def get_tool_names(record):
    content = record.get("message", {}).get("content", [])
    if not isinstance(content, list):
        return []
    return [b.get("name", "") for b in content
            if isinstance(b, dict) and b.get("type") == "tool_use"]


def get_tool_input_text(record):
    content = record.get("message", {}).get("content", [])
    if not isinstance(content, list):
        return ""
    out = []
    for b in content:
        if isinstance(b, dict) and b.get("type") == "tool_use":
            inp = b.get("input", {})
            out.append(json.dumps(inp) if isinstance(inp, dict) else str(inp))
    return " ".join(out)


def get_field(record, path):
    """Extract a dotted field path, with a computed `_tool_names` shortcut."""
    if path == "_tool_names":
        return get_tool_names(record)
    cur = record
    for part in path.split("."):
        if isinstance(cur, dict):
            cur = cur.get(part)
        else:
            return None
    return cur


def is_human_user(record):
    """A human-typed user message (not a tool result, command, or continuation)."""
    if record.get("isCompactSummary") or record.get("isMeta"):
        return False
    content = record.get("message", {}).get("content")
    text = content if isinstance(content, str) else None
    if text is None and isinstance(content, list):
        texts = [b.get("text", "") for b in content
                 if isinstance(b, dict) and b.get("type") == "text"]
        text = "\n".join(t for t in texts if t) or None
    if not text:
        return False
    return not any(text.startswith(p) for p in NON_HUMAN_PREFIXES)


def assistant_or_user_text(record):
    content = record.get("message", {}).get("content")
    if isinstance(content, str):
        return content or None
    if isinstance(content, list):
        texts = [b.get("text", "") for b in content
                 if isinstance(b, dict) and b.get("type") == "text"]
        return "\n".join(t for t in texts if t) or None
    return None


def prose_text(record):
    """The conversational prose of a record, or None if it is not prose.

    Prose is a human-typed user message or an assistant text response. Tool
    calls, tool results, thinking, progress, and other technical records are not
    prose; they are reachable with --type/--tool/--subtype.
    """
    rtype = record.get("type")
    if rtype == "user":
        return assistant_or_user_text(record) if is_human_user(record) else None
    if rtype == "assistant":
        return assistant_or_user_text(record)
    return None


def record_hit(record, pattern_re, args):
    """Return the text to display if the record matches, else None.

    Default (no --type/--tool/--subtype): prose only, matched against the prose.
    With those filters: the general record matching, matched across all
    searchable text and displayed via the summarized content.
    """
    if args._prose:
        text = prose_text(record)
        if text is None:
            return None
        if pattern_re is not None and not pattern_re.search(text):
            return None
        return text
    if not matches_filters(record, args):
        return None
    if not matches_pattern(record, pattern_re):
        return None
    return extract_text(get_content(record))


def matches_filters(record, args):
    rtype = record.get("type")
    if args.type:
        if rtype != args.type:
            return False
        if args.type == "user" and not is_human_user(record):
            return False
    if args.subtype:
        sub = record.get("subtype")
        if sub is None and isinstance(record.get("attachment"), dict):
            sub = record["attachment"].get("type")
        if sub != args.subtype:
            return False
    if args.tool:
        if rtype != "assistant":
            return False
        names = get_tool_names(record)
        if args.tool == "*" and not names:
            return False
        if args.tool != "*" and args.tool not in names:
            return False
    return True


def matches_pattern(record, pattern_re):
    if pattern_re is None:
        return True
    if pattern_re.search(extract_text(get_content(record))):
        return True
    if record.get("type") == "assistant" and pattern_re.search(get_tool_input_text(record)):
        return True
    attachment = record.get("attachment")
    if isinstance(attachment, dict) and pattern_re.search(json.dumps(attachment)):
        return True
    sys_content = record.get("content")
    if isinstance(sys_content, str) and pattern_re.search(sys_content):
        return True
    return False


def is_compact_boundary(record):
    # compact_boundary only -- microcompact_boundary prunes tool outputs from
    # active context but leaves originals in the file, so it is not a search
    # boundary.
    return record.get("type") == "system" and record.get("subtype") == "compact_boundary"


def last_compact_boundary(fpath):
    last = None
    for idx, record in iter_records(fpath):
        if is_compact_boundary(record):
            last = idx
    return last


def iter_records(fpath):
    """Yield (index, record) for each parseable line."""
    try:
        f = open(fpath, errors="replace")
    except OSError:
        return
    with f:
        for idx, line in enumerate(f):
            line = line.strip()
            if not line:
                continue
            try:
                yield idx, json.loads(line)
            except json.JSONDecodeError:
                continue


def load_lines(fpath):
    try:
        return open(fpath, errors="replace").read().splitlines()
    except OSError:
        return []


# --- session chain (for --current) ------------------------------------------

def find_parent_path(fpath):
    """Clear-context pointer in the first entries, or a fork parent."""
    records = [r for _, r in zip(range(8), iter_records(fpath))]
    for _, record in records:
        text = extract_text(record.get("message", {}).get("content", ""))
        m = re.search(r"read the full transcript at: (\S+\.jsonl)", text)
        if m:
            return m.group(1)
    for _, first in records[:1]:
        forked = first.get("forkedFrom")
        if isinstance(forked, dict) and forked.get("sessionId"):
            cand = Path(fpath).parent / f"{forked['sessionId']}.jsonl"
            if cand.is_file():
                return str(cand)
    return None


def parent_chain(fpath, seen=None):
    """Ancestor transcript paths, oldest first."""
    if seen is None:
        seen = set()
    fpath = str(Path(fpath).resolve())
    if fpath in seen:
        return []
    seen.add(fpath)
    parent = find_parent_path(fpath)
    if parent and os.path.isfile(parent):
        return parent_chain(parent, seen) + [parent]
    return []


# --- session resolution -----------------------------------------------------

def _ripgrep():
    """(executable, argv0) for ripgrep, or None.

    Prefers a real `rg`; otherwise Claude Code's bundled ripgrep, reached by
    running the claude binary with argv[0]="rg" (what its own `rg` shell
    function does).
    """
    real = shutil.which("rg")
    if real:
        return real, "rg"
    for cand in (os.environ.get("CLAUDE_CODE_EXECPATH"),
                 os.path.expanduser("~/.local/bin/claude"),
                 shutil.which("claude")):
        if cand and os.path.exists(cand):
            return cand, "rg"
    return None


def _scanner():
    rg = _ripgrep()
    if rg:
        executable, argv0 = rg
        return executable, [argv0, "-F", "--no-ignore", "--no-heading",
                            "--with-filename", TITLE_MARKER]
    grep = shutil.which("grep")
    if grep:
        return grep, [grep, "-F", "-H", TITLE_MARKER]
    return None


def file_titles(fpath):
    """Every custom title in a file, in write order (last is current)."""
    titles = []
    for _, record in iter_records(fpath):
        if record.get("type") == "custom-title" and record.get("customTitle"):
            titles.append(record["customTitle"])
    return titles


def _scan_titles(files):
    """{path: [titles in order]} for files carrying a custom title, fast."""
    out = {}
    if not files:
        return out
    scanner = _scanner()
    if scanner is None:
        for f in files:
            t = file_titles(f)
            if t:
                out[f] = t
        return out
    executable, base = scanner
    for i in range(0, len(files), _SCAN_BATCH):
        chunk = files[i:i + _SCAN_BATCH]
        try:
            proc = subprocess.run(base + chunk, executable=executable,
                                  capture_output=True, text=True)
        except OSError:
            for f in chunk:
                t = file_titles(f)
                if t:
                    out[f] = t
            continue
        for line in proc.stdout.splitlines():
            path, sep, payload = line.partition(":")
            if not sep:
                continue
            try:
                obj = json.loads(payload)
            except json.JSONDecodeError:
                continue
            if obj.get("type") == "custom-title" and obj.get("customTitle"):
                out.setdefault(path, []).append(obj["customTitle"])
    return out


def _mtime(path):
    try:
        return Path(path).stat().st_mtime
    except OSError:
        return 0.0


def _session(path, titles):
    return {"uuid": Path(path).stem, "path": str(path),
            "titles": titles, "current": titles[-1] if titles else None,
            "mtime": _mtime(path)}


def resolve_uuid(selector, project_dir, scope):
    """Resolve a full UUID or unique prefix to a path (most recent on tie)."""
    sel = selector.lower()
    roots = [project_dir] if scope == "current" else [project_dir, "*"]
    seen = []
    for root in roots:
        if root == "*":
            hits = sorted(PROJECTS_ROOT.glob(f"*/{sel}*.jsonl"))
        else:
            hits = sorted(Path(root).glob(f"{sel}*.jsonl"))
        # exact full-uuid file shortcut
        exact = [h for h in hits if h.stem.lower() == sel]
        hits = exact or hits
        seen.extend(str(h) for h in hits)
        if scope == "widen" and seen:
            break
    seen = list(dict.fromkeys(seen))
    if not seen:
        return None, []
    seen.sort(key=_mtime, reverse=True)
    return seen[0], seen[1:]


def resolve_name(selector, project_dir, scope):
    """Resolve a name matching any title a session ever had (most recent on tie)."""
    sel = selector.lower()

    def match(titles_by_path):
        hits = [p for p, titles in titles_by_path.items()
                if any(t.lower() == sel for t in titles)]
        hits.sort(key=_mtime, reverse=True)
        return hits

    pools = []
    if scope in ("current", "widen"):
        files = [str(p) for p in Path(project_dir).glob("*.jsonl")]
        pools.append({f: file_titles(f) for f in files})
    if scope == "all" or scope == "widen":
        pools.append(None)  # all-projects, scanned lazily

    all_titles_cache = None
    for pool in pools:
        if pool is None:
            files = [str(p) for p in PROJECTS_ROOT.glob("*/*.jsonl")]
            pool = all_titles_cache = _scan_titles(files)
        hits = match(pool)
        if hits:
            return hits[0], hits[1:], all_titles_cache
    return None, [], all_titles_cache


def suggest(selector, titles_by_path):
    names = sorted({t for titles in titles_by_path.values() for t in titles})
    return difflib.get_close_matches(selector, names, n=5, cutoff=0.5)


def label_for(path):
    titles = file_titles(path)
    return titles[-1] if titles else Path(path).stem[:8]


# --- current session / project ----------------------------------------------

def current_session_file(args):
    if args.file:
        return args.file
    sid = getattr(args, "session_id", None) or os.environ.get("CLAUDE_SESSION_ID")
    if sid:
        hits = list(PROJECTS_ROOT.glob(f"*/{sid}.jsonl"))
        if hits:
            return str(hits[0])
    # Fall back to the most recent session in the inferred current project.
    pdir = current_project_dir(args)
    if pdir:
        files = sorted(Path(pdir).glob("*.jsonl"), key=_mtime, reverse=True)
        if files:
            return str(files[0])
    return None


def _project_key(path):
    """The directory name Claude Code stores `path`'s sessions under.

    The suffix for an over-long name is a signed 32-bit accumulator over the
    path's UTF-16 code units, absolute value, base 36.  Iterating UTF-16 rather
    than code points matters only for a path holding a character outside the
    basic plane, but getting it wrong would name a directory that does not
    exist.  None of this is a published interface.
    """
    name = NON_ALNUM_RE.sub("-", path)
    if len(name) <= PROJECT_KEY_MAX:
        return name

    units = path.encode("utf-16-le", "surrogatepass")
    accumulator = 0
    for index in range(0, len(units), 2):
        accumulator = (accumulator << 5) - accumulator + (units[index] | (units[index + 1] << 8))
        accumulator &= 0xFFFFFFFF
        if accumulator >= 0x80000000:
            accumulator -= 0x100000000

    accumulator = abs(accumulator)
    suffix = ""
    while accumulator:
        accumulator, remainder = divmod(accumulator, 36)
        suffix = BASE36_DIGITS[remainder] + suffix
    return f"{name[:PROJECT_KEY_MAX]}-{suffix or '0'}"


def current_project_dir(args):
    cf = None
    if args.file:
        cf = args.file
    else:
        sid = getattr(args, "session_id", None) or os.environ.get("CLAUDE_SESSION_ID")
        if sid:
            hits = list(PROJECTS_ROOT.glob(f"*/{sid}.jsonl"))
            if hits:
                cf = str(hits[0])
    if cf:
        return str(Path(cf).parent)
    # Fall back to $PWD encoded the way Claude Code names project dirs, walking
    # up so an invocation from a subdirectory still finds the project.  The
    # encoding has to be exact: a near-miss finds nothing here and the walk
    # then lands on a parent directory, reporting another project's sessions
    # as this one's.
    cwd = Path(os.getcwd())
    for d in [cwd, *cwd.parents]:
        cand = PROJECTS_ROOT / _project_key(str(d))
        if cand.is_dir():
            return str(cand)
    return None


def project_dir_by_name(name):
    hits = [d for d in PROJECTS_ROOT.glob("*") if d.is_dir() and name.lower() in d.name.lower()]
    hits.sort(key=lambda d: _mtime(d), reverse=True)
    return str(hits[0]) if hits else None


# --- target resolution ------------------------------------------------------

def resolve_targets(args, default):
    """Return a list of transcript paths to operate on, or exit on failure.

    `default` is "project" (all sessions in the current project) or "current".
    """
    if args.file:
        return [args.file]
    if args.uuid:
        pdir = current_project_dir(args) or str(PROJECTS_ROOT)
        path, _ = resolve_uuid(args.uuid, pdir, "widen")
        if not path:
            fail(f"No session with UUID {args.uuid!r}.", code=1)
        return [path]
    if args.name:
        pdir = current_project_dir(args) or str(PROJECTS_ROOT)
        path, alts, cache = resolve_name(args.name, pdir, "widen")
        if not path:
            tips = suggest(args.name, cache or {})
            msg = f"No session named {args.name!r}."
            if tips:
                msg += " Did you mean: " + ", ".join(tips)
            fail(msg, code=1)
        if alts:
            note(f"{args.name!r} matched {len(alts) + 1} sessions; using most recent "
                 f"({label_for(path)}).")
        return [path]
    if getattr(args, "project", None):
        pdir = project_dir_by_name(args.project)
        if not pdir:
            fail(f"No project directory matching {args.project!r}.", code=1)
        return sorted((str(p) for p in Path(pdir).glob("*.jsonl")), key=_mtime, reverse=True)
    if getattr(args, "current", False) or default == "current":
        cf = current_session_file(args)
        if not cf:
            fail("Cannot locate the current session (pass --session-id or --file).", code=2)
        return [cf]
    # default == "project": every session in the current project
    pdir = current_project_dir(args)
    if not pdir:
        fail("Cannot locate the current project (pass --session-id or --file).", code=2)
    return sorted((str(p) for p in Path(pdir).glob("*.jsonl")), key=_mtime, reverse=True)


# --- output helpers ---------------------------------------------------------

def note(msg):
    print(msg, file=sys.stderr)


def fail(msg, code=1):
    print(msg, file=sys.stderr)
    sys.exit(code)


# --- verbs ------------------------------------------------------------------

def snippet(text, pattern_re, width):
    """One-line content for a match. Full text unless --width is set, in which
    case a window of `width` chars centered on the match (so the match is never
    truncated away); the head is used when there is no query."""
    text = text.replace("\n", " ")
    if not width or width <= 0 or len(text) <= width:
        return text
    if pattern_re is not None:
        m = pattern_re.search(text)
        if m:
            start = max(0, m.start() - width // 3)
            end = min(len(text), start + width)
            return ("…" if start > 0 else "") + text[start:end] + ("…" if end < len(text) else "")
    return text[:width] + "…"


def emit_match(label, idx, record, text, pattern_re, args):
    if args.field:
        print(f"[{label}:{idx}] {json.dumps(get_field(record, args.field))}")
    elif args.full:
        print(f"=== [{label}:{idx}] ===")
        print(json.dumps(record, indent=2))
    else:
        kind = record.get("message", {}).get("role") or record.get("type", "?")
        print(f"[{label}:{idx}] {kind}: {snippet(text, pattern_re, args.width)}")


def search_file(path, pattern_re, args, boundary=None):
    label = label_for(path)
    stop = boundary
    if args.before is not None:
        stop = args.before if stop is None else min(stop, args.before)
    count = 0
    for idx, record in iter_records(path):
        if stop is not None and idx >= stop:
            break
        if args.after is not None and idx <= args.after:
            continue
        text = record_hit(record, pattern_re, args)
        if text is None:
            continue
        count += 1
        if not args.count:
            emit_match(label, idx, record, text, pattern_re, args)
        if args.limit is not None and args.remaining is not None:
            args.remaining -= 1
            if args.remaining <= 0:
                break
    return count


def files_matching(pattern, files):
    """Subset of files whose bytes contain a regex match, via ripgrep.

    Returns the files unchanged if ripgrep is unavailable or errors, so a search
    is never silently narrowed by a tool failure.
    """
    if not files:
        return []
    rg = _ripgrep()
    if rg is None:
        return files
    executable, argv0 = rg
    out = []
    for i in range(0, len(files), _SCAN_BATCH):
        chunk = files[i:i + _SCAN_BATCH]
        try:
            proc = subprocess.run([argv0, "-l", "-i", "-e", pattern, *chunk],
                                  executable=executable, capture_output=True, text=True)
        except OSError:
            return files
        if proc.returncode > 1:  # 0 = matches, 1 = none, >1 = error
            return files
        out.extend(proc.stdout.splitlines())
    return out


def all_project_files(args):
    """Sessions outside the current project, newest first, prefiltered by query."""
    cur_dir = current_project_dir(args)
    files = [str(p) for p in PROJECTS_ROOT.glob("*/*.jsonl") if str(p.parent) != cur_dir]
    if args.query:
        files = files_matching(args.query, files)
    files.sort(key=_mtime, reverse=True)
    return files


def search_paths(paths, pattern_re, args, current_file):
    """Search each path, limiting the current session to out-of-context content.

    The current session's in-context tail (including the query that invoked the
    search) is already visible to the model, so only its compacted-away content
    is searched, and it is skipped entirely if nothing was compacted.
    """
    total = 0
    for path in paths:
        if args.remaining is not None and args.remaining <= 0:
            break
        if current_file and str(Path(path).resolve()) == current_file:
            boundary = last_compact_boundary(path)
            if boundary is None:
                continue
            total += search_file(path, pattern_re, args, boundary=boundary)
        else:
            total += search_file(path, pattern_re, args)
    return total


def finish_search(total, args):
    if args.count:
        print(total)
    elif total == 0:
        note("No matches found.")


def cmd_search(args):
    pattern_re = re.compile(args.query, re.IGNORECASE) if args.query else None
    args._prose = not (args.type or args.tool or args.subtype)
    args.remaining = args.limit
    explicit = bool(args.name or args.uuid or args.file
                    or getattr(args, "project", None) or args.current)

    # Explicit current-session recovery: out-of-context content plus the chain.
    if args.current and not (args.name or args.uuid or args.file):
        cf = resolve_targets(args, default="current")[0]
        total = search_file(cf, pattern_re, args, boundary=last_compact_boundary(cf))
        for parent in parent_chain(cf):
            if args.remaining is not None and args.remaining <= 0:
                break
            total += search_file(parent, pattern_re, args)
        finish_search(total, args)
        return

    targets = resolve_targets(args, default="project")
    current_file = None
    if not explicit:
        cf = current_session_file(args)
        current_file = str(Path(cf).resolve()) if cf else None
    total = search_paths(targets, pattern_re, args, current_file)

    # A bare current-project search that finds nothing widens to every project,
    # so a forgotten session is still found without naming it.
    if total == 0 and not explicit:
        note("No matches in the current project; widening to all projects.")
        if args.limit is None:
            args.remaining = WIDEN_CAP
        total = search_paths(all_project_files(args), pattern_re, args, current_file)
        if args.limit is None and total >= WIDEN_CAP:
            note(f"Showing the first {WIDEN_CAP}; narrow the query or name a project or session.")

    finish_search(total, args)


def cmd_fetch(args):
    targets = resolve_targets(args, default="current")
    path = targets[0]
    lines = load_lines(path)
    indices = list(dict.fromkeys(args.index))
    for idx in indices:
        if idx < 0 or idx >= len(lines):
            note(f"Index {idx} out of range ({len(lines)} lines in {label_for(path)}).")
            continue
        raw = lines[idx].strip()
        if not raw:
            continue
        try:
            record = json.loads(raw)
        except json.JSONDecodeError:
            note(f"Index {idx}: invalid JSON.")
            continue
        rtype = record.get("type", "?")
        role = record.get("message", {}).get("role", "-")
        print(f"=== [{label_for(path)}:{idx}] type={rtype} role={role} ===")
        print(extract_full(get_content(record)))


def cmd_find(args):
    pdir = current_project_dir(args) or str(PROJECTS_ROOT)
    if args.uuid:
        path, alts = resolve_uuid(args.uuid, pdir, "widen")
        if not path:
            fail(f"No session with UUID {args.uuid!r}.", code=1)
    else:
        path, alts, cache = resolve_name(args.name, pdir, "widen")
        if not path:
            tips = suggest(args.name, cache or {})
            msg = f"No session named {args.name!r}."
            if tips:
                msg += " Did you mean: " + ", ".join(tips)
            fail(msg, code=1)
    if alts:
        note(f"matched {len(alts) + 1} sessions; using most recent ({label_for(path)}). "
             f"others: {', '.join(label_for(a) for a in alts)}")
    print(path)


# --- argument parsing -------------------------------------------------------

def add_target(parser, with_project=True, with_current=True):
    parser.add_argument("--session-id", help="current session UUID (else $CLAUDE_SESSION_ID)")
    parser.add_argument("--name", help="session name (any name it ever had)")
    parser.add_argument("--uuid", help="session UUID or unique prefix")
    parser.add_argument("--file", help="explicit transcript path")
    if with_project:
        parser.add_argument("--project", help="every session in the matching project dir")
    if with_current:
        parser.add_argument("--current", action="store_true",
                            help="only the current session (out-of-context content)")


def main():
    parser = argparse.ArgumentParser(
        description="Search and read Claude Code session transcripts.",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="verb", required=True)

    p_search = sub.add_parser("search", help="find matching records")
    add_target(p_search)
    p_search.add_argument("--query", help="regex pattern (case-insensitive)")
    p_search.add_argument("--type", help="record type (user, assistant, system, ...)")
    p_search.add_argument("--tool", help="tool name ('*' for any)")
    p_search.add_argument("--subtype", help="system subtype or attachment type")
    p_search.add_argument("--before", type=int, help="only records before index N")
    p_search.add_argument("--after", type=int, help="only records after index N")
    p_search.add_argument("--limit", type=int, help="cap total matches")
    p_search.add_argument("--full", action="store_true", help="full JSON records")
    p_search.add_argument("--count", action="store_true", help="print match count")
    p_search.add_argument("--field", help="extract a dotted field path")
    p_search.add_argument("--width", type=int, default=0,
                          help="truncate each match to N chars, centered on the match "
                               "(default: full content)")
    p_search.set_defaults(func=cmd_search)

    p_fetch = sub.add_parser("fetch", help="print full content of records by index")
    add_target(p_fetch, with_project=False)
    p_fetch.add_argument("--index", type=int, nargs="+", required=True,
                         help="0-based record indices")
    p_fetch.set_defaults(func=cmd_fetch)

    p_find = sub.add_parser("find", help="resolve a session to its file path")
    p_find.add_argument("--session-id", help="current session UUID (else $CLAUDE_SESSION_ID)")
    p_find.add_argument("--name", help="session name (any name it ever had)")
    p_find.add_argument("--uuid", help="session UUID or unique prefix")
    p_find.add_argument("--file", help=argparse.SUPPRESS)
    p_find.set_defaults(func=cmd_find)

    args = parser.parse_args()

    if args.verb == "find" and not (args.name or args.uuid):
        parser.error("find requires --name or --uuid")

    args.func(args)


if __name__ == "__main__":
    main()
