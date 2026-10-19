import importlib.util
import io
import json
import subprocess
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


def run_hook(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], event: dict
) -> dict | None:
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(event)))
    monkeypatch.setattr(sys, "argv", ["format.py"])
    assert markdown_format.main() == 0
    out = capsys.readouterr().out
    return json.loads(out) if out else None


def edit_event(path: Path) -> dict:
    return {
        "hook_event_name": "PostToolUse",
        "tool_name": "Edit",
        "tool_input": {"file_path": str(path)},
    }


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


def test_ordered_lists_numbered(tmp_path: Path) -> None:
    path = write(tmp_path / "doc.md", "1. foo\n1. bar\n1. baz\n\nThen:\n\n3. a\n5. b\n")
    markdown_format.format_file(path)
    assert path.read_text() == "1. foo\n2. bar\n3. baz\n\nThen:\n\n3. a\n4. b\n"


def test_ordered_lists_not_zero_padded(tmp_path: Path) -> None:
    items = [f"{n}. item {n}\n" for n in range(1, 11)]
    items[8] = "9. item 9 " + "word " * 20 + "\n"
    path = write(tmp_path / "doc.md", "".join(items))
    markdown_format.format_file(path)
    lines = path.read_text().splitlines()
    assert lines[0] == "1. item 1"
    assert lines[8:11] == ["9. item 9 " + "word " * 17 + "word", "   word word", "10. item 10"]


def test_unchanged_file_not_written(tmp_path: Path) -> None:
    path = write(tmp_path / "doc.md", "Already tidy.\n")
    before = path.stat().st_mtime_ns
    assert not markdown_format.format_file(path).changed
    assert path.stat().st_mtime_ns == before


def test_frontmatter_formatted_and_kept_apart(tmp_path: Path) -> None:
    path = write(
        tmp_path / "notes.md", "---\ntitle:   Hello\ntags:\n- a\n---\n# **Title**\n\nBody\n"
    )
    markdown_format.format_file(path)
    assert path.read_text() == "---\ntitle: Hello\ntags:\n  - a\n---\n\n# Title\n\nBody\n"


def test_frontmatter_only(tmp_path: Path) -> None:
    path = write(tmp_path / "notes.md", "---\ntitle: Hello\n---\n")
    assert not markdown_format.format_file(path).changed


def test_claude_frontmatter_linted(tmp_path: Path) -> None:
    path = write(
        tmp_path / ".claude/agents/a.md",
        "---\nname: a\ndescription: b\ncolor: chartreuse\n---\n\nBody\n",
    )
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


def test_memory_index_left_alone(tmp_path: Path) -> None:
    text = "- [A](a.md) — " + "word " * 30 + "\n"
    index = write(tmp_path / "memory/MEMORY.md", text)
    entry = write(tmp_path / "memory/a.md", text)
    assert not markdown_format.format_file(index).changed
    assert index.read_text() == text
    assert markdown_format.format_file(entry).changed


def test_hook_ignores_other_files(tmp_path: Path, monkeypatch, capsys) -> None:
    path = write(tmp_path / "a.py", "x  =  1\n")
    assert run_hook(monkeypatch, capsys, edit_event(path)) is None
    assert path.read_text() == "x  =  1\n"


def test_hook_ignores_missing_file(tmp_path: Path, monkeypatch, capsys) -> None:
    assert run_hook(monkeypatch, capsys, edit_event(tmp_path / "gone.md")) is None


def test_hook_silent_when_unchanged(tmp_path: Path, monkeypatch, capsys) -> None:
    path = write(tmp_path / "doc.md", "Already tidy.\n")
    assert run_hook(monkeypatch, capsys, edit_event(path)) is None


