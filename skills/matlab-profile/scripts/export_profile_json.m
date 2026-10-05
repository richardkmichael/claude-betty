function export_profile_json(info, status, wallTime, outPath)
    % Serialise a profile('info') struct to compact JSON.
    %
    % Drops FunctionHistory (can be millions of events). To retain the full
    % struct, save it to a .mat separately via save(matPath, 'info', '-v7.3').
    % Captures per-function aggregates including ExecutedLines and optional
    % memory fields (PeakMem, AllocatedMemory, FreedMemory) when -memory on
    % was used.
    %
    % Pass the profile('status') struct to embed the run's profiler settings
    % under "Settings", and the tic/toc wall time under "WallTime", making the
    % JSON self-describing. Settings also gets what profile('status') does not
    % report: Memory (whether -memory on was used) and Threads (this session's
    % maxNumCompThreads), so call this in the session that ran the workload.
    %
    % Call after `profile off; info = profile('info')`.

    arguments
        info (1,1) struct {mustBeProfileInfo}
        status (1,1) struct
        wallTime (1,1) double {mustBeNonnegative}
        outPath {mustBeTextScalar}
    end

    function_table = info.FunctionTable;

    % Top-level metadata first; the Functions array is appended below.
    profile_json.Name = info.Name;
    profile_json.ClockPrecision = info.ClockPrecision;
    profile_json.ClockSpeed = info.ClockSpeed;
    profile_json.NumFunctions = numel(function_table);
    profile_json.WallTime = wallTime;

    % ProfilerStatus is always 'off' by the time status is captured; drop it.
    if isfield(status, 'ProfilerStatus')
        status = rmfield(status, 'ProfilerStatus');
    end
    % HistorySize is an integer count; serialise it as one (jsonencode renders the
    % raw double as 2.0E+7).
    if isfield(status, 'HistorySize')
        status.HistorySize = int64(status.HistorySize);
    end
    % profile('status') does not report -memory on, so record it from the
    % FunctionTable: a memory pass gives every row the memory fields.
    if isfield(function_table, 'PeakMem')
        status.Memory = 'on';
    else
        status.Memory = 'off';
    end
    % Nor the thread count, which -singleCompThread or maxNumCompThreads(n) set
    % and which decides how multithreaded built-ins time; read it from this
    % session.
    status.Threads = maxNumCompThreads;
    profile_json.Settings = status;

    function_entries = cell(numel(function_table), 1);

    for i = 1:numel(function_table)
        f = function_table(i);

        executed_lines = f.ExecutedLines;
        % Under -memory on, ExecutedLines is Nx6: columns 4-6 add per-line
        % allocated/freed/peak bytes. A time-only pass leaves it Nx3.
        has_line_memory = size(executed_lines, 2) >= 6;
        line_entries = cell(size(executed_lines, 1), 1);
        for j = 1:size(executed_lines, 1)
            entry = struct( ...
                'line', executed_lines(j, 1), ...
                'hits', executed_lines(j, 2), ...
                'time', executed_lines(j, 3));
            if has_line_memory
                entry.allocated = executed_lines(j, 4);
                entry.freed = executed_lines(j, 5);
                entry.peak = executed_lines(j, 6);
            end
            line_entries{j} = entry;
        end

        s = struct( ...
            'FunctionName', f.FunctionName, ...
            'FileName', f.FileName, ...
            'Type', f.Type, ...
            'NumCalls', f.NumCalls, ...
            'TotalTime', f.TotalTime, ...
            'PartialData', f.PartialData, ...
            'ExecutedLines', {line_entries});

        if isfield(f, 'PeakMem')
            s.PeakMem = f.PeakMem;
            % R2025b names these TotalMemAllocated/TotalMemFreed; older
            % documentation/references call them AllocatedMemory/FreedMemory.
            if isfield(f, 'TotalMemAllocated')
                s.AllocatedMemory = f.TotalMemAllocated;
                s.FreedMemory = f.TotalMemFreed;
            elseif isfield(f, 'AllocatedMemory')
                s.AllocatedMemory = f.AllocatedMemory;
                s.FreedMemory = f.FreedMemory;
            end
        end

        function_entries{i} = s;
    end

    profile_json.Functions = function_entries;

    [file, msg] = fopen(outPath, 'w');
    if file == -1
        error('export_profile_json:CannotOpenFile', ...
            'Could not open %s for writing: %s', outPath, msg);
    end
    fprintf(file, '%s', jsonencode(profile_json));
    fclose(file);
end

function mustBeProfileInfo(info)
    % Validate that info carries the fields export_profile_json reads from a
    % profile('info') result.
    required = {'FunctionTable', 'Name', 'ClockPrecision', 'ClockSpeed'};
    present = isfield(info, required);
    if ~all(present)
        error('export_profile_json:InvalidProfileInfo', ...
            'Input must be a profile(''info'') struct; missing field(s): %s.', ...
            strjoin(required(~present), ', '));
    end
end
