#!/usr/bin/env python3
"""Show per-line breakdown for a function in a MATLAB profile.

Prints lines sorted by time (or by allocated memory with --by memory), with hit
counts and per-hit microseconds. Reads ExecutedLines from the profile.json
export. When the profile was taken with -memory on, each line also carries
allocated/freed/peak bytes, which are shown automatically. If no function name
is given, shows the breakdown for the top-N functions.

With --all-lines, lists every line of the named function in line order with its
hits, time (0 for a line that never ran) and source text, so which branches ran
and how often can be read without changing the code. ExecutedLines holds only
the lines that ran, so the function's extent comes from its source file
(FileName): from the `function` line at or before its first executed line, or
line 1 for a script, to the line before the next `function` line, or the end of
the file. A nested function's `function` line therefore ends its parent's
extent early, and the file must be unchanged since the run; --lines and --by do
not apply.

Usage:
    line_breakdown.py <profile.json> [<function>] [--top N] [--lines M] [--by time|memory]
    line_breakdown.py <profile.json> <function> --all-lines
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from function_labels import FunctionRow, LabelledFunction, labelled_functions

MB = 1e6

# A line declaring a function, and the name it declares: `function foo`,
# `function y = foo(a)` or `function [y, z] = foo(a)`, with a dotted name for a
# property method (`set.Prop`). Not matched inside a comment, which starts with %.
FUNCTION_LINE = re.compile(r"^\s*function\b")
DECLARATION = re.compile(r"^\s*function\s+(?:[^=%]*=\s*)?([A-Za-z]\w*(?:\.\w+)*)")


class ProfileError(Exception):
    """The profile has no lines to show; main() reports the message and exits 1."""


def show_function(fn: FunctionRow, label: str, max_lines: int, sort_by: str) -> bool:
    """Print the function's hottest lines; False when it has no line-level data."""
    executed_lines = fn.get("ExecutedLines") or []
    if not executed_lines:
        return False
    # The exporter writes the memory columns for every line or none, so the
    # first line settles it (executed_lines is non-empty here).
    has_mem = "allocated" in executed_lines[0]
    if sort_by == "memory" and has_mem:
        lines = sorted(executed_lines, key=lambda row: -row.get("allocated", 0))
    else:
        lines = sorted(executed_lines, key=lambda row: -row["time"])
    # Counts can arrive as floats: jsonencode writes integers of 1e6 and above as 1.0E+6.
    print(f"\n{label} -- {fn['TotalTime']:.3f}s, {int(fn['NumCalls'])} calls")
    if has_mem:
        print(
            f"  {'line':>6}  {'hits':>9}  {'time(s)':>10}  "
            f"{'alloc(MB)':>11}  {'freed(MB)':>11}  {'peak(MB)':>10}"
        )
        for row in lines[:max_lines]:
            print(
                f"  {row['line']:>6}  {int(row['hits']):>9}  {row['time']:>10.3f}  "
                f"{row.get('allocated', 0) / MB:>11.2f}  "
                f"{row.get('freed', 0) / MB:>11.2f}  {row.get('peak', 0) / MB:>10.2f}"
            )
    else:
        print(f"  {'line':>6}  {'hits':>9}  {'time(s)':>10}  {'us/hit':>10}")
        for row in lines[:max_lines]:
            us_per_hit = 1e6 * row["time"] / row["hits"] if row["hits"] else 0
            print(
                f"  {row['line']:>6}  {int(row['hits']):>9}  {row['time']:>10.3f}  {us_per_hit:>10.2f}"
            )
    return True


def function_extent(source: list[str], first_executed: int) -> tuple[int, int]:
    """The 1-based, inclusive line range of the function whose first executed line is given.

    From the nearest `function` line at or before that line, or line 1 when there is none (a
    script), to the line before the next `function` line after it, or the end of the file.
    """
    declared = [number for number, text in enumerate(source, 1) if FUNCTION_LINE.match(text)]
    start = max((n for n in declared if n <= first_executed), default=1)
    end = min((n for n in declared if n > first_executed), default=len(source) + 1) - 1
    return start, end