def test_hook_notes_formatting(tmp_path: Path, monkeypatch, capsys) -> None:
    path = write(tmp_path / "doc.md", "Some **bold**.\n")
    output = run_hook(monkeypatch, capsys, edit_event(path))
    context = output["hookSpecificOutput"]["additionalContext"]
    assert (
        context
        == "markdown-format: wrapped at 100 columns, lists numbered, tables aligned, bold removed."
    )
    assert path.read_text() == "Some bold.\n"


def test_hook_reports_lint(tmp_path: Path, monkeypatch, capsys) -> None:
    path = write(tmp_path / ".claude/skills/s/SKILL.md", "---\nname: s\nturbo: 1\n---\n\nBody\n")
    output = run_hook(monkeypatch, capsys, edit_event(path))
    context = output["hookSpecificOutput"]["additionalContext"]
    assert output["hookSpecificOutput"]["hookEventName"] == "PostToolUse"
    assert "(Skill)" in context and "unknown key 'turbo'" in context
    assert "wrapped at" not in context  # nothing to reformat here


def test_hook_failure_does_not_raise(tmp_path: Path, monkeypatch, capsys) -> None:
    path = write(tmp_path / "doc.md", "Hello\n")

    def explode(body: str) -> str:
        raise RuntimeError("boom")

    monkeypatch.setattr(markdown_format, "format_body", explode)
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(edit_event(path))))
    monkeypatch.setattr(sys, "argv", ["format.py"])
    assert markdown_format.main() == 1
    assert "RuntimeError: boom" in capsys.readouterr().err


LONG_A = "Alpha " * 25 + "end.\n"
LONG_B = "Bravo " * 25 + "end.\n"


def test_changed_blocks_only(tmp_path: Path) -> None:
    before = f"# Doc\n\n{LONG_A}\n{LONG_B}"
    after = f"# Doc\n\n{LONG_A}\n{LONG_B.replace('end.', 'finish.')}"
    path = write(tmp_path / "doc.md", after)
    markdown_format.format_file(path, before)
    text = path.read_text()
    assert LONG_A in text  # untouched, still one long line
    assert LONG_B.replace("end.", "finish.") not in text  # the edited paragraph was wrapped
    assert all(len(line) <= 100 for line in text.split(LONG_A)[1].splitlines())


def test_changed_list_item_only(tmp_path: Path) -> None:
    before = f"* {LONG_A}* Two\n"
    path = write(tmp_path / "doc.md", f"* {LONG_A}* Two **now**\n")
    markdown_format.format_file(path, before)
    assert path.read_text() == f"* {LONG_A}* Two now\n"


def test_list_renumbered_whole(tmp_path: Path) -> None:
    before = f"1. {LONG_A}2. Two\n"
    path = write(tmp_path / "doc.md", f"1. {LONG_A}2. Two\n4. Three\n")
    markdown_format.format_file(path, before)
    text = path.read_text()
    assert text.endswith("2. Two\n3. Three\n")
    assert all(len(line) <= 100 for line in text.splitlines())


def test_moved_block_is_not_changed(tmp_path: Path) -> None:
    before = f"{LONG_A}\n{LONG_B}"
    path = write(tmp_path / "doc.md", f"{LONG_B}\n{LONG_A}")
    assert not markdown_format.format_file(path, before).changed


def test_reference_definitions_stay_in_place(tmp_path: Path) -> None:
    before = "[ref]: https://x.test\n\nSee [it][ref].\n"
    path = write(tmp_path / "doc.md", "[ref]: https://x.test\n\nSee [it][ref] **now**.\n")
    markdown_format.format_file(path, before)
    assert path.read_text() == "[ref]: https://x.test\n\nSee [it][ref] now.\n"


def test_unchanged_frontmatter_not_relinted(tmp_path: Path) -> None:
    frontmatter = "---\nname: a\ndescription: b\nturbo: 1\n---\n\n"
    before = frontmatter + "Body\n"
    path = write(tmp_path / ".claude/agents/a.md", frontmatter + "Body **more**\n")
    outcome = markdown_format.format_file(path, before)
    assert outcome.diagnostics == []
    assert path.read_text() == frontmatter + "Body more\n"


