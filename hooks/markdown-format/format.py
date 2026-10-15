#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13"
# dependencies = ["mdformat>=1.0,<2", "mdformat-gfm>=1.0"]
# ///
"""Format Markdown files after Claude changes them, and lint Claude Code frontmatter.

As a hook, it reads the hook event from stdin:

- PostToolUse on Edit and Write: formats the edited file.
- PreToolUse on Bash: saves the content of each Markdown file the command names.
- PostToolUse on Bash: formats each saved file whose content the command changed.

Only the top-level blocks (paragraphs, tables, headings, and each item of a list) that changed are
formatted, so a small edit to an unformatted file does not reformat the whole file.  A new file is
formatted whole.
Given paths instead of a hook event, it formats those files whole, e.g. to bring an existing
repository into line once.

The body is formatted by mdformat: wrapped at WRAP_WIDTH, ordered lists numbered 1., 2., 3.,
tables aligned, bold removed and existing double spaces after sentences kept.  Frontmatter that
changed is formatted and linted by claude-yaml.  A file is written only if the result renders the
same HTML as before, apart from removed bold.  Lint problems are returned to Claude as additional
context.

Besides the dependencies above, this needs the mdformat-no-bold, mdformat-sentence-spacing and
claude-yaml packages, passed with `uv run --with`.  See README.md.
"""

from __future__ import annotations

import json
import re
import sys
import tempfile
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import ClassVar

import mdformat
import mdformat.plugins
from claude_yaml import Diagnostic, Document, check_frontmatter
from markdown_it import MarkdownIt
from mdformat.renderer import RenderContext, RenderTreeNode
from mdformat.renderer._util import get_list_marker_type, is_tight_list

WRAP_WIDTH = 100
# Each mdformat plugin enabled, with what it guarantees when the hook reports a formatted file.
PLUGIN_RULES = {
    "plain_numbers": "lists numbered",
    "gfm": "tables aligned",
    "no_bold": "bold removed",
    "sentence_spacing": None,  # keeps the author's spacing, so nothing to report
}
EXTENSIONS = set(PLUGIN_RULES)
MDFORMAT_OPTIONS = {"wrap": WRAP_WIDTH}
FORMATTED_NOTE = ", ".join(
    [f"wrapped at {WRAP_WIDTH} columns", *(rule for rule in PLUGIN_RULES.values() if rule)]
)
MARKDOWN_SUFFIXES = {".md", ".markdown"}
# Claude Code's auto-memory index keeps one entry per line and loads only its first 200 lines, so
# wrapping it would push entries out of every session's context.
MEMORY_INDEX = ("memory", "MEMORY.md")
HOOK_NAME = "markdown-format"

# Bash support: files named in the command are saved here between the PreToolUse and PostToolUse
# events, keyed by tool_use_id.
SNAPSHOT_DIR = Path(tempfile.gettempdir()) / "claude-markdown-format"
SNAPSHOT_MAX_AGE_SECONDS = 24 * 60 * 60
MAX_CANDIDATES = 50
MAX_FILE_BYTES = 2_000_000
LEADING_CD = re.compile(r"""\s*cd\s+(?:'([^']*)'|"([^"]*)"|([^\s;&|]+))\s*(?:&&|;|\n)""")
PATH_CHARS = r"[~\w./@+-]"
MARKDOWN_PATH = re.compile(
    rf"(?<!{PATH_CHARS})({PATH_CHARS}*[\w@+-]\.(?:md|markdown))(?![\w.-])", re.IGNORECASE
)
MARKDOWN_GLOB = re.compile(
    r"(?<![\w./~@+*-])([~\w./@+*-]*\*[~\w./@+*-]*\.(?:md|markdown))(?![\w.-])", re.IGNORECASE
)


def _ordered_list(node: RenderTreeNode, context: RenderContext) -> str:
    """An ordered list numbered 1., 2., 3., renumbering any gaps.

    mdformat pads the numbers with zeros so that every marker in a list has one width ("01." to
    "10.").  Here each item is indented to the width of its own marker instead.
    """
    marker_type = get_list_marker_type(node)
    separator = "\n" if is_tight_list(node) else "\n\n"
    start = node.attrs.get("start")
    if start is None:
        start = 1
    items = []
    for index, item in enumerate(node.children):
        marker = f"{start + index}{marker_type}"
        indent = " " * (len(marker) + 1)
        with context.indented(len(indent)):
            first, *rest = item.render(context).split("\n")
        lines = [f"{marker} {first}" if first else marker]
        lines.extend(indent + line if line else "" for line in rest)
        items.append("\n".join(lines))
    return separator.join(items)


