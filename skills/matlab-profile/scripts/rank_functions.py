#!/usr/bin/env python
"""Rank MATLAB profile functions by inclusive time.

Prints a table of the top-N functions sorted by TotalTime (inclusive), with
call count and per-call microseconds. Reads a profile.json produced by the
MATLAB-side export in scripts/export_profile_json.m.

Usage:
    rank_functions.py <profile.json> [--top N] [--by time|memory|calls]
"""

import argparse
import json
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("profile_json", help="Path to profile.json")
    parser.add_argument("--top", type=int, default=15, help="How many to show (default 15)")
    parser.add_argument(
        "--by",
        choices=["time", "memory", "calls"],
        default="time",
        help="Sort key (default time)",
    )
    args = parser.parse_args()

    with open(args.profile_json) as f:
        data = json.load(f)
    fns = data["Functions"]

    if args.by == "time":
        fns = sorted(fns, key=lambda f: -f["TotalTime"])
        print(f"{'time(s)':>10}  {'calls':>9}  {'us/call':>12}  function")
        for fn in fns[: args.top]:
            c = int(fn["NumCalls"])
            u = 1e6 * fn["TotalTime"] / c if c else 0
            print(f"{fn['TotalTime']:>10.3f}  {c:>9d}  {u:>12.2f}  {fn['FunctionName']}")
    elif args.by == "memory":
        mem_fns = [f for f in fns if f.get("PeakMem")]
        if not mem_fns:
            print("No memory data in profile (run with -memory on)", file=sys.stderr)
            sys.exit(1)
        mem_fns = sorted(mem_fns, key=lambda f: -f.get("PeakMem", 0))
        print(f"{'peak(MB)':>10}  {'alloc(MB)':>10}  {'freed(MB)':>10}  function")
        for fn in mem_fns[: args.top]:
            print(
                f"{fn['PeakMem']/1e6:>10.1f}  "
                f"{fn['AllocatedMemory']/1e6:>10.1f}  "
                f"{fn['FreedMemory']/1e6:>10.1f}  {fn['FunctionName']}"
            )
    elif args.by == "calls":
        fns = sorted(fns, key=lambda f: -f["NumCalls"])
        print(f"{'calls':>9}  {'time(s)':>10}  {'us/call':>12}  function")
        for fn in fns[: args.top]:
            c = int(fn["NumCalls"])
            u = 1e6 * fn["TotalTime"] / c if c else 0
            print(f"{c:>9d}  {fn['TotalTime']:>10.3f}  {u:>12.2f}  {fn['FunctionName']}")


if __name__ == "__main__":
    main()