def test_changed_frontmatter_linted(tmp_path: Path) -> None:
    before = "---\nname: a\ndescription: b\n---\n\nBody\n"
    path = write(
        tmp_path / ".claude/agents/a.md", "---\nname: a\ndescription: b\nturbo: 1\n---\n\nBody\n"
    )
    assert [d.path for d in markdown_format.format_file(path, before).diagnostics] == ["/turbo"]


def test_edit_hook_uses_original_file(tmp_path: Path, monkeypatch, capsys) -> None:
    before = f"{LONG_A}\nShort.\n"
    path = write(tmp_path / "doc.md", f"{LONG_A}\nShort **now**.\n")
    event = edit_event(path) | {"tool_response": {"originalFile": before}}
    output = run_hook(monkeypatch, capsys, event)
    assert path.read_text() == f"{LONG_A}\nShort now.\n"
    assert "wrapped at 100 columns" in output["hookSpecificOutput"]["additionalContext"]


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        pytest.param("cat docs/A.md", ["base/docs/A.md"], id="relative"),
        pytest.param("cd /repo && wc -l docs/A.md", ["/repo/docs/A.md"], id="leading-cd"),
        pytest.param(
            "cd sub; sed -i '' 's/a/b/' x.markdown", ["base/sub/x.markdown"], id="cd-semicolon"
        ),
        pytest.param(
            "cd /repo && python3 - <<'EOF'\np='docs/OPERATING.md'\nopen(p,'w')\nEOF",
            ["/repo/docs/OPERATING.md"],
            id="python-heredoc",
        ),
        pytest.param('edits = {"a.md": 1, "/abs/b.md": 2}', ["base/a.md", "/abs/b.md"], id="dict"),
        pytest.param("mv a.md b.md", ["base/a.md", "base/b.md"], id="rename"),
        pytest.param("grep -n md file.mdx notes.txt", [], id="not-markdown"),
    ],
)
def test_markdown_paths(command: str, expected: list[str]) -> None:
    found = markdown_format.markdown_paths(command, Path("base"))
    assert [str(p) for p in found] == expected


def test_markdown_paths_glob(tmp_path: Path) -> None:
    write(tmp_path / "docs/a.md", "a\n")
    write(tmp_path / "docs/b.md", "b\n")
    found = markdown_format.markdown_paths("for f in docs/*.md; do wc -l $f; done", tmp_path)
    assert sorted(p.name for p in found) == ["a.md", "b.md"]


@pytest.fixture
def snapshots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    directory = tmp_path / "snapshots"
    monkeypatch.setattr(markdown_format, "SNAPSHOT_DIR", directory)
    return directory


def bash_events(command: str, cwd: Path, tool_use_id: str = "toolu_1") -> tuple[dict, dict]:
    common = {
        "tool_name": "Bash",
        "tool_input": {"command": command},
        "cwd": str(cwd),
        "tool_use_id": tool_use_id,
    }
    return common | {"hook_event_name": "PreToolUse"}, common | {"hook_event_name": "PostToolUse"}


def run_bash(monkeypatch, capsys, command: str, cwd: Path, action) -> dict | None:
    pre, post = bash_events(command, cwd)
    assert run_hook(monkeypatch, capsys, pre) is None
    action()
    return run_hook(monkeypatch, capsys, post)


def test_bash_read_only_does_nothing(tmp_path: Path, snapshots: Path, monkeypatch, capsys) -> None:
    path = write(tmp_path / "doc.md", "A **b**\n")
    assert run_bash(monkeypatch, capsys, "wc -l doc.md", tmp_path, lambda: None) is None
    assert path.read_text() == "A **b**\n"
    assert not list(snapshots.glob("*.json"))