class _PlainNumbers:
    """An mdformat plugin replacing the ordered list renderer, registered as "plain_numbers"."""

    RENDERERS: ClassVar = {"ordered_list": _ordered_list}

    @staticmethod
    def update_mdit(mdit: MarkdownIt) -> None:
        pass


mdformat.plugins.PARSER_EXTENSIONS["plain_numbers"] = _PlainNumbers


@dataclass
class _Range:
    """Lines [start, end) of a top-level block or list item, without trailing blank lines."""

    start: int
    end: int
    marker: str = ""  # a list item's marker: "-", "*", "+", or a number with "." or ")"
    items: list[_Range] = field(default_factory=list)  # a list's items

    def text(self, lines: list[str]) -> str:
        return "".join(lines[self.start : self.end])


@dataclass
class Outcome:
    path: Path
    changed: bool = False
    kind: str | None = None
    diagnostics: list[Diagnostic] = field(default_factory=list)
    skipped_reason: str | None = None


def _parser() -> MarkdownIt:
    return MarkdownIt("commonmark").enable(["table", "strikethrough"])


def _rendered_html(text: str) -> str:
    """Render to HTML with whitespace normalized, ignoring bold, which the hook may remove."""
    parser = _parser()
    tokens = parser.parse(text)
    for token in tokens:
        if token.children:
            token.children = [
                c for c in token.children if c.type not in ("strong_open", "strong_close")
            ]
    html = parser.renderer.render(tokens, parser.options, {})
    return " ".join(html.split())


def format_body(body: str) -> str:
    return mdformat.text(body, extensions=EXTENSIONS, options=MDFORMAT_OPTIONS)


def _blocks(lines: list[str]) -> list[_Range]:
    """The top-level blocks, with the items of each top-level list."""

    def trimmed(token, marker: str = "") -> _Range:
        start, end = token.map
        while end > start and not lines[end - 1].strip():
            end -= 1
        return _Range(start, end, marker)

    blocks: list[_Range] = []
    for token in _parser().parse("".join(lines)):
        if token.level == 0 and token.map and token.nesting != -1:
            blocks.append(trimmed(token))
        elif token.level == 1 and token.type == "list_item_open":
            blocks[-1].items.append(trimmed(token, token.info + token.markup))
    return blocks


def _replacement(original: str, formatted: str) -> str:
    return formatted if original.endswith("\n") else formatted.rstrip("\n")


def _merge_items(
    after: _Range,
    formatted: _Range,
    after_lines: list[str],
    formatted_lines: list[str],
    unchanged: Counter[str],
) -> list[str] | None:
    """The list `after` with only its changed items formatted, or None if they cannot be paired.

    Unchanged items keep their text and markers.  A formatted item keeps its own bullet character,
    since a different one would start a new list.  When formatting renumbers the list, the whole
    list is formatted.
    """
    if len(after.items) != len(formatted.items):
        return None
    out: list[str] = []
    cursor = after.start
    for item, f_item in zip(after.items, formatted.items):
        bullet = item.marker in ("-", "*", "+")
        if not bullet and item.marker != f_item.marker:
            return None
        out.extend(after_lines[cursor : item.start])
        text = item.text(after_lines)
        if unchanged[text] > 0:
            unchanged[text] -= 1
            out.append(text)
        else:
            replacement = f_item.text(formatted_lines)
            if bullet:
                replacement = item.marker + replacement[len(f_item.marker) :]
            out.append(_replacement(text, replacement))
        cursor = item.end
    out.extend(after_lines[cursor : after.end])
    return out


