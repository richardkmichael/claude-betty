---
name: matlab-profile
description: >
  Investigate MATLAB performance problems: time hotspots, memory consumption,
  parallelism, call patterns. Configures the profiler, runs code under
  instrumentation, analyzes results across multiple passes, and reports
  findings with automatic before/after comparison. Use this skill when the
  user says "profile", "matlab profile", "why is this slow", "memory usage",
  "hotspot", "performance", "where is time going", "bottleneck", or invokes
  /matlab-profile. Also use when the user asks to optimize MATLAB code and
  you need to measure before changing.
argument-hint: "[target] [file.m]"
allowed-tools: Bash(matlab *) Bash(python *) Bash(mkdir *) Read Write Glob Grep
---

# MATLAB Performance Investigation

Investigate performance problems in the user's MATLAB code. The profiler is a
diagnostic tool — choose the right options and passes based on what you are
trying to learn. Iterate to build a complete picture, then report findings.
Do the investigation; the user decides what to change.

Read `${CLAUDE_SKILL_DIR}/references/PROFILER_REFERENCE.md` at the start of
every investigation for profiler options, output struct schema, and
interpretation rules.

## Tools

All scripts are in `${CLAUDE_SKILL_DIR}/scripts/`. Invoke them directly; they
are executable.

Analysis (take `profile.json`):
- `rank_functions.py <profile.json> [--top N] [--by time|memory|calls]`
- `line_breakdown.py <profile.json> [<function>] [--top N] [--lines M]`
- `compare_runs.py <before.json> <after.json> [--top N]`

MATLAB-side helpers (inline into `matlab -batch` commands or run via
`matlab -batch "run('...'); ..."`):
- `export_profile_json.m` — function `export_profile_json(info, outPath)`,
  serialises `profile('info')` to JSON.
- `history_query.m` — function `events = history_query(matPath[, fnNames])`,
  flattens FunctionHistory for call-pattern analysis.

Documentation lookup: use the `matlab-docs` skill when an unfamiliar
function appears in results.

## Constraints

- Do NOT optimize or refactor the user's code. The skill profiles and reports;
  the user decides what to change. Temporary instrumentation (profiler guards,
  tic/toc, input-capture saves for snapshot-and-replay) is fine, but revert
  it after the investigation or make the changes in a copy.
- Do NOT hardcode project-specific function names or fixture paths.
- Do NOT fabricate profiler options. If unsure whether an option exists, probe
  with `profile('status')` first.

## Run management

All profile artefacts go in a working directory:

```
profile_runs/
├── manifest.json
├── last                ← text file containing the most recent run ID
├── Foo_001/
│   ├── profile.json
│   ├── profile.mat
│   └── profile_html/
└── Foo_002/
    └── ...
```

Manifest entry shape:

```json
{
  "id": "Foo_001",
  "target": "Foo.m",
  "label": "initial time profile",
  "timestamp": "2026-04-16T16:23:00",
  "settings": "-detail builtin -historysize 2e7",
  "concern": "time",
  "wall_time": 12.345,
  "working_tree_diff": "Foo.m | 3 ++-\n1 file changed"
}
```

Rules:
1. At invocation start, load `manifest.json` if it exists and read `last` to
   find the baseline for comparison.
2. Derive a run ID that captures what distinguishes this run from others the
   user is likely to do. See "Run ID naming" below — avoid generic entry-point
   names like `Solver` or `main` unless that really is the distinguishing
   feature. Append a sequence number (`_001`, `_002`, ...).
3. Resolve the sequence number against BOTH the manifest and the filesystem.
   Take the max of (highest sequence number in the manifest for this slug)
   and (highest sequence number in `profile_runs/<slug>_*` directories).
   Then add 1. This handles cases where the manifest and filesystem drift —
   user deleted a run directory manually, manifest was lost, or runs were
   added from elsewhere. Never overwrite an existing directory.
4. Before creating the run directory, check that the full path
   `profile_runs/<run_id>/` does not exist. If it does (should not happen
   after rule 3, but defensively), increment the sequence number until
   you find a free slot.
5. Derive a short human-readable label from the user's prompt.
6. Capture `git diff --stat` at run time — the code is likely uncommitted, so
   git hash is not a reliable identifier. If a baseline run exists, also
   capture `git diff -- <target_files>` to show the actual code changes.
7. Save artefacts into `profile_runs/<run_id>/`, update the manifest,
   update `last` to the new run ID.
