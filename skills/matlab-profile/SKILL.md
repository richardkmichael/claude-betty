---
name: matlab-profile
description: >
  Measure where time and memory go in MATLAB code: runs the MATLAB profiler (time, memory,
  CPU against wall-clock time, call history) over one or more passes, ranks the hotspot
  functions and lines, benchmarks alternatives with timeit, and compares each run with the
  previous one. Use it whenever the user wants to know why MATLAB code (a .m function, script,
  or test) is slow, takes too much
  memory, or which function or line is the bottleneck, even if they never say "profile"; and
  before optimising MATLAB code, to measure first. Not for Python, C or other non-MATLAB
  code, and not for looking up MATLAB documentation (use matlab-docs).
argument-hint: "[target] [file.m]"
allowed-tools:
  - Bash(matlab *)
  - Bash(${CLAUDE_SKILL_DIR}/scripts/rank_functions.py *)
  - Bash(${CLAUDE_SKILL_DIR}/scripts/line_breakdown.py *)
  - Bash(${CLAUDE_SKILL_DIR}/scripts/compare_runs.py *)
  - Bash(${CLAUDE_SKILL_DIR}/scripts/profile_run.py *)
  - Bash(du -sh *)
  - Read
  - Write
  - Glob
  - Grep
metadata:
  version: "1.0"
---

# MATLAB performance investigation

Investigate performance problems in the user's MATLAB code. The profiler is a diagnostic tool:
choose its options and passes for what you are trying to learn, iterate until the picture is
complete, then report. Do the investigation; the user decides what to change.

The skill targets MATLAB R2025b. It relies on profiler behaviour verified there, some of it
undocumented (`-detail builtin`, the `-memory on` field names), and is unverified on earlier
releases. If the user's release is older, say that results may differ.

`${CLAUDE_SKILL_DIR}/references/PROFILER_REFERENCE.md` holds the profiler facts. Read the
section a step needs when you reach it, rather than the whole file:

- Before a memory pass: "Memory options".
- Before explaining figures to the user: "How the per-function numbers aggregate".
- Before a call-pattern pass: "FunctionHistory event matrix".
- When a function dominates or the overhead is large: "Focused-profile recipes".
- For any other option or output field: the table of contents at its top.

## Tools

All scripts are in `${CLAUDE_SKILL_DIR}/scripts/`. Run each Python script by its full path,
`${CLAUDE_SKILL_DIR}/scripts/<name>.py ...`: that form is pre-approved, and the bare names
below are short for it.

Analysis, each reading a `profile.json`:

- `rank_functions.py <profile.json> [--top N] [--by time|memory|calls] [--filter SUBSTR]`
- `line_breakdown.py <profile.json> [<function>] [--top N] [--lines M] [--by time|memory]`
- `line_breakdown.py <profile.json> <function> --all-lines` lists every line of the function
  in line order with hits, time and source, 0 hits where a line never ran, showing which
  branches ran and how often without changing the code. The profile holds only executed lines,
  so the extent is read from the source file: the `function` line at or before the first
  executed line (line 1 for a script) up to the next `function` line or the end of the file. A
  nested function ends its parent's extent early, and a missing or changed file is an error.
- `compare_runs.py <before.json> <after.json> [--top N] [--by time|memory]`

When two functions share a name (the same file name in two folders), the tools label each with
the end of its file path, such as `helper (a/helper.m)`.

Run catalog, which writes `index.json` and `last` in the runs directory:

- `profile_run.py record start <slug> [--runs-dir DIR]` allocates the next `<slug>_NNN`
  directory and prints its path.
- `profile_run.py record stop <run_dir> --target T --label L --concern C [--code-dir DIR]
  [--matlab-options=OPTS]` reads the run's `profile.json` for its settings and wall time,
  writes the index entry with the code directory (absolute; default the working directory) and
  the startup options (default none; the `=` form, as they start with a dash), and advances `last`.

MATLAB helpers, called from the driver script (Phase 2) or a one-line `matlab -batch`:

- `export_profile_json(info, status, wallTime, outPath)` writes `profile('info')` to JSON, with
  the `profile('status')` settings under `Settings` and the wall time under `WallTime`.
- `events = history_query(profileMatPath, fnNames)` flattens FunctionHistory into a table for
  call-pattern questions; `fnNames` is optional. Called without an output, it prints a count of
  calls per function.

`profile.json` holds a `Functions` array plus `Settings`, `WallTime`, `Name`,
`ClockPrecision`, `ClockSpeed` and `NumFunctions`. Each function has `FunctionName`,
`FileName`, `Type`, `NumCalls`, `TotalTime`, `PartialData` and `ExecutedLines`
(`[{line, hits, time}]`). A `-memory on` pass adds `PeakMem`, `AllocatedMemory` and
`FreedMemory` to each function and `allocated`, `freed` and `peak` to each line. The exporter
renames R2025b's `TotalMemAllocated` and `TotalMemFreed` to `AllocatedMemory` and
`FreedMemory`. `Settings` is the `profile('status')` result plus two things it does not
report: `Memory` (`'on'` or `'off'`) and `Threads` (`maxNumCompThreads` in the exporting
session, so export from the session that ran the workload).

## Constraints

- Do not change the user's code: the user decides what to change. Profiling, reporting and
  recommending are in scope, and so are scratch benchmarks that back a recommendation. Keep
  instrumentation and alternatives in scratch files under the run directory, and revert
  anything you add to the user's files.
- Use only profiler options documented in the reference or in MATLAB's documentation (the
  `matlab-docs` skill). `profile('status')` shows the current settings, not which options
  exist.

## Run management

All profile artefacts go in the runs directory, `<RUNS_DIR>`: `profile_runs/` in the
working directory unless the user names another (`record start --runs-dir`):

```
<RUNS_DIR>/
├── .gitignore          ← `*`; record start writes it so git ignores the catalog
├── index.json          ← one entry per run: id, target, label, timestamp, settings,
│                         concern, wall_time, code_dir, matlab_options
├── last                ← the most recent run ID
├── Foo_001/
│   ├── run_profile.m   ← profiler driver: setup, workload(), export
│   ├── profile.json
│   ├── profile.mat
│   └── profile_html/   ← written for the runs you report (Phase 4)
└── Foo_002/
    └── ...
```

Write `index.json` and `last` only through `profile_run.py`; read them directly.

1. At the start, read `index.json` and `last` if they exist. A baseline for comparison must
   match this pass in `target`, `concern`, `settings` (`Timer`, `DetailLevel`, `Memory`,
   `Threads`), `code_dir` and `matlab_options`, and its label must show the same warm-up
   and repetition count: a run that differs in any of these measures something else. An
   entry lacking a field (an older catalog) does not match. The baseline is `last` when it
   matches; otherwise the newest matching entry, or none. The one exception is `code_dir`
   when the user asks to compare two checkouts: before/after branches in two worktrees.
2. Choose a run slug (see "Run ID naming"; letters, digits, `.`, `_` and `-`) and allocate
   the run directory with `profile_run.py record start <slug>`. It prints the run
   directory's absolute path; capture it as `RUN_DIR`. Do not add the sequence number
   yourself.
3. Derive a short label from the user's prompt, and include how the workload ran: warmed up
   or cold, and the repetition count (for example "warm, 10 reps").
4. Run the profiler pass (Phase 2), writing artefacts into `RUN_DIR`.
5. Record the run with `profile_run.py record stop <RUN_DIR> --target <target>
   --label <label> --concern <concern> --code-dir <CODE_DIR>
   --matlab-options="<MATLAB_OPTIONS>"`, omitting the last two at their defaults.
6. To start fresh without a comparison, the user deletes `last`. To compare with an older
   run, the user edits `last` or says which run.

### Run ID naming

Name a run by what makes it distinct, not by the top-level entry point. Profiling one entry
point with different inputs and naming every run after it gives `runSimulation_001`,
`runSimulation_002`, which say nothing. Name by what varies:

- A specific function or file target (`/matlab-profile function foo in Bar.m`): the
  function or file (`foo_001`).
- An entry point run on a specific dataset or fixture ("profile the analysis on
  sampleData.mat"): the dataset (`sampleData_001`), not the entry point.
- A mode or scenario ("profile in GPU mode"): the mode (`gpu_001`).
- Nothing in particular varies: the entry point.

When several things vary, combine them: `sampleData_gpu_001`. Prefer informative over short.
If you are unsure what is distinctive, propose a name and ask whether it fits.

## Phase 1: Understand the concern and plan

Arguments given with the skill, if any: $ARGUMENTS

With none, read the target from the conversation. Common forms:

- `/matlab-profile function foo_bar in Foo.m`: profile one function. Read the file, find the
  function, and call it from `workload()`. Ask for representative inputs if you cannot infer
  them.
- `/matlab-profile lines 100-200 in Bar.m`: profile a region. Extract the lines with the
  setup they need.
- `/matlab-profile Foo.m`: profile a script or main function.
- `/matlab-profile svd(rand(500))`: profile an expression.
- No target anywhere: ask what to investigate.

When the target is clear, proceed. Ask only when you need information you cannot find.

Choose the profiler settings from the concern. Every pass uses `-detail builtin` and
`-historysize 2e7`; the concern adds to that:

- "slow", "time", "hotspot": nothing more. Analyse the TotalTime ranking and per-line time.
- "memory", "allocation", "leak": `-memory on`. Analyse allocated and peak memory per
  function and per line.
- "parallel", "CPU utilisation", threads: two passes, the default `-timer performance` and
  then `-timer cpu`. Compare each function's TotalTime between them.
- "call pattern", "why is this called": `-timestamp` (the default history mode). Analyse the
  FunctionHistory entry and exit sequence.
- A general "profile this": nothing more. A broad first pass; follow what stands out.

When the concern is unclear, start with a broad time pass and let its anomalies choose the
next pass (see "Follow-up passes").

Estimate the runtime from earlier `index.json` entries for the same target, or ask the user.
Confirm before starting anything expected to run longer than 120 seconds.

### Short workloads

On a fast target the profiler and the clock answer different questions. The profiler shows
where the time goes, but on a sub-second target its per-call overhead distorts both the
absolute times (observed: about 5x on tiny operations) and the proportions. The overhead
lands on instrumented MATLAB functions called many times, while built-ins that MATLAB
compiles inline, such as `rand` and `sum`, may not appear in the table at all, even with
`-detail builtin`. Observed: `interp1` called 50,000 times showed 87 % of a profile but
took about 50 % of the time by `timeit`. So profile to find candidates, and measure their
share with `timeit` before reporting it.

For attribution, repeat the target inside `workload()` so the profiler collects enough
samples, and record the repetition count in the run's label:

```matlab
function workload()
    for rep = 1:10
        <USER_CODE>;
    end
end
```

For the headline time, use `timeit` (`gputimeit` for GPU code) instead of the profiler or a
`tic`/`toc` loop. It warms the target up, calls it as often as needed, and returns the median:

```matlab
t = timeit(@() <USER_CODE>);        % seconds, median over many runs
% GPU: gputimeit(@() <USER_CODE>), which also waits for the GPU to finish
```

## Phase 2: Run the profiler

Write a driver script to `<RUN_DIR>/run_profile.m`, then run it:

```shell
matlab -nodisplay <MATLAB_OPTIONS> -batch "run('<RUN_DIR>/run_profile.m')"
```

Do not paste a multi-line body into `-batch "..."`: MATLAB rejects it with "No MATLAB command
specified for -batch command line argument". `-batch` already starts without the desktop or
splash screen; `-nodisplay` keeps figure windows from opening.

The placeholders, besides `${CLAUDE_SKILL_DIR}` and `<USER_CODE>`, every path absolute
(`run()` changes into the driver's own folder before running it, so `'.'` or a relative path
would resolve inside the run directory, and MATLAB would not find the user's code):

- `<CODE_DIR>`: the checkout holding the code to profile; the working directory unless the
  user names another, such as a worktree of a branch under comparison. The driver changes
  into it, so relative paths in the code resolve there.
- `<SETUP_FILE>`: the project's own path setup script, when it documents one; `run()` of it
  replaces the `addpath(genpath(...))` and `rmpath` lines.
- `<RUNS_DIR>`: the runs directory (Run management).
- `<MATLAB_OPTIONS>`: extra MATLAB startup options, `-singleCompThread` say; none by default
  and free-form, since the thread count MATLAB used is recorded in `Settings.Threads` anyway.

```matlab
cd('<CODE_DIR>');
addpath(genpath('<CODE_DIR>'));   % or run('<SETUP_FILE>'); see below
rmpath(genpath('<RUNS_DIR>'));    % only when <RUNS_DIR> lies inside <CODE_DIR>
addpath('${CLAUDE_SKILL_DIR}/scripts');
workload();   % warm-up; see below
profile clear;
profile('on', '-detail', 'builtin', '-historysize', 2e7);
t0 = tic;
workload();
walltime = toc(t0);
profile off;
info = profile('info');
status = profile('status');
fprintf('Wall time: %.3fs\n', walltime);
fprintf('FunctionTable rows: %d\n', numel(info.FunctionTable));
fprintf('FunctionHistory events: %d\n', size(info.FunctionHistory, 2));
export_profile_json(info, status, walltime, '<RUN_DIR>/profile.json');
save('<RUN_DIR>/profile.mat', 'info', 'status', '-v7.3');

function workload()
    <USER_CODE>;
end
```

- Keep the user's code in the local function `workload()`. Its row, `run_profile>workload`,
  encloses the whole workload and nothing else, so it holds the run's totals. The script's
  own row also encloses the profiler's shutdown, which adds megabytes to a memory pass.
- The warm-up call runs the workload once before profiling, so the profile does not include
  MATLAB compiling and loading the code on first use. In a fresh `matlab -batch` session the
  first call is always cold: observed 161 ms against 29 ms warm. Drop the warm-up when the
  first call is what the user asks about, or when the workload runs for minutes and the
  warm-up would double that. Say in the run's label which you did.
- `addpath(genpath('<CODE_DIR>'))` is the fallback path setup. When the project documents
  its own (a CLAUDE.md, a `startup.m`), use `run('<SETUP_FILE>')` instead: `genpath` also
  adds any other copy of the code under the code directory (agent worktrees, say), which
  can shadow the live functions. The `rmpath` line removes the skill's own copies in the runs
  directory (drivers, benchmarks) when it lies inside; else drop it, or `rmpath` warns.
- For a memory pass, replace the `profile('on', ...)` line with
  `profile('-memory', 'on', '-detail', 'builtin', '-historysize', 2e7);`, which configures
  and starts the profiler in one call. Adding `'-memory', 'on'` to `profile('on', ...)`
  fails with "Only one profiler action is supported per call." Expect it to run far slower
  than a time pass (observed: 130x on a loop of small calls), and estimate its runtime from
  that before the 120-second check in Phase 1.
- For a CPU-time pass, add `'-timer', 'cpu'` to the `profile('on', ...)` call.

After the pass:

1. Record it with `profile_run.py record stop` (Run management, step 5).
2. Check the FunctionHistory event count against the `-historysize` cap. Overflow is silent:
   the aggregates keep counting but the trace stops.
3. Check the MATLAB output for errors and warnings.
4. Tell the user the run path.

## Phase 3: Analyse and iterate

Run the analysis scripts on the JSON:

```shell
# Top functions by inclusive time
${CLAUDE_SKILL_DIR}/scripts/rank_functions.py <RUNS_DIR>/Foo_001/profile.json --top 15

# Only functions whose names contain a substring (comma-separated, case-insensitive)
${CLAUDE_SKILL_DIR}/scripts/rank_functions.py <RUNS_DIR>/Foo_001/profile.json \
    --filter sparse,mldivide

# Per-line breakdown of the hottest functions, of one function, or of every line of one
# function in line order with hits and source (which branches ran, and how often)
${CLAUDE_SKILL_DIR}/scripts/line_breakdown.py <RUNS_DIR>/Foo_001/profile.json
${CLAUDE_SKILL_DIR}/scripts/line_breakdown.py <RUNS_DIR>/Foo_001/profile.json myHotFunction
${CLAUDE_SKILL_DIR}/scripts/line_breakdown.py <RUNS_DIR>/Foo_001/profile.json myHotFunction \
    --all-lines

# Memory by function, and by line (needs a -memory on pass)
${CLAUDE_SKILL_DIR}/scripts/rank_functions.py <RUNS_DIR>/Foo_001/profile.json --by memory
${CLAUDE_SKILL_DIR}/scripts/line_breakdown.py <RUNS_DIR>/Foo_001/profile.json --by memory

# Compare with the baseline, by time or by memory
${CLAUDE_SKILL_DIR}/scripts/compare_runs.py <RUNS_DIR>/Foo_001/profile.json \
    <RUNS_DIR>/Foo_002/profile.json
${CLAUDE_SKILL_DIR}/scripts/compare_runs.py <RUNS_DIR>/Foo_001/profile.json \
    <RUNS_DIR>/Foo_002/profile.json --by memory
```

For call-pattern questions, query FunctionHistory through MATLAB:

```shell
matlab -nodisplay -batch "addpath('${CLAUDE_SKILL_DIR}/scripts'); \
    history_query('<RUNS_DIR>/Foo_001/profile.mat', 'myHotFunction')"
```

Patterns to look for:

- Built-in functions high in the ranking: the time is in LAPACK, BLAS or the runtime.
- A function with a high total time spread over many cheap calls (high NumCalls, low time
  per call): call overhead, not algorithmic cost. Fewer, larger calls would help.
- One or two lines dominating a function: the optimisation targets.
- Large AllocatedMemory with small FreedMemory: accumulation or a leak.
- After a `-timer cpu` pass, CPU time well above the wall-clock time of the same function:
  multithreaded built-ins at work. CPU time well below it: the function waits on I/O or
  something external.

### Looking up a function

When an unfamiliar MATLAB function appears in the hotspots, fetch its documentation with the
`matlab-docs` skill: what it does, what options it takes, and whether the documentation
recommends something else for the observed pattern (for example `griddedInterpolant`
instead of repeated `interp1` calls).

### Benchmarking alternatives

When the hotspot is a specific MATLAB pattern, do not stop at naming the bottleneck. Write a
short benchmark comparing the current code with plausible alternatives, save it as
`<RUN_DIR>/bench_<description>.m`, and check that the outputs match before trusting a speedup:

```matlab
tA = timeit(@() <CURRENT>);   % median seconds, warmup handled
tB = timeit(@() <ALT1>);
tC = timeit(@() <ALT2>);
fprintf('A: %.3f ms, B: %.3f ms (%.1fx), C: %.3f ms (%.1fx)\n', ...
    tA*1e3, tB*1e3, tA/tB, tC*1e3, tA/tC);
```

Treat a speedup under 1.2x as noise: below that, measurement variance dominates.

### Comparing with the baseline

When there is a baseline (Run management, step 1), compare with `compare_runs.py`, adding
`--by memory` for a memory concern. It warns when the two runs differ in `Timer`,
`DetailLevel`, `Memory` or `Threads`, and notes a setting one run did not record (an older
export); if it warns, the baseline was the wrong choice. Report:

- The wall-time change, or the allocation and peak change under `--by memory`.
- Functions that improved, regressed, appeared or disappeared.
- What changed in the code between the runs, when known.
- Any regression, prominently.

### Follow-up passes

After each pass, check for signals that call for another pass. When one fires, run the next
pass without asking (within the limits below), so that the report gives a complete picture
instead of a series of "shall I look at X?" questions.

Signals:

- The history buffer filled: rerun with a larger `-historysize`.
- The user asked about memory, or a time hotspot is full of allocation-looking built-ins
  (`zeros`, `ones`, `repmat`, `cat`, `sparse` in a loop, or many calls on growing arrays):
  run a memory pass.
- The user asked about parallelism, or multithreaded built-ins (`mtimes`, `mldivide`, `svd`,
  `eig`, `fft`) dominate the time: run a `-timer cpu` pass and compare.
- One function takes more than 70 % of the time and its lines do not explain where: profile
  it in isolation ("Focused-profile recipes" in the reference, Recipe A).
- The profiler overhead looks larger than the work (absolute times far above `timeit`) and is
  spread across many functions: shrink the workload (Recipe C).
- A memory pass shows large allocations in a function that is also a time hotspot: no new
  pass is needed; report the connection.

Ask the user instead when:

- The next pass will take more than 120 seconds.
- Two or more follow-up passes have not made the picture clearer.
- Several signals fire and none dominates, or the next step is a scoping choice (which
  function to isolate, which fixture to use). List the options in the report's next steps,
  each with what it would show and roughly how long it would take, and wait for the user's
  choice.

When the user asks why something is slow, do not stop at time hotspots: check whether memory
pressure, poor parallelism or excessive call counts contribute.

## Phase 4: Report

Report the chained passes once, at the end, not after each pass.

1. Summary: what was profiled and where (code directory, startup options, threads), the
   passes and their settings, wall time, history-buffer status, run paths.
2. A findings table suited to the measurement: time, memory or a comparison.
3. The before/after comparison, prominently, when there was a baseline.
4. Per-line detail for the hotspots. Read the source at those lines and explain what the code
   does there, not just the line numbers; `--all-lines` shows the hits beside the source.
5. Surprises: anything the first pass did not predict, such as built-in dominance, unexpected
   call counts, a CPU and wall-clock mismatch, history overflow, or regressions in untouched
   functions.
6. Diagnosis: connect the findings to the user's concern, across all passes.
7. Next steps: specific changes or investigations, with the benchmarks you ran. Do not change
   the code.
8. The HTML report. Write it for each run you report, then tell the user to open
   `<RUNS_DIR>/<run_id>/profile_html/file0.html`:

   ```shell
   matlab -nodisplay -batch "s = load('<RUN_DIR>/profile.mat'); \
       profsave(s.info, '<RUN_DIR>/profile_html')"
   ```

   `profsave` also opens the report in the default browser, which is why it runs only here
   and not after every pass. Give it the absolute `RUN_DIR`: with a relative path the
   browser open fails with a misleading "file does not exist" error, although the files are
   written. It also warns that coverage information for `run_profile.m` is not available;
   the warning is harmless.

### Explaining the numbers

Each profiler figure is per function and rolled up: it includes the function's callees and is
summed over all its calls ("How the per-function numbers aggregate" in the reference). Say so
when it matters, in these terms:

- Time: TotalTime is inclusive. The run's wall-clock time is `WallTime`, not a sum of the
  table. A function's own cost is its self-time, its TotalTime minus its children's.
- Memory: allocated (and freed) is the total requested (or released) over the run, so it
  measures churn. Peak is the most memory live at one instant, so it measures how much RAM the
  run needed. Allocated can far exceed peak when memory is reused; do not present them as one
  quantity.
- Run totals: the `run_profile>workload` row holds the run's allocation, freed and peak
  totals. Never sum a column, and do not take the largest row: the driver script's row is
  larger because it also counts the profiler's shutdown.
- Memory figures come only from the profiler's `-memory on` mode, which is undocumented.
  When a memory pass returns no memory fields, say so: there is no other measurement to fall
  back on.
- The memory figures can miss temporaries created inside built-ins: in one test, 32 MB of
  `rand` results did not appear. Treat allocation totals as a lower bound.

## Cleanup

The runs directory grows with every run. Report its size (`du -sh <RUNS_DIR>`) in the final
report. When the user is done, offer to clean it up, and delete nothing without asking.
