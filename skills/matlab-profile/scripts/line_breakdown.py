#!/usr/bin/env python
"""Show per-line breakdown for a function in a MATLAB profile.

Prints lines sorted by time, with hit counts and per-hit microseconds. Reads
ExecutedLines from the profile.json export. If no function name is given,
shows the breakdown for the top-N hottest functions.

Usage:
    line_breakdown.py <profile.json> [<function>] [--top N] [--lines M]
"""

import argparse
import json
import sys


def show_function(fn, max_lines):
    el = fn.get("ExecutedLines") or []
    if not el:
        return False
    lines = sorted(el, key=lambda l: -l["time"])
    print(f"\n{fn['FunctionName']} -- {fn['TotalTime']:.3f}s, {fn['NumCalls']} calls")
    print(f"  {'line':>6}  {'hits':>9}  {'time(s)':>10}  {'us/hit':>10}")
    for l in lines[:max_lines]:
        u = 1e6 * l["time"] / l["hits"] if l["hits"] else 0
        print(f"  {l['line']:>6}  {l['hits']:>9}  {l['time']:>10.3f}  {u:>10.2f}")
    return True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("profile_json", help="Path to profile.json")
    parser.add_argument("function", nargs="?", help="Function name (default: top-N hottest)")
    parser.add_argument("--top", type=int, default=5, help="How many functions when no name given (default 5)")
    parser.add_argument("--lines", type=int, default=10, help="Lines per function to show (default 10)")
    args = parser.parse_args()

    with open(args.profile_json) as f:
        data = json.load(f)
    fns = data["Functions"]

    if args.function:
        matches = [fn for fn in fns if fn["FunctionName"] == args.function]
        if not matches:
            print(f"Function not found: {args.function}", file=sys.stderr)
            print("Available functions (by time):", file=sys.stderr)
            for fn in sorted(fns, key=lambda f: -f["TotalTime"])[:10]:
                print(f"  {fn['FunctionName']}", file=sys.stderr)
            sys.exit(1)
        if not show_function(matches[0], args.lines):
            print(f"No line-level data for {args.function}", file=sys.stderr)
            sys.exit(1)
    else:
        sorted_fns = sorted(fns, key=lambda f: -f["TotalTime"])
        shown = 0
        for fn in sorted_fns:
            if show_function(fn, args.lines):
                shown += 1
                if shown >= args.top:
                    break


if __name__ == "__main__":
    main()