def format_changed_blocks(before: str, after: str) -> str:
    """Format only the blocks and list items of `after` that do not appear unchanged in `before`.

    Blocks are matched by their exact text, so moving a block does not count as changing it.  If
    formatting changes the number of blocks, the blocks cannot be paired up, and `after` is
    returned unchanged.
    """
    after_lines = after.splitlines(keepends=True)
    formatted_lines = format_body(after).splitlines(keepends=True)
    after_blocks = _blocks(after_lines)
    formatted_blocks = _blocks(formatted_lines)
    if len(after_blocks) != len(formatted_blocks):
        return after
    before_lines = before.splitlines(keepends=True)
    unchanged: Counter[str] = Counter()
    for block in _blocks(before_lines):
        unchanged[block.text(before_lines)] += 1
        unchanged.update(item.text(before_lines) for item in block.items)
    out: list[str] = []
    cursor = 0
    for block, f_block in zip(after_blocks, formatted_blocks):
        out.extend(after_lines[cursor : block.start])
        text = block.text(after_lines)
        if unchanged[text] > 0:
            unchanged[text] -= 1
            out.append(text)
        else:
            merged = block.items and _merge_items(
                block, f_block, after_lines, formatted_lines, unchanged
            )
            if merged:
                out.extend(merged)
            else:
                out.append(_replacement(text, f_block.text(formatted_lines)))
        cursor = block.end
    out.extend(after_lines[cursor:])
    return "".join(out)


def is_memory_index(path: Path) -> bool:
    return (path.parent.name, path.name) == MEMORY_INDEX


def format_file(path: Path, before: str | None = None, *, whole: bool = False) -> Outcome:
    """Format the file at `path`, given its content before the change (None for a new file)."""
    if is_memory_index(path):
        return Outcome(path)
    path = path.resolve()
    outcome = Outcome(path)
    text = path.read_text()
    document = Document.parse(text)
    previous = None if whole or before is None else Document.parse(before)

    if previous is None:
        body = format_body(document.body)
        if document.yaml is not None and body:
            body = "\n" + body  # one blank line between the frontmatter and the body
    else:
        body = format_changed_blocks(previous.body, document.body)
    if _rendered_html(body) != _rendered_html(document.body):
        outcome.skipped_reason = (
            "formatting would change the rendered content, so the file was left as is"
        )
        return outcome

    yaml = document.yaml
    if yaml is not None and (previous is None or previous.yaml != yaml):
        result = check_frontmatter(path, yaml)
        yaml, outcome.kind, outcome.diagnostics = result.yaml, result.kind, result.diagnostics

    formatted = Document(yaml, body).render()
    if formatted != text:
        path.write_text(formatted)
        outcome.changed = True
    return outcome


def _report(outcome: Outcome) -> str | None:
    """Problems for Claude to act on, or None."""
    lines = []
    if outcome.skipped_reason:
        lines.append(f"{HOOK_NAME}: {outcome.path}: {outcome.skipped_reason}.")
    if outcome.diagnostics:
        kind = f" ({outcome.kind})" if outcome.kind else ""
        lines.append(f"{HOOK_NAME}: frontmatter problems in {outcome.path}{kind}:")
        lines.extend(f"  {diagnostic}" for diagnostic in outcome.diagnostics)
    return "\n".join(lines) or None


def _emit(lines: list[str]) -> None:
    if lines:
        output = {
            "hookSpecificOutput": {
                "hookEventName": "PostToolUse",
                "additionalContext": "\n".join(lines),
            }
        }
        print(json.dumps(output))


def run_edit_hook(event: dict) -> int:
    file_path = (event.get("tool_input") or {}).get("file_path")
    if not file_path or Path(file_path).suffix.lower() not in MARKDOWN_SUFFIXES:
        return 0
    path = Path(file_path)
    if not path.is_file():
        return 0
    before = (event.get("tool_response") or {}).get("originalFile") or None
    outcome = format_file(path, before)
    # Claude Code already tells the model that a hook changed the file; this line says what the
    # result guarantees, so the model has no reason to check it again.
    lines = [f"{HOOK_NAME}: {FORMATTED_NOTE}."] if outcome.changed else []
    report = _report(outcome)
    if report:
        lines.append(report)
    _emit(lines)
    return 0


