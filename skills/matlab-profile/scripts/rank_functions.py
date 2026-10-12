#!/usr/bin/env python3
"""Rank MATLAB profile functions by inclusive time, memory or call count.

Prints a table of the top-N functions sorted by TotalTime (inclusive, the
default), by PeakMem with allocated and freed bytes (--by memory, which needs a
-memory on profile), or by NumCalls (--by calls). Reads a profile.json produced
by the MATLAB-side export in scripts/export_profile_json.m.

Usage:
    rank_functions.py <profile.json> [--top N] [--by time|memory|calls] [--filter SUBSTR]
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

from function_labels import FunctionRow, LabelledFunction, labelled_functions

MB = 1e6


class ProfileError(Exception):
    """The profile has nothing to rank; main() reports the message and exits 1."""


def select_functions(fns: list[FunctionRow], name_filter: str | None) -> list[LabelledFunction]:
    """The labelled rows, keeping only those whose name contains a filter substring."""
    rows = labelled_functions(fns)
    if name_filter:
        tokens = [t.strip().lower() for t in name_filter.split(",") if t.strip()]
        rows = [row for row in rows if any(t in row.fn["FunctionName"].lower() for t in tokens)]
        if not rows:
            raise ProfileError(f"No functions match filter: {name_filter}")
    return rows


def print_by_time_or_calls(rows: list[LabelledFunction], top: int, by: str) -> None:
    """Time, calls and microseconds per call for each function, the sort key's column first."""
    time_first = by == "time"
    sort_field = "TotalTime" if time_first else "NumCalls"
    ranked = sorted(rows, key=lambda row: -row.fn[sort_field])

    def lead(time_cell: str, calls_cell: str) -> str:
        return f"{time_cell}  {calls_cell}" if time_first else f"{calls_cell}  {time_cell}"

    time_header = f"{'time(s)':>10}"
    calls_header = f"{'calls':>9}"
    print(f"{lead(time_header, calls_header)}  {'us/call':>12}  function")
    for fn, label in ranked[:top]:
        calls = int(fn["NumCalls"])
        us_per_call = 1e6 * fn["TotalTime"] / calls if calls else 0
        time_cell = f"{fn['TotalTime']:>10.3f}"
        calls_cell = f"{calls:>9d}"
        print(f"{lead(time_cell, calls_cell)}  {us_per_call:>12.2f}  {label}")


def print_by_memory(rows: list[LabelledFunction], top: int) -> None:
    """Peak, allocated and freed memory for each function, largest peak first."""
    # Test field presence, not truthiness: a function that allocated nothing
    # has PeakMem == 0 but still carries memory data. A -memory on profile
    # carries all three memory fields together or none (see
    # references/PROFILER_REFERENCE.md); read alloc/freed with .get anyway so
    # a malformed or hand-edited profile cannot raise.
    mem_rows = [row for row in rows if "PeakMem" in row.fn]
    if not mem_rows:
        raise ProfileError("No memory data in profile (run with -memory on)")
    ranked = sorted(mem_rows, key=lambda row: -row.fn.get("PeakMem", 0))
    print(f"{'peak(MB)':>10}  {'alloc(MB)':>10}  {'freed(MB)':>10}  function")
    for fn, label in ranked[:top]:
        print(
            f"{fn.get('PeakMem', 0) / MB:>10.1f}  "
            f"{fn.get('AllocatedMemory', 0) / MB:>10.1f}  "
            f"{fn.get('FreedMemory', 0) / MB:>10.1f}  {label}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("profile_json", help="Path to profile.json")
    parser.add_argument("--top", type=int, default=15, help="How many to show (default 15)")
    parser.add_argument(
        "--by",
        choices=["time", "memory", "calls"],
        default="time",
        help="Sort key (default time)",
    )
    parser.add_argument(
        "--filter",
        metavar="SUBSTR",
        help="Keep only functions whose name contains any comma-separated "
        "substring (case-insensitive)",
    )
    args = parser.parse_args()

    with pathlib.Path(args.profile_json).open() as f:
        data = json.load(f)
    try:
        rows = select_functions(data["Functions"], args.filter)
        if args.by == "memory":
            print_by_memory(rows, args.top)
        else:
            print_by_time_or_calls(rows, args.top, args.by)
    except ProfileError as e:
        print(e, file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
