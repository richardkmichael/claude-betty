#!/usr/bin/env python3
"""Compare two MATLAB profile runs, by function time or by allocated memory.

By default the summary line is the top-level WallTime delta (the tic/toc around
the workload), followed by a before/after table of inclusive TotalTime per
function with the delta and percentage change. With --by memory it compares
AllocatedMemory per function (in MB) and summarizes total allocation and peak;
this requires both runs to have been profiled with -memory on. Reads two
profile.json files produced by the MATLAB-side export in
scripts/export_profile_json.m.

Usage:
    compare_runs.py <before.json> <after.json> [--top N] [--by time|memory]
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from function_labels import FunctionRow, labelled

MB = 1e6

# The driver template (SKILL.md, Phase 2) runs the profiled code inside a local function,
# workload(), so this row encloses the whole run and nothing else.
WORKLOAD_FUNCTION = "run_profile>workload"

# Settings that change what the per-function numbers measure. Two runs that differ in
# any of them are not a like-for-like comparison.
COMPARED_SETTINGS = ("Timer", "DetailLevel", "Memory", "Threads")

# A whole profile export, and its functions keyed by label.
Profile = dict[str, Any]
Functions = dict[str, FunctionRow]


class ProfileError(Exception):
    """The runs cannot be compared as asked; main() reports the message and exits 1."""


def pct(delta: float, base: float) -> float:
    return 100 * delta / base if base > 0 else 0


def load_profile(path: str) -> Profile:
    # Return the whole profile dict so callers can read top-level fields
    # (WallTime) as well as the per-function table.
    with open(path) as f:
        return json.load(f)


def function_maps(prev: Profile, curr: Profile) -> tuple[Functions, Functions]:
    """Label both runs' rows against each other, so a function keeps one label in both."""
    return (
        labelled(prev["Functions"], curr["Functions"]),
        labelled(curr["Functions"], prev["Functions"]),
    )


def has_memory(profile: Profile) -> bool:
    """Whether a run was profiled with -memory on: every row then carries PeakMem."""
    return any("PeakMem" in fn for fn in profile["Functions"])


def value(fns: Functions, name: str, field: str) -> float:
    """A function's figure in one run, 0 when the run lacks the function or the field."""
    return fns.get(name, {}).get(field, 0)


def ranked_names(prev_fns: Functions, curr_fns: Functions, field: str) -> list[str]:
    """Functions of either run, largest first by the larger of their two figures, so a
    function that disappeared stays as visible as one that appeared. Ties go by name, so
    the table is the same on every run."""
    names = set(prev_fns) | set(curr_fns)
    return sorted(
        names, key=lambda n: (-max(value(prev_fns, n, field), value(curr_fns, n, field)), n)
    )


def change_label(before: float, after: float) -> str:
    if before > 0 and after > 0:
        return f"{pct(after - before, before):+.1f}%"
    if after > 0:
        return "new"
    if before > 0:
        return "gone"
    return ""


def comparable_settings(profile: Profile) -> dict[str, Any]:
    """The settings that matter for comparison. Exports made before Settings recorded
    Memory lack it, so derive it from the rows for those. Threads (the session's
    maxNumCompThreads) leaves no trace in the rows, so an export without it stays unknown."""
    settings = dict(profile.get("Settings") or {})
    settings.setdefault("Memory", "on" if has_memory(profile) else "off")
    return settings


def warn_on_settings_mismatch(prev: Profile, curr: Profile) -> None:
    """Warn when the runs are known to differ in a compared setting. A setting one run did
    not record is unknown, not different: say so without claiming a mismatch."""
    prev_settings = comparable_settings(prev)
    curr_settings = comparable_settings(curr)
    for name in COMPARED_SETTINGS:
        before, after = prev_settings.get(name), curr_settings.get(name)
        if before == after:
            continue
        if before is None or after is None:
            print(
                f"note: a run did not record {name} ({before} -> {after}); "
                "whether the runs differ in it is unknown",
                file=sys.stderr,
            )
        else:
            print(
                f"warning: the runs differ in {name} ({before} -> {after}); "
                "their numbers are not directly comparable",
                file=sys.stderr,
            )


