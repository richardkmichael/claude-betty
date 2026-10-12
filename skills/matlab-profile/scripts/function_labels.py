"""Label the functions of a profile.json so that each row has a distinct name.

Two FunctionTable rows can share a FunctionName: functions of the same name in different
folders (a helper.m in each of two packages) each get their own row. Keying rows on
FunctionName alone merges them, so the tools label a row with the end of its file path when
its name belongs to more than one file within a run. The label omits the rest of the path,
so the same function carries the same label in runs taken from two different checkouts of a
project.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from collections.abc import Iterable
from typing import Any, NamedTuple

# One row of the profile's Functions array, as export_profile_json.m writes it.
FunctionRow = dict[str, Any]


class LabelledFunction(NamedTuple):
    fn: FunctionRow
    label: str


def _file_tail(file_name: str | None) -> str:
    """The last two components of a file path, on either path separator."""
    parts = [p for p in re.split(r"[\\/]", file_name or "") if p]
    return "/".join(parts[-2:])


def _shared_names(fns: Iterable[FunctionRow]) -> set[str]:
    """FunctionNames that belong to more than one file within one run."""
    files_by_name: defaultdict[str, set[str]] = defaultdict(set)
    for fn in fns:
        files_by_name[fn["FunctionName"]].add(_file_tail(fn.get("FileName")))
    return {name for name, files in files_by_name.items() if len(files) > 1}


def function_labels(fns: list[FunctionRow], reference: Iterable[FunctionRow] = ()) -> list[str]:
    """Return a label for each row of fns, in order.

    A row's label is its FunctionName, followed by the end of its file path when that name
    belongs to more than one file within fns or within reference. Pass the other run as
    reference when comparing two runs, so that a function keeps the same label in both even
    when its name is shared in only one of them. A name is shared only within a run: a
    function whose file sits in a different folder in each run, such as the driver script
    in each run's own directory, keeps its plain name. A counter follows when rows still
    collide: a function reloaded mid-run can appear twice from the same file.
    """
    shared = _shared_names(fns) | _shared_names(reference)

    def label(fn: FunctionRow) -> str:
        name = fn["FunctionName"]
        return f"{name} ({_file_tail(fn.get('FileName'))})" if name in shared else name

    labels = [label(fn) for fn in fns]
    label_counts = Counter(labels)
    seen: Counter[str] = Counter()
    for i, label_text in enumerate(labels):
        if label_counts[label_text] > 1:
            seen[label_text] += 1
            labels[i] = f"{label_text} #{seen[label_text]}"
    return labels


def labelled_functions(
    fns: list[FunctionRow], reference: Iterable[FunctionRow] = ()
) -> list[LabelledFunction]:
    """Pair each row of fns with its label, in order; see function_labels for reference."""
    return list(map(LabelledFunction, fns, function_labels(fns, reference)))


def labelled(
    fns: list[FunctionRow], reference: Iterable[FunctionRow] = ()
) -> dict[str, FunctionRow]:
    """Map each row's label to the row; see function_labels for reference."""
    return dict(zip(function_labels(fns, reference), fns, strict=True))