def markdown_paths(command: str, cwd: Path) -> list[Path]:
    """The Markdown files a shell command names, resolved against a leading `cd`, if any."""
    base = cwd
    match = LEADING_CD.match(command)
    if match:
        directory = next(group for group in match.groups() if group is not None)
        base = cwd / Path(directory).expanduser()

    def resolve(name: str) -> Path:
        path = Path(name).expanduser()
        return path if path.is_absolute() else base / path

    found: dict[Path, None] = {}
    for name in MARKDOWN_PATH.findall(command):
        found[resolve(name)] = None
    for pattern in MARKDOWN_GLOB.findall(command):
        path = resolve(pattern)
        anchor = Path(path.anchor)
        for match_path in anchor.glob(str(path.relative_to(anchor))):
            found[match_path] = None
    return list(found)[:MAX_CANDIDATES]


def _read_small(path: Path) -> str | None:
    try:
        if path.is_file() and path.stat().st_size <= MAX_FILE_BYTES:
            return path.read_text()
    except (OSError, UnicodeDecodeError):
        pass
    return None


def _snapshot_path(event: dict) -> Path:
    return SNAPSHOT_DIR / f"{event.get('tool_use_id', 'unknown')}.json"


def run_bash_pre(event: dict) -> int:
    command = (event.get("tool_input") or {}).get("command") or ""
    paths = markdown_paths(command, Path(event.get("cwd") or "."))
    if not paths:
        return 0
    SNAPSHOT_DIR.mkdir(exist_ok=True)
    now = time.time()
    for stale in SNAPSHOT_DIR.glob("*.json"):  # left behind when a command never finished
        if now - stale.stat().st_mtime > SNAPSHOT_MAX_AGE_SECONDS:
            stale.unlink(missing_ok=True)
    snapshot = {str(path): _read_small(path) for path in paths}
    _snapshot_path(event).write_text(json.dumps(snapshot))
    return 0


def run_bash_post(event: dict) -> int:
    snapshot_file = _snapshot_path(event)
    if not snapshot_file.exists():
        return 0
    snapshot: dict[str, str | None] = json.loads(snapshot_file.read_text())
    snapshot_file.unlink(missing_ok=True)

    after = {name: _read_small(Path(name)) for name in snapshot}
    # A new file holding the earlier content of another named file was moved or copied there.
    earlier_contents = {before for before in snapshot.values() if before is not None}

    formatted: list[Path] = []
    lines: list[str] = []
    for name, before in snapshot.items():
        current = after[name]
        if current is None or current == before:
            continue  # deleted, unreadable, or unchanged (the command only read it)
        if before is None and current in earlier_contents:
            continue  # a renamed or copied file, not new writing
        outcome = format_file(Path(name), before)
        if outcome.changed:
            formatted.append(outcome.path)
        report = _report(outcome)
        if report:
            lines.append(report)
    if formatted:
        cwd = Path(event.get("cwd") or ".")
        names = ", ".join(
            str(p.relative_to(cwd)) if p.is_relative_to(cwd) else str(p) for p in formatted
        )
        lines.insert(
            0, f"{HOOK_NAME}: reformatted {names} ({FORMATTED_NOTE}); re-read before editing again."
        )
    _emit(lines)
    return 0


def run_hook(event: dict) -> int:
    hook_event, tool = event.get("hook_event_name"), event.get("tool_name")
    try:
        if tool == "Bash" and hook_event == "PreToolUse":
            return run_bash_pre(event)
        if tool == "Bash" and hook_event == "PostToolUse":
            return run_bash_post(event)
        if hook_event == "PostToolUse":
            return run_edit_hook(event)
    except Exception as error:  # a formatter failure must never block Claude's work
        print(f"{HOOK_NAME}: {type(error).__name__}: {error}", file=sys.stderr)
        return 1
    return 0


def run_paths(paths: list[str]) -> int:
    status = 0
    for name in paths:
        outcome = format_file(Path(name), whole=True)
        if outcome.changed:
            print(f"formatted {name}")
        report = _report(outcome)
        if report:
            print(report, file=sys.stderr)
            if outcome.skipped_reason or any(d.severity == "error" for d in outcome.diagnostics):
                status = 1
    return status


def main() -> int:
    if len(sys.argv) > 1:
        return run_paths(sys.argv[1:])
    return run_hook(json.load(sys.stdin))


if __name__ == "__main__":
    sys.exit(main())