8. To start fresh without comparison, the user deletes `last`. To compare
   against a specific older run, the user edits `last` or says so explicitly.

### Run ID naming

The run ID should identify what makes this run distinct, not just the
top-level entry point. If the user repeatedly profiles the same entry point
with different inputs, naming by entry point gives `Solver_001`, `Solver_002`,
... — uninformative. Name by what varies.

Rules of thumb:

- If the user invokes with a specific function or file target (e.g.,
  `/matlab-profile function foo in Bar.m`): use the function/file as the
  slug (`foo_001`).
- If the user profiles an entry point with a specific dataset or fixture
  (e.g., "profile the solver on case7.json", "profile Solver with simulated
  noisy data"): use the dataset/fixture as the slug (`case7_001`, `noisy_001`), not
  the entry point.
- If the user describes a mode or scenario ("profile in fast mode",
  "profile with memory tracking"): use the mode/scenario (`fast_001`,
  `memtrack_001`).
- If nothing clearly varies and the user just wants to profile the main
  entry point: fall back to the entry point name.

When multiple things vary (dataset AND mode), combine: `case7_fast_001`.
Err toward informative over short. If unsure what's distinctive, ask the
user briefly before naming: "I'll name this run `<id>` — does that fit
how you'll distinguish it from other runs?"

## Phase 1 — Understand the concern and plan the investigation

The user's prompt tells you what to investigate. Arguments are in `$ARGUMENTS`
(the full text after `/matlab-profile`). Common forms:

- `/matlab-profile function foo_bar in Foo.m` — profile a specific function.
  Read the file, find the function, wrap a call to it under the profiler.
  Ask for representative inputs if you cannot infer them.
- `/matlab-profile lines 100-200 in Bar.m` — profile a specific code region.
  Extract the lines, wrap them with necessary setup.
- `/matlab-profile Foo.m` — profile a script or main function.
- `/matlab-profile svd(rand(500))` — profile a standalone expression.
- `/matlab-profile` (no arguments) — ask what to investigate.

If the invocation specifies the target, proceed directly. Ask only when you
genuinely need more information.

Identify the concern from the user's prompt and choose profiler settings:

| Concern                        | Profiler settings                              | Analysis focus                           |
|--------------------------------|------------------------------------------------|------------------------------------------|
| "slow", "time", "hotspot"      | -detail builtin, -historysize 2e7              | TotalTime ranking, per-line timing       |
| "memory", "allocation", "leak" | -detail builtin, -historysize 2e7, -memory on  | PeakMem, AllocatedMemory per function    |
| "parallel", "CPU utilization"  | Two runs: -timer performance then -timer cpu   | Compare wall-clock vs CPU time           |
| "call pattern", "why called"   | -detail builtin, -historysize 2e7, -timestamp  | FunctionHistory entry/exit trace         |
| General "profile this"         | -detail builtin, -historysize 2e7              | Broad first pass; follow what stands out |

When the concern is unclear, start with a broad time pass. Anomalies in the
first pass (wall ≫ CPU, unexpectedly high call counts) suggest what to probe
next.

Ask about expected runtime. Confirm before launching anything over 120 seconds.

### Handling short workloads

If the target runs in under a second, per-call profiler overhead dominates and
relative timing is distorted. Wrap the target in a reps loop so total wall
time is at least a few seconds:

```matlab
for rep = 1:10
    <USER_CODE>;
end
```

Note the reps count so the user can divide. For sub-millisecond operations,
also run an off-profiler `tic`/`toc` benchmark as a cross-check. The profiler
inflates per-call cost (observed: ~5x for tiny ops) but preserves relative
ordering within a single profile.

## Phase 2 — Run the profiler

Generate a `matlab -batch` command that adds paths, configures the profiler,
runs the code, and saves artefacts. Call `export_profile_json` to write the
JSON. Template:

```bash
matlab -nodisplay -nodesktop -nosplash -batch "
  addpath(genpath('.'));
  addpath('${CLAUDE_SKILL_DIR}/scripts');
  profile clear;
  profile('on', '-detail', 'builtin', '-historysize', 2e7);
  t0 = tic;
  <USER_CODE>;
  wall = toc(t0);
  profile off;
  info = profile('info');
  fprintf('Wall time: %.3fs\n', wall);
  fprintf('FunctionTable rows: %d\n', numel(info.FunctionTable));
  fprintf('FunctionHistory events: %d\n', size(info.FunctionHistory, 2));
  export_profile_json(info, '<RUN_DIR>/profile.json');
  save('<RUN_DIR>/profile.mat', 'info', '-v7.3');
  profsave(info, fullfile(pwd, '<RUN_DIR>', 'profile_html'));
"
```

Pass profsave an absolute path (via `fullfile(pwd, ...)`, not a relative
`'<RUN_DIR>/profile_html'`). profsave's last action is
`web(['file:///' fullfile(dirname,'file0.html')],'-browser')`; with a
relative `dirname`, that URL parses as `/<dirname>/file0.html` (absolute
from root, doesn't exist) and fails in -batch mode with a misleading
"file does not exist" error. Files are written correctly either way, but
the absolute-path form avoids the spurious error.

For memory profiling, DO NOT append `-memory on` to a `profile('on', ...)` call —
that errors with "Only one profiler action is supported per call." Instead,
use a configure-only invocation that enables memory, then start with a separate
`profile on`:

```matlab
profile('-memory', 'on', '-detail', 'builtin', '-historysize', 2e7);
profile on;
% ... user code ...
profile off;
```

The exporter captures memory fields (`PeakMem`, `TotalMemAllocated`,
`TotalMemFreed`) automatically when the struct contains them.

After the run: update the manifest, check FunctionHistory event count against
the historysize cap, check for errors or warnings, tell the user the run path.

## Phase 3 — Analyze and iterate

Use the analysis scripts against the JSON. Examples:

```bash
# Top functions by inclusive time
${CLAUDE_SKILL_DIR}/scripts/rank_functions.py profile_runs/Foo_001/profile.json --top 15

# Per-line breakdown of the hottest function
${CLAUDE_SKILL_DIR}/scripts/line_breakdown.py profile_runs/Foo_001/profile.json

# Per-line breakdown of a specific function
${CLAUDE_SKILL_DIR}/scripts/line_breakdown.py profile_runs/Foo_001/profile.json myHotFunction

# Memory ranking (requires -memory on)
${CLAUDE_SKILL_DIR}/scripts/rank_functions.py profile_runs/Foo_001/profile.json --by memory

# Compare against baseline
${CLAUDE_SKILL_DIR}/scripts/compare_runs.py profile_runs/Foo_001/profile.json \
                                            profile_runs/Foo_002/profile.json
```

For FunctionHistory call-pattern queries, run `history_query.m` via MATLAB:

```bash
matlab -nodisplay -nodesktop -nosplash -batch "
  addpath('${CLAUDE_SKILL_DIR}/scripts');
  history_query('profile_runs/Foo_001/profile.mat', 'myHotFunction');
"
```

Patterns to watch for:
- Functions with Type "Built-in" high in the ranking → time in LAPACK or runtime
- High NumCalls but low TotalTime → dispatch overhead, not algorithmic cost
- One or two lines dominating → pinpoint optimization targets
- ExecutedLines sum much less than TotalTime → hidden built-in cost (use -detail builtin)
- Large AllocatedMemory with small FreedMemory → potential accumulation or leak
- Wall time much larger than CPU time → I/O bound or waiting on external resources
- CPU time much larger than wall time → good BLAS parallelism

### When to look up a function

When an unfamiliar MATLAB function appears in the hotspot list, use the
`matlab-docs` skill to fetch its documentation. Use the docs to
understand what the function does, whether a better alternative
is recommended for the observed pattern (e.g., `griddedInterpolant` instead of
repeated `interp1` calls), and what options it supports.

### Benchmarking alternatives

When the hotspot is a specific MATLAB pattern, don't stop at reporting the
bottleneck — write a short off-profiler benchmark comparing the current
approach against plausible alternatives. Save as
`bench_<description>.m`. Verify outputs match before trusting
speedup numbers. This turns a report into an actionable recommendation.

Template:

```matlab
N = 10;  % reps
t = tic; for i=1:N, <CURRENT>; end; tA = toc(t) / N;
t = tic; for i=1:N, <ALT1>; end; tB = toc(t) / N;
t = tic; for i=1:N, <ALT2>; end; tC = toc(t) / N;
fprintf('A: %.3f ms, B: %.3f ms (%.1fx), C: %.3f ms (%.1fx)\n', ...
    tA*1e3, tB*1e3, tA/tB, tC*1e3, tA/tC);
```

### Comparing with the baseline

If `last` exists, compare automatically with `compare_runs.py`. Report:

- Overall wall time delta
- Functions that improved, regressed, appeared, or disappeared
- What code changed between runs (git diff stat from the manifest)
- Prominent flag if anything regressed

### Autonomous iteration

After each pass, examine the results for signals that demand a follow-up
pass. When a signal fires, run the next pass automatically (within the
check-in constraints below) — do not stop and ask. The goal is a complete
picture by the time you report, not a ping-pong of "do you want me to
look at X?" questions.

Signals that trigger an automatic follow-up pass:

- History buffer filled → rerun with larger -historysize (same workload,
  bigger buffer).
- ExecutedLines sum significantly less than TotalTime and -detail builtin
  wasn't used → rerun with -detail builtin (same workload, more detail).
- Wall time much larger than CPU time, or CPU ≫ wall → run a parallelism
  pass (-timer cpu) to quantify what's happening.
- Time profile shows a hot function with many allocation-looking built-ins
  in its ExecutedLines (zeros, ones, repmat, cat, sparse in a loop; or
  high-call-count operations on growing arrays) → run a memory pass with
  -memory on to confirm and quantify.
- One function dominates (>70% of total time) and its internal line
  attribution doesn't explain where the time goes → snapshot-and-replay
  that function in isolation for a focused profile.
- User asked about memory but only time was run → run a memory pass.
- User asked about parallelism but only wall time was measured → run a
  CPU-time pass.
- Memory pass shows large allocations in a function that's also a time
  hotspot → you already have the cross-reference; just report it.

Check in with the user before proceeding automatically when:
- The next run will take more than 120 seconds.
- You've already run 2+ follow-up passes without the picture converging
  (means the evidence is ambiguous; the user should choose).
- Multiple signals fire at once and none dominates — picking which to
  chase first is a judgment call.

Report the chained passes in one consolidated final report, not piecewise
after each one. The user wanted an answer, not a blow-by-blow.

### Deciding between autonomous follow-up and asking

After an analysis pass surfaces multiple plausible next steps, decide
whether to pick one autonomously or ask the user first.

Proceed autonomously when one direction clearly dominates:
- The first pass already answers the user's question and the follow-ups
  are nice-to-have polish
- One signal in the data points unambiguously at a single next pass
  (e.g., history buffer overflowed — just rerun with a larger buffer;
  ExecutedLines sum doesn't match TotalTime — rerun with -detail builtin)
- The user's prompt was specific enough that the follow-up direction is
  obvious (they asked about memory and you only ran a time pass — go run
  the memory pass)

Ask the user when multiple directions are roughly equally promising and
none is implied by the prompt:
- Three or more possible follow-up passes each addressing a different
  dimension (time drill-down, memory, parallelism, call-pattern)
- The data shows multiple signals (e.g., both a time hotspot AND
  unexplained wall/CPU gap — either could be worth investigating first)
- A non-trivial scoping choice (which subfunction to snapshot for replay,
  which fixture to rerun with)

How to ask: present the options as a numbered list in the report's
"Next steps" section, with a short description of what each would reveal
and roughly how long it would take. Then stop and wait for the user to
pick one (or say "none" / "all" / something else). Do not invent a
decision just to keep moving.

If the user asked "why is this slow?", don't just report time hotspots —
check whether memory pressure, poor parallelism, or excessive call counts
are contributing. Follow the evidence.

## Phase 4 — Report

1. Investigation summary: what was profiled, passes run, settings, wall time,
   buffer status, run path.
2. Findings table adapted to the measurement (time, memory, or comparison).
3. Before/after comparison prominently shown if a baseline existed.
4. Per-line detail for hotspots. Read the source at those lines and explain
   what the code is doing, not just report line numbers.
5. Surprises: anything the first pass didn't predict (built-in dominance,
   unexpected call counts, wall/CPU mismatch, history overflow, regressions
   in untouched functions).
6. Diagnosis: connect findings to the user's concern. Synthesize across passes.
7. Actionable next steps. Be specific about what to investigate or change.
   Include alternatives benchmarks when you ran them. Do not make code changes.
8. Run path for interactive exploration: "Open
   `profile_runs/<run_id>/profile_html/index.html` in a browser."

## Cleanup

The `profile_runs/` directory accumulates across runs. Mention its
total size periodically. When the user is done, offer to clean up, but don't
delete without asking.
