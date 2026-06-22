function export_profile_json(info, outPath)
    % Serialise a profile('info') struct to compact JSON.
    %
    % Drops FunctionHistory (can be millions of events; save separately via
    % save(outPath, 'info', '-v7.3')). Captures per-function aggregates
    % including ExecutedLines and optional memory fields (PeakMem,
    % AllocatedMemory, FreedMemory) when -memory on was used.
    %
    % Call after `profile off; info = profile('info')`.

    ft = info.FunctionTable;
    funcs = cell(numel(ft), 1);
    for i = 1:numel(ft)
        f = ft(i);
        el = f.ExecutedLines;
        lc = cell(size(el, 1), 1);
        for j = 1:size(el, 1)
            lc{j} = struct('line', el(j, 1), 'hits', el(j, 2), 'time', el(j, 3));
        end
        s = struct( ...
            'FunctionName', f.FunctionName, ...
            'FileName', f.FileName, ...
            'Type', f.Type, ...
            'NumCalls', f.NumCalls, ...
            'TotalTime', f.TotalTime, ...
            'TotalRecursiveTime', f.TotalRecursiveTime, ...
            'PartialData', f.PartialData, ...
            'ExecutedLines', {lc});
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
        funcs{i} = s;
    end
    out = struct( ...
        'Name', info.Name, ...
        'ClockPrecision', info.ClockPrecision, ...
        'ClockSpeed', info.ClockSpeed, ...
        'NumFunctions', numel(ft), ...
        'Functions', {funcs});
    fid = fopen(outPath, 'w');
    fprintf(fid, '%s', jsonencode(out));
    fclose(fid);
end
