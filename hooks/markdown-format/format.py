#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13"
# dependencies = ["mdformat>=1.0,<2", "mdformat-gfm>=1.0"]
# ///
"""Format Markdown files after Claude changes them, and lint Claude Code frontmatter.

As a PostToolUse hook on Edit and Write, it reads the hook event from stdin and formats the edited
file.  Only the top-level blocks (paragraphs, lists, tables, headings) that changed are formatted,
so a small edit to an unformatted file does not reformat the whole file.  A new file is formatted
whole.  Given paths instead of a hook event, it formats those files whole, e.g. to bring an existing
repository into line once.

The body is formatted by mdformat: wrapped at WRAP_WIDTH, ordered lists numbered consecutively,
tables aligned, bold removed and existing double spaces after sentences kept.  Frontmatter that
changed is formatted and linted by claude-yaml.  A file is written only if the result renders the
same HTML as before, apart from removed bold.  Lint problems are returned to Claude as additional
context.

Besides the dependencies above, this needs the mdformat-no-bold, mdformat-sentence-spacing and
claude-yaml packages, passed with `uv run --with`.  See README.md.
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import mdformat
from claude_yaml import Diagnostic, Document, check_frontmatter
from markdown_it import MarkdownIt

WRAP_WIDTH = 100
# Each mdformat plugin enabled, with what it guarantees when the hook reports a formatted file.
PLUGIN_RULES = {
    "gfm": "tables aligned",
    "no_bold": "bold removed",
    "sentence_spacing": None,  # keeps the author's spacing, so nothing to report
}
EXTENSIONS = set(PLUGIN_RULES)
# "number" writes 1., 2., 3. rather than repeating the first number, renumbering any gaps.
MDFORMAT_OPTIONS = {"wrap": WRAP_WIDTH, "number": True}
FORMATTED_NOTE = ", ".join(
    [
        f"wrapped at {WRAP_WIDTH} columns",
        "lists numbered",
        *(rule for rule in PLUGIN_RULES.values() if rule),
    ]
)
MARKDOWN_SUFFIXES = {".md", ".markdown"}
HOOK_NAME = "markdown-format"


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


def _block_ranges(lines: list[str]) -> list[tuple[int, int]]:
    """Line ranges of the top-level blocks, without trailing blank lines."""
    ranges = []
    for token in _parser().parse("".join(lines)):
        if token.level == 0 and token.map and token.nesting != -1:
            start, end = token.map
            while end > start and not lines[end - 1].strip():
                end -= 1
            ranges.append((start, end))
    return ranges


def format_changed_blocks(before: str, after: str) -> str:
    """Format only the blocks of `after` that do not appear unchanged in `before`.

    Blocks are matched by their exact text, so moving a block does not count as changing it.  If
    formatting changes the number of blocks, the blocks cannot be paired up, and `after` is
    returned unchanged.
    """
    after_lines = after.splitlines(keepends=True)
    formatted_lines = format_body(after).splitlines(keepends=True)
    after_ranges = _block_ranges(after_lines)
    formatted_ranges = _block_ranges(formatted_lines)
    if len(after_ranges) != len(formatted_ranges):
        return after
    before_lines = before.splitlines(keepends=True)
    unchanged = Counter("".join(before_lines[s:e]) for s, e in _block_ranges(before_lines))
    out: list[str] = []
    cursor = 0
    for (start, end), (f_start, f_end) in zip(after_ranges, formatted_ranges):
        out.extend(after_lines[cursor:start])
        block = "".join(after_lines[start:end])
        if unchanged[block] > 0:
            unchanged[block] -= 1
            out.append(block)
        else:
            replacement = "".join(formatted_lines[f_start:f_end])
            if not block.endswith("\n"):
                replacement = replacement.rstrip("\n")
            out.append(replacement)
        cursor = end
    out.extend(after_lines[cursor:])
    return "".join(out)


def format_file(path: Path, before: str | None = None, *, whole: bool = False) -> Outcome:
    """Format the file at `path`, given its content before the change (None for a new file)."""
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


def run_hook(event: dict) -> int:
    hook_event = event.get("hook_event_name")
    try:
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
