import importlib.util
import io
import json
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parent.parent / "format.py"
spec = importlib.util.spec_from_file_location("markdown_format", SCRIPT)
markdown_format = importlib.util.module_from_spec(spec)
sys.modules["markdown_format"] = markdown_format
spec.loader.exec_module(markdown_format)


def write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def run_hook(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], event: dict) -> dict | None:
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(event)))
    monkeypatch.setattr(sys, "argv", ["format.py"])
    assert markdown_format.main() == 0
    out = capsys.readouterr().out
    return json.loads(out) if out else None


def edit_event(path: Path) -> dict:
    return {"hook_event_name": "PostToolUse", "tool_name": "Edit", "tool_input": {"file_path": str(path)}}


def test_formats_body(tmp_path: Path) -> None:
    path = write(tmp_path / "doc.md", "Some **bold** text.  Next sentence.\n\n* item\n")
    outcome = markdown_format.format_file(path)
    assert outcome.changed
    assert path.read_text() == "Some bold text.  Next sentence.\n\n- item\n"


def test_wraps_at_100(tmp_path: Path) -> None:
    path = write(tmp_path / "doc.md", "word " * 30 + "\n")
    markdown_format.format_file(path)
    lines = path.read_text().splitlines()
    assert len(lines) == 2 and all(len(line) <= 100 for line in lines)


def test_unchanged_file_not_written(tmp_path: Path) -> None:
    path = write(tmp_path / "doc.md", "Already tidy.\n")
    before = path.stat().st_mtime_ns
    assert not markdown_format.format_file(path).changed
    assert path.stat().st_mtime_ns == before


def test_frontmatter_formatted_and_kept_apart(tmp_path: Path) -> None:
    path = write(tmp_path / "notes.md", "---\ntitle:   Hello\ntags:\n- a\n---\n# **Title**\n\nBody\n")
    markdown_format.format_file(path)
    assert path.read_text() == "---\ntitle: Hello\ntags:\n  - a\n---\n\n# Title\n\nBody\n"


def test_frontmatter_only(tmp_path: Path) -> None:
    path = write(tmp_path / "notes.md", "---\ntitle: Hello\n---\n")
    assert not markdown_format.format_file(path).changed


def test_claude_frontmatter_linted(tmp_path: Path) -> None:
    path = write(tmp_path / ".claude/agents/a.md", "---\nname: a\ndescription: b\ncolor: chartreuse\n---\n\nBody\n")
    outcome = markdown_format.format_file(path)
    assert outcome.kind == "Agent"
    assert [d.path for d in outcome.diagnostics] == ["/color"]


def test_invalid_yaml_left_but_body_formatted(tmp_path: Path) -> None:
    path = write(tmp_path / "SKILL.md", "---\ndescription: Use when: it fails\n---\n\n**Body**\n")
    outcome = markdown_format.format_file(path)
    assert path.read_text() == "---\ndescription: Use when: it fails\n---\n\nBody\n"
    assert "YAML syntax" in outcome.diagnostics[0].message


def test_symlink_target_written(tmp_path: Path) -> None:
    target = write(tmp_path / "real/CLAUDE.md", "A **b**\n")
    link = tmp_path / "CLAUDE.md"
    link.symlink_to(target)
    markdown_format.format_file(link)
    assert link.is_symlink()
    assert target.read_text() == "A b\n"


def test_content_change_skips_write(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = write(tmp_path / "doc.md", "Hello\n")
    monkeypatch.setattr(markdown_format, "format_body", lambda body: "Goodbye\n")
    outcome = markdown_format.format_file(path)
    assert outcome.skipped_reason
    assert path.read_text() == "Hello\n"


def test_idempotent(tmp_path: Path) -> None:
    path = write(tmp_path / "doc.md", "---\na:  1\n---\nOne.  Two **three** " + "word " * 30 + "\n")
    markdown_format.format_file(path)
    once = path.read_text()
    assert not markdown_format.format_file(path).changed
    assert path.read_text() == once


def test_hook_ignores_other_files(tmp_path: Path, monkeypatch, capsys) -> None:
    path = write(tmp_path / "a.py", "x  =  1\n")
    assert run_hook(monkeypatch, capsys, edit_event(path)) is None
    assert path.read_text() == "x  =  1\n"


def test_hook_ignores_missing_file(tmp_path: Path, monkeypatch, capsys) -> None:
    assert run_hook(monkeypatch, capsys, edit_event(tmp_path / "gone.md")) is None


def test_hook_silent_when_clean(tmp_path: Path, monkeypatch, capsys) -> None:
    path = write(tmp_path / "doc.md", "Some **bold**.\n")
    assert run_hook(monkeypatch, capsys, edit_event(path)) is None
    assert path.read_text() == "Some bold.\n"


def test_hook_reports_lint(tmp_path: Path, monkeypatch, capsys) -> None:
    path = write(tmp_path / ".claude/skills/s/SKILL.md", "---\nname: s\nturbo: 1\n---\n\nBody\n")
    output = run_hook(monkeypatch, capsys, edit_event(path))
    context = output["hookSpecificOutput"]["additionalContext"]
    assert output["hookSpecificOutput"]["hookEventName"] == "PostToolUse"
    assert "(Skill)" in context and "unknown key 'turbo'" in context


def test_hook_failure_does_not_raise(tmp_path: Path, monkeypatch, capsys) -> None:
    path = write(tmp_path / "doc.md", "Hello\n")

    def explode(body: str) -> str:
        raise RuntimeError("boom")

    monkeypatch.setattr(markdown_format, "format_body", explode)
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(edit_event(path))))
    monkeypatch.setattr(sys, "argv", ["format.py"])
    assert markdown_format.main() == 1
    assert "RuntimeError: boom" in capsys.readouterr().err