def compare_time(prev: Profile, curr: Profile, top: int) -> None:
    prev_fns, curr_fns = function_maps(prev, curr)
    names = ranked_names(prev_fns, curr_fns, "TotalTime")

    # The summary line reports the top-level WallTime (the tic/toc around the
    # workload), the one wall-clock number that is not double-counted.
    # Per-function TotalTime is inclusive, so summing the whole table multiplies
    # time by call depth. The table rows below keep inclusive TotalTime, but the
    # total must not be a sum of it.
    prev_wall = prev.get("WallTime")
    curr_wall = curr.get("WallTime")
    if prev_wall is not None and curr_wall is not None:
        delta = curr_wall - prev_wall
        print(
            f"Wall time: {prev_wall:.3f}s -> {curr_wall:.3f}s "
            f"(delta {delta:+.3f}s, {pct(delta, prev_wall):+.1f}%)"
        )
    else:
        print("Wall time: unavailable (a profile predates the WallTime field)")
    print()
    print(f"{'function':<30}  {'before':>10}  {'after':>10}  {'delta':>10}  {'change':>8}")
    for name in names[:top]:
        b = value(prev_fns, name, "TotalTime")
        a = value(curr_fns, name, "TotalTime")
        print(f"{name:<30}  {b:>10.3f}  {a:>10.3f}  {a - b:>+10.3f}  {change_label(b, a):>8}")


def run_total(fns: Functions, field: str) -> float:
    """The run's figure for an inclusive memory field.

    AllocatedMemory is inclusive, so an enclosing frame already holds the run
    total; never sum the column, which would multiply by call depth. PeakMem is
    summarised the same way. Verified empirically: a thin wrapper that only
    calls an allocating leaf reports the leaf's full allocation, not zero.

    Take the workload() row when the driver has one. The driver script's own row
    also encloses it, but adds the profiler's shutdown (profile off,
    profile('info')): measured at 20 MB of a 23 MB total on a small workload.
    Without a workload() row, fall back to the largest row.
    """
    workload = fns.get(WORKLOAD_FUNCTION)
    if workload is not None:
        return workload.get(field, 0)
    return max((fn.get(field, 0) for fn in fns.values()), default=0)


def total_change(
    prev_fns: Functions, curr_fns: Functions, field: str
) -> tuple[float, float, float, float]:
    """(before, after, delta, percentage) of the run totals of a memory field."""
    prev_val = run_total(prev_fns, field)
    curr_val = run_total(curr_fns, field)
    delta = curr_val - prev_val
    return prev_val, curr_val, delta, pct(delta, prev_val)


def compare_memory(prev: Profile, curr: Profile, top: int, paths: tuple[str, str]) -> None:
    # Both runs need memory data: against a time-only run every function would read as
    # freed to zero.
    missing = [path for path, profile in zip(paths, (prev, curr)) if not has_memory(profile)]
    if missing:
        raise ProfileError(
            f"No memory data in {' and '.join(missing)} (profile both runs with -memory on)"
        )
    prev_fns, curr_fns = function_maps(prev, curr)
    names = ranked_names(prev_fns, curr_fns, "AllocatedMemory")

    prev_alloc, curr_alloc, d_alloc, apct = total_change(prev_fns, curr_fns, "AllocatedMemory")
    prev_peak, curr_peak, d_peak, ppct = total_change(prev_fns, curr_fns, "PeakMem")
    print(
        f"Total allocated (inclusive): {prev_alloc / MB:.1f} MB -> {curr_alloc / MB:.1f} MB "
        f"(delta {d_alloc / MB:+.1f} MB, {apct:+.1f}%)"
    )
    print(
        f"Peak (inclusive):            {prev_peak / MB:.1f} MB -> {curr_peak / MB:.1f} MB "
        f"(delta {d_peak / MB:+.1f} MB, {ppct:+.1f}%)"
    )
    print()
    print(f"{'function':<30}  {'before_MB':>10}  {'after_MB':>10}  {'delta_MB':>10}  {'change':>8}")
    for name in names[:top]:
        b = value(prev_fns, name, "AllocatedMemory") / MB
        a = value(curr_fns, name, "AllocatedMemory") / MB
        print(f"{name:<30}  {b:>10.1f}  {a:>10.1f}  {a - b:>+10.1f}  {change_label(b, a):>8}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("before", help="Path to baseline profile.json")
    parser.add_argument("after", help="Path to current profile.json")
    parser.add_argument("--top", type=int, default=20, help="How many to show (default 20)")
    parser.add_argument(
        "--by",
        choices=["time", "memory"],
        default="time",
        help="Compare by inclusive time (default) or allocated memory",
    )
    args = parser.parse_args()

    prev = load_profile(args.before)
    curr = load_profile(args.after)
    warn_on_settings_mismatch(prev, curr)
    try:
        if args.by == "memory":
            compare_memory(prev, curr, args.top, (args.before, args.after))
        else:
            compare_time(prev, curr, args.top)
    except ProfileError as e:
        print(e, file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
