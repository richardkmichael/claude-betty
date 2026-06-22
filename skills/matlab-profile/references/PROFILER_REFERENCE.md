# MATLAB Profiler Reference

Verified against R2025b on Apple Silicon. Every default was confirmed via `profile('status')`,
not taken from documentation at face value.

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

When unsure whether an option exists, probe `profile('status')` first rather than fabricating.

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

| Action   | Effect                                                                      |
|----------|-----------------------------------------------------------------------------|
| on       | Start profiler, clear prior statistics                                      |
| off      | Stop profiler, keep statistics                                              |
| resume   | Restart without clearing previously recorded statistics                     |
| clear    | Stop and wipe statistics. Does NOT reset Timer/DetailLevel/HistorySize      |
| viewer   | Stop and open GUI. Not supported in -batch mode                             |
| info     | Stop and return result struct                                               |
| status   | Return current settings (works whether profiler is on or off)               |

## Configuration options

### Recommended defaults for profiling

```matlab
profile('on', '-detail', 'builtin', '-historysize', 2e7);
```

- `-detail builtin`: includes built-in operators in the trace. Negligible overhead vs mmex.
- `-historysize 2e7`: the 5M default fills on runs longer than a few minutes.

### History options

| Option           | Effect                                                              | Default       |
|------------------|---------------------------------------------------------------------|---------------|
| -nohistory       | Aggregate stats only. FunctionHistory is empty.                     |               |
| -history         | Aggregates + entry/exit sequence (no timestamps)                    |               |
| -timestamp       | Aggregates + sequence + epoch timestamp per event                   | yes           |
| -historysize N   | Bound event buffer to N entries. Overflow silently drops history    | 5,000,000     |

History overflow is silent: aggregate stats keep accumulating but trace data stops.
Check `size(info.FunctionHistory, 2)` against the cap after each run.

### Timer options

| Option               | Effect                                                            | Default |
|----------------------|-------------------------------------------------------------------|---------|
| -timer 'performance' | OS wall-clock. Most reliable general choice.                      | yes     |
| -timer 'processor'   | Direct processor clock. Can drift under power management.         |         |
| -timer 'real'        | OS system time. Highest overhead. Affected by clock adjustments.  |         |
| -timer 'cpu'         | CPU time summed across threads. For parallel comparison.          |         |

### Detail level

| Option          | Effect                                                             | Default |
|-----------------|--------------------------------------------------------------------|---------|
| -detail mmex    | Track M-functions and MEX functions only                           | yes     |
| -detail builtin | Also track built-in operators (*, +, speye, sparse, etc.)         |         |

`-detail builtin` is undocumented but works in R2025b. Measured cost on a 500-iteration
benchmark: indistinguishable wall-clock time, ~2x FunctionTable rows, ~26% more history events.
Use it by default.

### Memory options (undocumented)

| Option       | Effect                                                               |
|--------------|----------------------------------------------------------------------|
| -memory on   | Track allocated/freed/peak memory per function. Significant overhead |
| -nomemory    | Disable memory tracking (default)                                    |

Use only when chasing a memory issue. When enabled, FunctionTable entries gain:
- PeakMem: peak memory usage during the function (bytes)
- TotalMemAllocated: total bytes allocated (bytes). Older sources call this AllocatedMemory.
- TotalMemFreed: total bytes freed (bytes). Older sources call this FreedMemory.

These fields are undocumented and the names varied historically. Detect with
isfield(ft(i), 'TotalMemAllocated') and fall back to isfield(ft(i), 'AllocatedMemory').
May be unreliable on some platforms.

Note: `-memory on` cannot be combined with an action in a single profile() call.
Use `profile('-memory', 'on', '-detail', 'builtin', '-historysize', 2e7)` as a
single configure-and-start invocation — NOT `profile('on', '-memory', 'on', ...)`,
which errors with "Only one profiler action is supported per call."

### Critical gotcha: settings persist

Timer, DetailLevel, and HistorySize survive `profile off` and `profile clear`.
Only ProfilerStatus and statistics are affected by clear.
Always set options explicitly or start a new MATLAB session.

## Output structures

### profile('info') top-level fields