def show_all_lines(fn: FunctionRow, label: str) -> bool:
    """Print every line of the function's extent in line order, with hits, time and source;
    False when it has no line-level data. Errors when its file is missing or has changed."""
    executed = {int(row["line"]): row for row in fn.get("ExecutedLines") or []}
    if not executed:
        return False
    file_name = fn.get("FileName") or ""
    try:
        source = Path(file_name).read_text(errors="replace").splitlines()
    except OSError as e:
        raise ProfileError(f"Cannot read the source of {label}: {e}") from e

    start, end = function_extent(source, min(executed))
    # Two signs the file is not the one profiled: the `function` line names another
    # function (skipped for an anonymous function, whose name is no identifier and whose
    # extent is its enclosing function's), or a line ran beyond the extent, which a
    # nested function also causes by ending its parent's extent early.
    declaration = DECLARATION.match(source[start - 1])
    short_name = re.split(r"[>.]", fn["FunctionName"])[-1]
    if declaration and short_name.isidentifier():
        declared_name = declaration.group(1).split(".")[-1]
        if declared_name != short_name:
            raise ProfileError(
                f"{file_name} has changed since the run: line {start} declares "
                f"{declared_name}, not {short_name}"
            )
    if max(executed) > end:
        raise ProfileError(
            f"{file_name} has changed since the run, or {label} has nested functions: "
            f"line {max(executed)} ran, beyond the extent found (lines {start}-{end})"
        )

    print(f"\n{label} -- {fn['TotalTime']:.3f}s, {int(fn['NumCalls'])} calls")
    print(f"  {file_name}, lines {start}-{end}")
    print(f"  {'line':>6}  {'hits':>9}  {'time(s)':>10}  source")
    for number in range(start, end + 1):
        row = executed.get(number, {"hits": 0, "time": 0.0})
        print(
            f"  {number:>6}  {int(row['hits']):>9}  {row['time']:>10.3f}  "
            f"{source[number - 1].rstrip()}"
        )
    return True


def named(rows: list[LabelledFunction], name: str) -> list[LabelledFunction]:
    """Every function called name, or the one function labelled name."""
    # A name can match several rows (same-named functions in different folders);
    # show each. A full label selects one of them.
    matches = [row for row in rows if name in (row.fn["FunctionName"], row.label)]
    if not matches:
        by_time = sorted(rows, key=lambda row: -row.fn["TotalTime"])[:10]
        raise ProfileError(
            "\n".join(
                [
                    f"Function not found: {name}",
                    "Available functions (by time):",
                    *(f"  {row.label}" for row in by_time),
                ]
            )
        )
    return matches


def show_named(
    rows: list[LabelledFunction], name: str, max_lines: int, sort_by: str, all_lines: bool
) -> None:
    """Show the hottest lines, or with all_lines every line, of each function matching name."""
    if all_lines:
        shown = [show_all_lines(row.fn, row.label) for row in named(rows, name)]
    else:
        shown = [show_function(row.fn, row.label, max_lines, sort_by) for row in named(rows, name)]
    if not any(shown):
        raise ProfileError(f"No line-level data for {name}")


def show_top(rows: list[LabelledFunction], top: int, max_lines: int, sort_by: str) -> None:
    """Show the top functions by the sort key, counting only those with line-level data."""
    if sort_by == "memory":
        ranked = sorted(rows, key=lambda row: -row.fn.get("PeakMem", 0))
    else:
        ranked = sorted(rows, key=lambda row: -row.fn["TotalTime"])
    shown = 0
    for row in ranked:
        if show_function(row.fn, row.label, max_lines, sort_by):
            shown += 1
            if shown >= top:
                break
    if shown == 0:
        raise ProfileError("No line-level data in profile (no function has ExecutedLines)")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("profile_json", help="Path to profile.json")
    parser.add_argument("function", nargs="?", help="Function name (default: top-N functions)")
    parser.add_argument(
        "--top", type=int, default=5, help="How many functions when no name given (default 5)"
    )
    parser.add_argument(
        "--lines", type=int, default=10, help="Lines per function to show (default 10)"
    )
    parser.add_argument(
        "--by",
        choices=["time", "memory"],
        default="time",
        help="Sort lines by time (default) or by allocated memory",
    )
    parser.add_argument(
        "--all-lines",
        action="store_true",
        help="List every line of the named function in line order, with hits, time and "
        "source text, reading its extent from the source file (see above)",
    )
    args = parser.parse_args()
    if args.all_lines and not args.function:
        parser.error("--all-lines needs a function name")

    with open(args.profile_json) as f:
        data = json.load(f)
    fns = data["Functions"]
    try:
        # A time-only profile has no memory columns to sort by; stop rather than fall back to
        # sorting by time, as rank_functions and compare_runs do.
        if args.by == "memory" and not any("PeakMem" in fn for fn in fns):
            raise ProfileError("No memory data in profile (run with -memory on)")
        rows = labelled_functions(fns)
        if args.function:
            show_named(rows, args.function, args.lines, args.by, args.all_lines)
        else:
            show_top(rows, args.top, args.lines, args.by)
    except ProfileError as e:
        print(e, file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
