#!/usr/bin/env python3
"""Allocate and record MATLAB profile runs in the runs directory's index.json.

Records the start and end of a profiling run with deterministic catalog bookkeeping:

  record start <slug>     allocate the next <slug>_NNN run directory and print it
  record stop  <run_dir>  write that run's index entry and advance `last`

`start` runs before the MATLAB profiling fragment; it only creates the run
directory. `stop` runs after; it copies settings and wall time out of the run's
profile.json into the index, with the conditions that make runs comparable: the
directory holding the profiled code and the MATLAB startup options. Reads of
index.json / last stay agent-direct; this tool owns only the writes.

Usage:
    profile_run.py record start <slug> [--runs-dir DIR]
    profile_run.py record stop <run_dir> --target T --label L --concern C
                   [--code-dir DIR] [--matlab-options=OPTS]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

INDEX_NAME = "index.json"
LAST_NAME = "last"

# A slug becomes a directory name inside the runs dir, so it may not contain a path
# separator or start with a dot: "/abs/x" or "../x" would place the run outside it.
SLUG_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")

# One index.json entry.
Entry = dict[str, Any]


class CatalogError(Exception):
    """A run or catalog the tool cannot record; main() reports the message and exits 1."""


def load_index(runs_dir: Path) -> list[Entry]:
    """Return the catalog entries as a list, or [] if the index is absent or empty."""
    index_path = runs_dir / INDEX_NAME
    if not index_path.is_file():
        return []
    text = index_path.read_text().strip()
    if not text:
        return []
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise CatalogError(f"{index_path} is not valid JSON: {e}") from e
    if isinstance(data, list) and all(isinstance(entry, dict) for entry in data):
        return data
    # Anything else is an unsupported or old-format catalog. Exit cleanly with
    # guidance rather than letting a later access raise; deleting the catalog
    # starts fresh.
    raise CatalogError(
        f"{index_path} is not a list of run entries (unsupported or old-format "
        f"catalog). Delete {runs_dir} and rerun to start a fresh catalog."
    )


def write_index_atomic(runs_dir: Path, entries: list[Entry]) -> None:
    """Write entries to index.json via a temp file + atomic rename."""
    runs_dir.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=runs_dir, prefix=".index.", suffix=".tmp")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(entries, f, indent=2)
            f.write("\n")
        tmp.replace(runs_dir / INDEX_NAME)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def parse_seq(name: str, slug: str) -> int | None:
    """Return the integer sequence in `<slug>_<NNN>`, or None if name is not one."""
    m = re.fullmatch(re.escape(slug) + r"_(\d+)", name)
    return int(m.group(1)) if m else None


def next_id(runs_dir: Path, slug: str) -> str:
    """Allocate the next `<slug>_NNN` id, resolving the index and filesystem together."""
    seqs = [parse_seq(str(entry.get("id", "")), slug) for entry in load_index(runs_dir)]
    if runs_dir.is_dir():
        seqs += [parse_seq(child.name, slug) for child in runs_dir.iterdir() if child.is_dir()]
    seq = max((seq for seq in seqs if seq is not None), default=0) + 1
    # Defensive: never reuse a directory even if the index and filesystem drifted.
    while (runs_dir / f"{slug}_{seq:03d}").exists():
        seq += 1
    return f"{slug}_{seq:03d}"


def ensure_gitignore(runs_dir: Path, created: bool) -> None:
    """Keep the run catalog out of git; it is regenerable working data.

    Write `<runs_dir>/.gitignore` containing `*` so the whole catalog is ignored in
    whatever project the skill runs in. Only for a directory that is a catalog: one this
    call created, or one that already holds an index. A `*` ignore file in any other
    directory, such as a project root passed as --runs-dir, would hide the project from
    git. Never overwrites an existing file.
    """
    if not (created or (runs_dir / INDEX_NAME).is_file()):
        return
    gitignore = runs_dir / ".gitignore"
    if not gitignore.exists():
        gitignore.write_text("*\n")


def cmd_start(args: argparse.Namespace) -> None:
    if not SLUG_PATTERN.fullmatch(args.slug):
        raise CatalogError(
            f"invalid slug {args.slug!r}: use letters, digits, '.', '_' and '-', "
            "starting with a letter or digit"
        )
    runs_dir = Path(args.runs_dir)
    created = not runs_dir.exists()
    runs_dir.mkdir(parents=True, exist_ok=True)
    ensure_gitignore(runs_dir, created)
    # Another `record start` for the same slug can take the id between next_id and
    # mkdir; allocate again rather than fail.
    for _ in range(100):
        run_id = next_id(runs_dir, args.slug)
        run_dir = runs_dir / run_id
        try:
            run_dir.mkdir()
            break
        except FileExistsError:
            continue
    else:
        raise CatalogError(f"could not allocate a run directory for {args.slug!r} in {runs_dir}")
    # Sole stdout line: the run directory, for the agent to use as RUN_DIR and
    # pass back to `record stop`. Absolute, because the driver runs through MATLAB's
    # run(), which changes into the script's folder, so a relative path there would
    # resolve inside the run directory.
    print(run_dir.resolve())


def cmd_stop(args: argparse.Namespace) -> None:
    run_dir = Path(args.run_dir)
    runs_dir = run_dir.parent
    run_id = run_dir.name

    profile_path = run_dir / "profile.json"
    if not profile_path.is_file():
        raise CatalogError(f"{profile_path} not found; run the profiler before recording stop")
    try:
        profile_json = json.loads(profile_path.read_text())
    except json.JSONDecodeError as e:
        raise CatalogError(f"{profile_path} is not valid JSON: {e}") from e
    if not isinstance(profile_json, dict):
        raise CatalogError(f"{profile_path} is not a profile export (expected a JSON object)")

    settings = profile_json.get("Settings")
    wall_time = profile_json.get("WallTime")
    if settings is None:
        print(f"warning: {profile_path} has no Settings; wrote null", file=sys.stderr)
    if wall_time is None:
        print(f"warning: {profile_path} has no WallTime; wrote null", file=sys.stderr)

    entry = {
        "id": run_id,
        "target": args.target,
        "label": args.label,
        "timestamp": datetime.now().astimezone().isoformat(timespec="seconds"),
        "settings": settings,
        "concern": args.concern,
        "wall_time": wall_time,
        # Absolute, like the paths in the driver: the entry outlives the working directory.
        "code_dir": str(Path(args.code_dir).resolve()),
        "matlab_options": args.matlab_options,
    }

    # Replace any existing entry for this id so re-running stop is idempotent.
    entries = [e for e in load_index(runs_dir) if e.get("id") != run_id]
    entries.append(entry)
    write_index_atomic(runs_dir, entries)

    (runs_dir / LAST_NAME).write_text(run_id + "\n")
    print(run_id)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    commands = parser.add_subparsers(dest="command", required=True)

    record = commands.add_parser("record", help="bookend a profiling run")
    actions = record.add_subparsers(dest="action", required=True)

    start = actions.add_parser("start", help="allocate and create the run directory")
    start.add_argument("slug", help="run slug; the id becomes <slug>_NNN")
    start.add_argument(
        "--runs-dir", default="profile_runs", help="catalog directory (default: profile_runs)"
    )
    start.set_defaults(func=cmd_start)

    stop = actions.add_parser("stop", help="write the index entry for a finished run")
    stop.add_argument("run_dir", help="run directory printed by 'record start'")
    stop.add_argument("--target", required=True, help="what was profiled")
    stop.add_argument("--label", required=True, help="short human-readable label")
    stop.add_argument("--concern", required=True, help="time | memory | parallel | ...")
    stop.add_argument(
        "--code-dir",
        default=".",
        help="directory holding the profiled code (default: the working directory)",
    )
    stop.add_argument(
        "--matlab-options",
        default="",
        metavar="OPTS",
        help="MATLAB startup options the run used, written --matlab-options=OPTS since "
        "they start with a dash, such as --matlab-options=-singleCompThread (default: none)",
    )
    stop.set_defaults(func=cmd_stop)

    args = parser.parse_args()
    try:
        args.func(args)
    except CatalogError as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