| Field           | Meaning                                                                  |
|-----------------|--------------------------------------------------------------------------|
| FunctionTable   | Struct array, one entry per function observed                            |
| FunctionHistory | Event matrix. 2xE (history) or 4xE (timestamp)                          |
| ClockPrecision  | Timer resolution in seconds (~3.3e-7 for performance timer)             |
| ClockSpeed      | Estimated CPU clock speed in Hz                                          |
| Name            | 'MATLAB' (constant)                                                      |
| Overhead        | Reserved for future use. Always 0; ignore                                |

### FunctionTable entry fields

| Field              | Meaning                                                                |
|--------------------|------------------------------------------------------------------------|
| CompleteName       | Full dotted path (e.g., decomposition>decomposition.mldivide)          |
| FunctionName       | Short name                                                             |
| FileName           | Absolute path to .m file                                               |
| Type               | 'M-function', 'M-subfunction', 'MEX-function', 'Built-in', etc.       |
| NumCalls           | Total invocations                                                      |
| TotalTime          | INCLUSIVE time (this function + all children). NOT self-time.           |
| TotalRecursiveTime | Deprecated, unused                                                     |
| Children           | Struct array: {Index, NumCalls, TotalTime} per direct child            |
| Parents            | Struct array: {Index, NumCalls} per caller                             |
| ExecutedLines      | Nx3 matrix: [line_number, hit_count, time_seconds]                     |
| IsRecursive        | Logical                                                                |
| PartialData        | Logical. True if function was edited/cleared mid-profile               |

Important nuances:
- TotalTime is INCLUSIVE. Self-time must be computed:
  self = TotalTime - sum(child.TotalTime * child.NumCalls / Children(child).NumCalls)
- Sum of ExecutedLines time does NOT necessarily equal TotalTime.
  The difference is time in built-in operators not line-attributed
  (unless -detail builtin was used).
- CompleteName disambiguates subfunctions better than FunctionName.

### profile('status') fields

| Field            | Values                                              | Default       |
|------------------|-----------------------------------------------------|---------------|
| ProfilerStatus   | 'on', 'off'                                         | 'off'         |
| DetailLevel      | 'mmex', 'builtin'                                   | 'mmex'        |
| Timer            | 'performance', 'processor', 'cpu', 'real'           | 'performance' |
| HistoryTracking  | 'on', 'off', 'timestamp'                            | 'timestamp'   |
| HistorySize      | integer                                              | 5000000       |

### FunctionHistory event matrix

4xE matrix (with -timestamp, the default):

| Row | Meaning                                                |
|-----|--------------------------------------------------------|
| 1   | 0 = function entry, 1 = function exit                  |
| 2   | Index into FunctionTable (1-based)                     |
| 3   | Epoch seconds (integer part)                           |
| 4   | Epoch microseconds (fractional part)                   |

Built-in functions do NOT appear in FunctionHistory under -detail mmex.
Use -detail builtin to include them.

What FunctionHistory reveals that FunctionTable cannot:
- Per-iteration call patterns (slice by time range)
- Call sequence verification (confirm assumed call trees)
- Outlier identification (slow individual invocations hidden by averages)
- Flame-graph construction (pair entries with exits via stack walk)

## Measured overhead (M1, R2025b)

| Operation       | Without profiler | Under profiler | Overhead |
|-----------------|------------------|----------------|----------|
| decomposition   | ~40 us/call      | ~118 us/call   | ~78 us   |
| mldivide (\\)   | ~20 us/call      | ~45 us/call    | ~25 us   |
| sparse build    | ~15 us/call      | ~25 us/call    | ~10 us   |

Per-call overhead is large for micro-operations but invisible on long runs (>minutes).
On long-running workloads, profiler overhead is typically within system variance.

## Interpretation rules of thumb

1. Trust ordering and proportions on long runs (minutes). Distrust absolute per-call
   times for micro-operations (<1 ms). Use tic/toc for those.
2. For line-level attribution within a function, the profiler is the right tool
   regardless of workload size.
3. Use CompleteName to disambiguate subfunctions and overloaded methods.
4. Ignore the first few FunctionHistory events (loader/addpath). Focus on the
   time range corresponding to actual workload.
5. History overflow is silent. Always check size(info.FunctionHistory, 2) against
   the historysize cap.

## Pass-planning heuristic

After the first profile pass, decide whether to iterate:

| First-pass outcome                              | Next step                                  |
|-------------------------------------------------|--------------------------------------------|
| Clean attribution, overhead <50%, history OK    | One pass enough. Analyze and act.          |
| History buffer hit cap                          | Rerun with larger -historysize             |
| Overhead >2x, hotspot in one function           | Snapshot-and-replay that function          |
| Overhead >2x, spread across many functions      | Shrink workload (smaller fixture/fewer iterations) |
| Only one iteration/region is interesting        | Add profile on/off hooks around that region |

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
    profile clear; profile('on', '-detail', 'builtin', '-historysize', 2e7);
end
% ... body ...
if iteration == target
    profile off; info = profile('info');
end
```

### Shrink workload (Recipe C)

Use a smaller input that still exercises the code paths of interest.

## JSON export for Python analysis

Serialize profile('info') to JSON, dropping FunctionHistory (too large):

```matlab
info = profile('info');
ft = info.FunctionTable;
funcs = cell(numel(ft), 1);
for i = 1:numel(ft)
    f = ft(i);
    el = f.ExecutedLines;
    linesCell = cell(size(el,1), 1);
    for j = 1:size(el,1)
        linesCell{j} = struct('line', el(j,1), 'hits', el(j,2), 'time', el(j,3));
    end
    funcs{i} = struct( ...
        'FunctionName', f.FunctionName, 'FileName', f.FileName, ...
        'Type', f.Type, 'NumCalls', f.NumCalls, ...
        'TotalTime', f.TotalTime, 'TotalRecursiveTime', f.TotalRecursiveTime, ...
        'PartialData', f.PartialData, 'ExecutedLines', {linesCell});
end
out = struct('Name', info.Name, 'ClockPrecision', info.ClockPrecision, ...
    'ClockSpeed', info.ClockSpeed, 'NumFunctions', numel(ft), 'Functions', {funcs});
fid = fopen('profile_output.json', 'w');
fprintf(fid, '%s', jsonencode(out));
fclose(fid);
```

## Python analysis patterns

Rank functions by inclusive time:

```python
import json
with open('profile_output.json') as f:
    d = json.load(f)
fns = sorted(d['Functions'], key=lambda f: -f['TotalTime'])
for fn in fns[:15]:
    c = fn['NumCalls']
    u = 1e6 * fn['TotalTime'] / c if c else 0
    print(f"{fn['TotalTime']:>10.3f}s  {c:>9d}  {u:>10.2f}us/call  {fn['FunctionName']}")
```

Per-line breakdown for a hot function:

```python
fn = fns[0]  # hottest function
lines = sorted(fn['ExecutedLines'], key=lambda l: -l['time'])
for l in lines[:10]:
    u = 1e6 * l['time'] / l['hits'] if l['hits'] else 0
    print(f"  line {l['line']:>4}  hits={l['hits']:>9}  time={l['time']:>9.2f}s  {u:>9.2f}us/hit")
```

Filter by function name pattern:

```python
for fn in fns:
    n = fn['FunctionName'].lower()
    if any(k in n for k in ['sparse', 'decomposition', 'mldivide']):
        print(f"{fn['TotalTime']:>8.3f}s  {fn['NumCalls']:>9d}  {fn['FunctionName']}")
```

## FunctionHistory queries in MATLAB

Flatten history into a table for filtering:

```matlab
info = load('profile_output.mat').info;

H = info.FunctionHistory;
nEvents = size(H, 2);
ft = info.FunctionTable;

% Build event table
eventType = repmat("entry", nEvents, 1);
eventType(H(1,:) == 1) = "exit";
tableIndex = double(H(2,:))';
epoch = H(3,:)' + H(4,:)' * 1e-6;
tsec = epoch - epoch(1);

fnNames = strings(numel(ft), 1);
for i = 1:numel(ft), fnNames(i) = string(ft(i).FunctionName); end
fnPerEvent = fnNames(tableIndex);

events = table(tsec, eventType, fnPerEvent, tableIndex, ...
    'VariableNames', {'Time', 'EventType', 'Function', 'TableIndex'});

% Count calls in a time range
slice = events(events.Time >= 50 & events.Time < 60, :);
entries = slice(slice.EventType == "entry", :);
groupcounts(entries, 'Function')
```