def test_bash_edit_formats_changed_blocks(
    tmp_path: Path, snapshots: Path, monkeypatch, capsys
) -> None:
    path = write(tmp_path / "docs/OPERATING.md", f"{LONG_A}\nShort.\n")
    command = "python3 - <<'EOF'\np='docs/OPERATING.md'\nEOF"
    output = run_bash(
        monkeypatch,
        capsys,
        command,
        tmp_path,
        lambda: path.write_text(f"{LONG_A}\nShort **now**.\n"),
    )
    assert path.read_text() == f"{LONG_A}\nShort now.\n"
    context = output["hookSpecificOutput"]["additionalContext"]
    assert context.startswith(
        "markdown-format: reformatted docs/OPERATING.md (wrapped at 100 columns"
    )
    assert context.endswith("re-read before editing again.")


def test_bash_rename_left_alone(tmp_path: Path, snapshots: Path, monkeypatch, capsys) -> None:
    old = write(tmp_path / "a.md", f"{LONG_A}")
    new = tmp_path / "b.md"
    assert run_bash(monkeypatch, capsys, "mv a.md b.md", tmp_path, lambda: old.rename(new)) is None
    assert new.read_text() == LONG_A


def test_bash_new_file_formatted_whole(
    tmp_path: Path, snapshots: Path, monkeypatch, capsys
) -> None:
    path = tmp_path / "new.md"
    output = run_bash(
        monkeypatch,
        capsys,
        "cat > new.md <<'EOF'\n...\nEOF",
        tmp_path,
        lambda: path.write_text(LONG_A),
    )
    assert all(len(line) <= 100 for line in path.read_text().splitlines())
    assert "reformatted new.md" in output["hookSpecificOutput"]["additionalContext"]


def test_bash_deleted_file_ignored(tmp_path: Path, snapshots: Path, monkeypatch, capsys) -> None:
    path = write(tmp_path / "a.md", "A\n")
    assert run_bash(monkeypatch, capsys, "rm a.md", tmp_path, path.unlink) is None


def test_bash_post_without_snapshot(tmp_path: Path, snapshots: Path, monkeypatch, capsys) -> None:
    _, post = bash_events("echo a.md", tmp_path)
    assert run_hook(monkeypatch, capsys, post) is None


def test_bash_memory_index_left_alone(tmp_path: Path, snapshots: Path, monkeypatch, capsys) -> None:
    path = write(tmp_path / "memory/MEMORY.md", "- [A](a.md)\n")
    text = "- [A](a.md) — " + "word " * 30 + "\n"
    command = "python - memory/MEMORY.md <<'EOF'\n...\nEOF"
    assert run_bash(monkeypatch, capsys, command, tmp_path, lambda: path.write_text(text)) is None
    assert path.read_text() == text


def git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.email", "test@example.com")
    git(tmp_path, "config", "user.name", "Test")
    write(tmp_path / "doc.md", LONG_A)
    git(tmp_path, "add", "doc.md")
    git(tmp_path, "commit", "-q", "-m", "doc")
    return tmp_path


def test_bash_git_restore_left_alone(repo: Path, snapshots: Path, monkeypatch, capsys) -> None:
    path = repo / "doc.md"
    markdown_format.format_file(path, whole=True)
    assert path.read_text() != LONG_A

    def restore() -> None:
        git(repo, "checkout", "--", "doc.md")

    assert run_bash(monkeypatch, capsys, "git checkout -- doc.md", repo, restore) is None
    assert path.read_text() == LONG_A


def test_bash_edit_then_stage_formatted(repo: Path, snapshots: Path, monkeypatch, capsys) -> None:
    path = repo / "doc.md"

    def edit_and_stage() -> None:
        path.write_text(LONG_A.replace("end.", "finish."))
        git(repo, "add", "doc.md")

    output = run_bash(monkeypatch, capsys, "python edit.py && git add doc.md", repo, edit_and_stage)
    assert "reformatted doc.md" in output["hookSpecificOutput"]["additionalContext"]
    assert all(len(line) <= 100 for line in path.read_text().splitlines())
