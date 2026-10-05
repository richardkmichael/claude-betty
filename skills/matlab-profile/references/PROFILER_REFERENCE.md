# MATLAB Profiler Reference

Covers the MATLAB R2025b profiler.

Contents:

- Verification methodology
- Invocation, and the profiler actions
- Configuration options: history, timer, detail level, memory options, settings that persist
- Output structures: `profile('info')`, FunctionTable entries, how the per-function numbers
  aggregate, `profile('status')`, the FunctionHistory event matrix
- Measured overhead
- Interpretation rules of thumb
- Focused-profile recipes: snapshot-and-replay, a specific region, a smaller workload

## Verification methodology

Before trusting any MATLAB tool, probe it live:

```matlab
s = profile('status');
disp(s);   % actual defaults on THIS machine
```

Several published defaults are wrong for R2025b:

- HistorySize default is 5,000,000 (not 10,000 as older references claim)
- HistoryTracking defaults to 'timestamp' (not 'off')
- Timer defaults to 'performance' (not 'cpu'; changed in R2015b)

`profile('status')` reports the current settings, not which options exist. When unsure
whether an option exists, check MATLAB's documentation for `profile` (the `matlab-docs` skill)
rather than guessing.

## Invocation

```matlab
profile clear                    % wipe prior statistics
profile on                       % start with default settings
% ... run code ...
profile off
info = profile('info');          % struct with FunctionTable + FunctionHistory
profsave(info, 'path/to/html')   % write browsable HTML report
```

Settings can be combined:

```matlab
profile('on', '-detail', 'builtin', '-historysize', 2e7);
```

## Actions

| Action   | Effect                                                                 |
|----------|------------------------------------------------------------------------|
| on       | Start profiler, clear prior statistics                                 |
| off      | Stop profiler, keep statistics                                         |
| resume   | Restart without clearing previously recorded statistics                |
| clear    | Stop and wipe statistics. Does NOT reset Timer/DetailLevel/HistorySize |
| viewer   | Stop and open GUI. Not supported in -batch mode                        |
| info     | Stop and return result struct                                          |
| status   | Return current settings (works whether profiler is on or off)          |

## Configuration options

### Recommended defaults for profiling

```matlab
profile('on', '-detail', 'builtin', '-historysize', 2e7);
```

- `-detail builtin`: includes built-in operators in the trace. Negligible overhead vs mmex.
- `-historysize 2e7`: the 5M default fills on runs longer than a few minutes.

### History options

| Option         | Effect                                            | Default   |
|----------------|---------------------------------------------------|-----------|
| -nohistory     | Aggregate stats only. FunctionHistory is empty.   |           |
| -history       | Aggregates + entry/exit sequence (no timestamps)  |           |
| -timestamp     | Aggregates + sequence + epoch timestamp per event | yes       |
| -historysize N | Bound the event buffer to N entries               | 5,000,000 |

History overflow is silent: aggregate stats keep accumulating but trace data stops.
Check `size(info.FunctionHistory, 2)` against the cap after each run.

### Timer options

| Option               | Effect                                                     | Default |
|----------------------|------------------------------------------------------------|---------|
| -timer 'performance' | OS wall-clock. Most reliable general choice.               | yes     |
| -timer 'processor'   | Direct processor clock. Can drift under power management.  |         |
| -timer 'real'        | OS system time. Highest overhead; clock-adjustment prone.  |         |
| -timer 'cpu'         | CPU time summed across threads. For parallel comparison.   |         |

### Detail level

| Option          | Effect                                                             | Default |
|-----------------|--------------------------------------------------------------------|---------|
| -detail mmex    | Track M-functions and MEX functions only                           | yes     |
| -detail builtin | Also track built-in operators (*, +, speye, sparse, etc.)          |         |

`-detail builtin` is undocumented but works in R2025b. Measured cost on a 500-iteration
benchmark: indistinguishable wall-clock time, ~2x FunctionTable rows, ~26% more history events.
Use it by default.

### Memory options (undocumented)

| Option       | Effect                                                               |
|--------------|----------------------------------------------------------------------|
| -memory on   | Track allocated/freed/peak memory per function. Large overhead       |
| -nomemory    | Disable memory tracking (default)                                    |

