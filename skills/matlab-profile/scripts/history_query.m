function events = history_query(profileMatPath, fnNames)
    % Load a profile.mat file and flatten FunctionHistory into a table.
    %
    %   events = history_query('path/to/profile.mat')
    %   events = history_query('path/to/profile.mat', 'myHotFunction')
    %   events = history_query('path/to/profile.mat', {'fn1','fn2'})
    %
    % Columns: Time, EventType (entry|exit), Function, CompleteName, TableIndex.
    % Time is seconds since the first event. Requires the profile to have run
    % with -timestamp (the default since R2015b); otherwise Time is NaN.

    loaded = load(profileMatPath);
    info = loaded.info;

    if ~isfield(info, 'FunctionHistory') || isempty(info.FunctionHistory)
        warning('history_query:EmptyHistory', ...
            'info.FunctionHistory is empty. Was -nohistory set?');
        events = table('Size', [0, 5], ...
            'VariableTypes', {'double', 'categorical', 'string', 'string', 'double'}, ...
            'VariableNames', {'Time', 'EventType', 'Function', 'CompleteName', 'TableIndex'});
        return;
    end

    H = info.FunctionHistory;
    nRows = size(H, 1);
    nEvents = size(H, 2);
    hasTimestamps = (nRows >= 4);

    eventType = repmat(categorical({'entry'}), nEvents, 1);
    eventType(H(1, :) == 1) = categorical({'exit'});

    tableIndex = double(H(2, :))';

    if hasTimestamps
        epoch = H(3, :)' + H(4, :)' * 1e-6;
        tsec = epoch - epoch(1);
    else
        tsec = nan(nEvents, 1);
    end

    ft = info.FunctionTable;
    nFuncs = numel(ft);
    fnStr = strings(nFuncs, 1);
    cnStr = strings(nFuncs, 1);
    for i = 1:nFuncs
        fnStr(i) = string(ft(i).FunctionName);
        cnStr(i) = string(ft(i).CompleteName);
    end
    validIdx = tableIndex >= 1 & tableIndex <= nFuncs;
    fnPerEvent = strings(nEvents, 1);
    cnPerEvent = strings(nEvents, 1);
    fnPerEvent(validIdx) = fnStr(tableIndex(validIdx));
    cnPerEvent(validIdx) = cnStr(tableIndex(validIdx));

    events = table(tsec, eventType, fnPerEvent, cnPerEvent, tableIndex, ...
        'VariableNames', {'Time', 'EventType', 'Function', 'CompleteName', 'TableIndex'});

    if nargin >= 2 && ~isempty(fnNames)
        targets = string(fnNames);
        mask = ismember(events.Function, targets) | ismember(events.CompleteName, targets);
        events = events(mask, :);
    end

    % Print a summary when called without capturing output
    if nargout == 0
        entries = events(events.EventType == 'entry', :);
        if height(entries) > 0
            disp(groupcounts(entries, 'Function'));
        else
            fprintf('No entry events matched.\n');
        end
    end
end
