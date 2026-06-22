#!/usr/bin/env python
"""Compare two MATLAB profile runs by function time.

Prints a before/after table showing TotalTime per function, the delta, and
the percentage change. Reads two profile.json files produced by the
MATLAB-side export.

Usage:
    compare_runs.py <before.json> <after.json> [--top N]
"""

import argparse
import json


def load_profile(path):
    with open(path) as f:
        d = json.load(f)
    return {fn["FunctionName"]: fn for fn in d["Functions"]}, d.get("Functions", [])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("before", help="Path to baseline profile.json")
    parser.add_argument("after", help="Path to current profile.json")
    parser.add_argument("--top", type=int, default=20, help="How many to show (default 20)")
    args = parser.parse_args()

    prev, _ = load_profile(args.before)
    curr, _ = load_profile(args.after)

    all_fns = sorted(
        set(prev) | set(curr),
        key=lambda n: -curr.get(n, {}).get("TotalTime", 0),
    )

    prev_total = sum(fn["TotalTime"] for fn in prev.values())
    curr_total = sum(fn["TotalTime"] for fn in curr.values())
    delta_total = curr_total - prev_total
    pct_total = 100 * delta_total / prev_total if prev_total > 0 else 0
    print(
        f"Total: {prev_total:.3f}s -> {curr_total:.3f}s "
        f"(delta {delta_total:+.3f}s, {pct_total:+.1f}%)"
    )
    print()

    print(
        f"{'function':<30}  {'before':>10}  {'after':>10}  {'delta':>10}  {'change':>8}"
    )
    for name in all_fns[: args.top]:
        p = prev.get(name, {})
        c = curr.get(name, {})
        t_prev = p.get("TotalTime", 0)
        t_curr = c.get("TotalTime", 0)
        delta = t_curr - t_prev
        if t_prev > 0 and t_curr > 0:
            pct_str = f"{100*delta/t_prev:+.1f}%"
        elif t_curr > 0:
            pct_str = "new"
        elif t_prev > 0:
            pct_str = "gone"
        else:
            pct_str = ""
        print(
            f"{name:<30}  {t_prev:>10.3f}  {t_curr:>10.3f}  "
            f"{delta:>+10.3f}  {pct_str:>8}"
        )


if __name__ == "__main__":
    main()
