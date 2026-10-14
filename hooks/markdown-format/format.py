#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13"
# dependencies = ["mdformat>=1.0,<2", "mdformat-gfm>=1.0"]
# ///
"""Format Markdown files after Claude edits them, and lint Claude Code frontmatter.

As a PostToolUse hook on Edit and Write, it reads the hook event from stdin.  Given paths
instead, it formats those files, e.g. to bring an existing repository into line once.

The body is formatted by mdformat: wrapped at WRAP_WIDTH, ordered lists numbered consecutively,
tables aligned, bold removed and existing double spaces after sentences kept.  The frontmatter is formatted and linted by
claude-yaml.  A file is written only if the result renders the same HTML as before, apart from
the removed bold.  Lint problems are returned to Claude as additional context.

Besides the dependencies above, this needs the mdformat-no-bold, mdformat-sentence-spacing and
claude-yaml packages, passed with `uv run --with`.  See README.md.
"""

from __future__ import annotations

import json
import sys
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


def _html_parser() -> MarkdownIt:
    return MarkdownIt("commonmark").enable(["table", "strikethrough"])


def _rendered_html(text: str, *, drop_bold: bool) -> str:
    """Render to HTML with whitespace normalized, optionally ignoring bold markers."""
    parser = _html_parser()
    tokens = parser.parse(text)
    if drop_bold:
        for token in tokens:
            if token.children:
                token.children = [
                    child for child in token.children if child.type not in ("strong_open", "strong_close")
                ]
    html = parser.renderer.render(tokens, parser.options, {})
    return " ".join(html.split())


def format_body(body: str) -> str:
    return mdformat.text(body, extensions=EXTENSIONS, options=MDFORMAT_OPTIONS)


def format_file(path: Path) -> Outcome:
    path = path.resolve()
    outcome = Outcome(path)
    text = path.read_text()
    document = Document.parse(text)

    body = format_body(document.body)
    if _rendered_html(body, drop_bold=False) != _rendered_html(document.body, drop_bold=True):
        outcome.skipped_reason = "formatting would change the rendered content, so the file was left as is"
        return outcome

    yaml = document.yaml
    if yaml is not None:
        result = check_frontmatter(path, yaml)
        yaml, outcome.kind, outcome.diagnostics = result.yaml, result.kind, result.diagnostics
        if body:
            body = "\n" + body  # one blank line between the frontmatter and the body

    formatted = Document(yaml, body).render()
    if formatted != text:
        path.write_text(formatted)
        outcome.changed = True
    return outcome


def _report(outcome: Outcome) -> str | None:
    """Text for Claude, or None when there is nothing it needs to act on."""
    lines = []
    if outcome.skipped_reason:
        lines.append(f"{HOOK_NAME}: {outcome.path}: {outcome.skipped_reason}.")
    if outcome.diagnostics:
        kind = f" ({outcome.kind})" if outcome.kind else ""
        lines.append(f"{HOOK_NAME}: frontmatter problems in {outcome.path}{kind}:")
        lines.extend(f"  {diagnostic}" for diagnostic in outcome.diagnostics)
    return "\n".join(lines) or None


def run_hook(event: dict) -> int:
    file_path = (event.get("tool_input") or {}).get("file_path")
    if not file_path or Path(file_path).suffix.lower() not in MARKDOWN_SUFFIXES:
        return 0
    path = Path(file_path)
    if not path.is_file():
        return 0
    try:
        outcome = format_file(path)
    except Exception as error:  # a formatter failure must never block Claude's edit
        print(f"{HOOK_NAME}: {path}: {type(error).__name__}: {error}", file=sys.stderr)
        return 1
    # Claude Code already tells the model that a hook changed the file; this line says what the
    # result guarantees, so the model has no reason to check it again.
    lines = [f"{HOOK_NAME}: {FORMATTED_NOTE}."] if outcome.changed else []
    report = _report(outcome)
    if report:
        lines.append(report)
    if lines:
        output = {"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": "\n".join(lines)}}
        print(json.dumps(output))
    return 0


def run_paths(paths: list[str]) -> int:
    status = 0
    for name in paths:
        outcome = format_file(Path(name))
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