Use only when chasing a memory issue. The overhead is large: a loop of small calls ran 130x
slower than under the time profiler. The figures can also miss temporaries created inside
built-ins (32 MB of `rand` results did not appear in one test), so treat allocation totals as
a lower bound. `profile('status')` does not report whether memory profiling was on;
`export_profile_json` records it as `Settings.Memory`, from whether the FunctionTable carries
the memory fields. Nor does it report the thread count, which `-singleCompThread` at startup
or `maxNumCompThreads(n)` set and which changes how multithreaded built-ins time;
`export_profile_json` records `maxNumCompThreads` of its own session as `Settings.Threads`.

When enabled, FunctionTable entries gain:

- PeakMem: the most memory live at any one instant during the function (bytes). A
  high-water mark, so it is a maximum, not a running total.
- TotalMemAllocated: cumulative bytes allocated over the function's run, counting
  every allocation even if later freed (bytes). Older sources call this AllocatedMemory.
- TotalMemFreed: cumulative bytes freed over the function's run (bytes). Older
  sources call this FreedMemory.

All three are INCLUSIVE (function plus its callees) and summed across every call to
the function, except PeakMem, which takes the maximum rather than summing. See "How
the per-function numbers aggregate" below.

These fields are undocumented and the names varied historically. Detect the modern
name with isfield(ft(i), 'TotalMemAllocated') and fall back to
isfield(ft(i), 'AllocatedMemory'). When -memory on works, a profile carries all three
together (verified), and they are not emitted singly, so a reader that finds PeakMem
can rely on TotalMemAllocated/TotalMemFreed too. Where -memory on is unavailable the
fields are absent entirely, not partially: the only cases are all three or none.

Note: `-memory on` cannot be combined with an action in a single profile() call.
Use `profile('-memory', 'on', '-detail', 'builtin', '-historysize', 2e7)` as a
single configure-and-start invocation, NOT `profile('on', '-memory', 'on', ...)`,
which errors with "Only one profiler action is supported per call."

### Critical gotcha: settings persist

Timer, DetailLevel, and HistorySize survive `profile off` and `profile clear`.
Only ProfilerStatus and statistics are affected by clear.
Always set options explicitly or start a new MATLAB session.

## Output structures

### profile('info') top-level fields

| Field           | Meaning                                                     |
|-----------------|-------------------------------------------------------------|
| FunctionTable   | Struct array, one entry per function observed               |
| FunctionHistory | Event matrix. 2xE (history) or 4xE (timestamp)              |
| ClockPrecision  | Timer resolution in seconds (~3.3e-7 for performance timer) |
| ClockSpeed      | Estimated CPU clock speed in Hz                             |
| Name            | 'MATLAB' (constant)                                         |
| Overhead        | Reserved for future use. Always 0; ignore                   |

### FunctionTable entry fields

| Field              | Meaning                                                         |
|--------------------|-----------------------------------------------------------------|
| CompleteName       | Full dotted path (e.g., decomposition>decomposition.mldivide)   |
| FunctionName       | Short name                                                      |
| FileName           | Absolute path to .m file                                        |
| Type               | 'M-function', 'M-subfunction', 'MEX-function', 'Built-in', etc. |
| NumCalls           | Total invocations                                               |
| TotalTime          | INCLUSIVE time (this function + all children). NOT self-time.   |
| TotalRecursiveTime | Deprecated, unused                                              |
| Children           | Struct array: {Index, NumCalls, TotalTime} per direct child     |
| Parents            | Struct array: {Index, NumCalls} per caller                      |
| ExecutedLines      | Nx3 [line, hits, time]; Nx6 with -memory on (see note below)    |
| IsRecursive        | Logical                                                         |
| PartialData        | Logical. True if function was edited/cleared mid-profile        |

Important nuances:

- TotalTime is INCLUSIVE. Compute self-time from the per-parent child times the
  profiler already records in the Children array:
  self = TotalTime - sum(Children.TotalTime)
  Each Children(k).TotalTime is the time spent in that child as called from this
  function (per-parent), not the child's global TotalTime, so no call-count
  scaling is needed. (Verified: the per-parent child times across a function's
  callers sum to that child's global TotalTime.)
- Sum of ExecutedLines time does NOT necessarily equal TotalTime.
  The difference is time in built-in operators not line-attributed
  (unless -detail builtin was used).
- Under -memory on, ExecutedLines gains three columns:
  [line, hits, time, allocated, freed, peak] (the last three in bytes). This
  gives per-LINE memory attribution, not just per-function.
- CompleteName disambiguates subfunctions better than FunctionName.

### How the per-function numbers aggregate

Every per-function number (TotalTime, TotalMemAllocated, TotalMemFreed, PeakMem)
is rolled up two ways at once:

- Up the call tree (inclusive): a function's number includes everything its callees
  did, not just its own work.
- Across calls: a function called N times reports the figure summed over all N
  invocations.

TotalTime, TotalMemAllocated, and TotalMemFreed are sums under both roll-ups. PeakMem
is a maximum (a high-water mark): the most memory live at any single instant during
the function and its callees, taken across all its calls.

Because each number already includes the callees, the whole-run total is the
number of the frame that encloses the workload, NOT a sum down the table. Summing the
table counts the same time or bytes once per stack level, multiplying by call depth. So:

- For a wall-clock total, read the top-level WallTime (the tic/toc around the
  workload), never the sum of TotalTime.
- For a run's allocation, freed, or peak total, read the `run_profile>workload` row
  (the driver's local function). The driver script's own row is larger: it also
  counts `profile off` and `profile('info')`, measured at 20 MB of a 23 MB total on
  a small workload.
- For one function's own cost, use self-time: TotalTime minus the sum of its
  Children.TotalTime (see the self-time note above).

### profile('status') fields

| Field            | Values                                              | Default       |
|------------------|-----------------------------------------------------|---------------|
| ProfilerStatus   | 'on', 'off'                                         | 'off'         |
| DetailLevel      | 'mmex', 'builtin'                                   | 'mmex'        |
| Timer            | 'performance', 'processor', 'cpu', 'real'           | 'performance' |
| HistoryTracking  | 'on', 'off', 'timestamp'                            | 'timestamp'   |
| HistorySize      | integer                                             | 5000000       |

### FunctionHistory event matrix

| Row | Meaning                               |
|-----|---------------------------------------|
| 1   | 0 = function entry, 1 = function exit |
| 2   | Index into FunctionTable (1-based)    |
| 3   | Epoch seconds (integer part)          |
| 4   | Epoch microseconds (fractional part)  |

Built-in functions do NOT appear in FunctionHistory under -detail mmex.
Use -detail builtin to include them.

What FunctionHistory reveals that FunctionTable cannot:

- Per-iteration call patterns (slice by time range)
- Call sequence verification (confirm assumed call trees)
- Outlier identification (slow individual invocations hidden by averages)
- Flame-graph construction (pair entries with exits via stack walk)

## Measured overhead (Apple M1, R2025b)

| Operation       | Without profiler | Under profiler | Overhead |
|-----------------|------------------|----------------|----------|
| decomposition   | ~40 us/call      | ~118 us/call   | ~78 us   |
| mldivide (\\)   | ~20 us/call      | ~45 us/call    | ~25 us   |
| sparse build    | ~15 us/call      | ~25 us/call    | ~10 us   |

Per-call overhead is large for micro-operations but invisible on long runs (>minutes).
On long-running workloads, profiler overhead is typically within system variance.

## Interpretation rules of thumb

1. Trust ordering and proportions on long runs (minutes). On short runs the per-call
   overhead inflates instrumented functions called many times, and built-ins compiled
   inline (`rand`, `sum`) may be missing from the table even with -detail builtin, so the
   proportions mislead as well as the absolute times. Use timeit for those; it warms up
   and returns the median over many runs, which raw tic/toc does not.
2. For line-level attribution within a function, the profiler is the right tool
   regardless of workload size.
3. Use CompleteName to disambiguate subfunctions and overloaded methods.
4. Ignore the first few FunctionHistory events (loader/addpath). Focus on the
   time range corresponding to actual workload.

## Focused-profile recipes

SKILL.md's "Follow-up passes" decides when to use these: Recipe A when one function
dominates, Recipe B when only one iteration or region matters, Recipe C when the profiler
overhead is spread across many functions.

### Snapshot-and-replay (Recipe A)

After identifying the hot function, capture one invocation's inputs and replay in isolation:

```matlab
% In a separate session:
s = load('snapshot.mat');
profile clear; profile('on', '-detail', 'builtin', '-historysize', 2e7);
for rep = 1:1000
    r = hotFunction(s.arg1, s.arg2);
end
profile off; info = profile('info');
```

### Profile a specific region (Recipe B)

Add temporary hooks around the iteration of interest:

```matlab
if iteration == target
    profile clear;
    profile('on', '-detail', 'builtin', '-historysize', 2e7);
end
% ... body ...
if iteration == target
    profile off;
    info = profile('info');
end
```

### Shrink workload (Recipe C)

Use a smaller input that still exercises the code paths of interest.
