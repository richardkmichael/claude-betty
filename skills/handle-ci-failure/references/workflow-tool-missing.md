# Workflow tool not installed

A workflow step failed because a tool it invokes isn't installed on the runner. This is a workflow
configuration gap, not a code problem — the fix is to add the install step, not to change any
source.

## Recognizing this pattern

- `log_tail` contains `command not found`, `No such file or directory`, or
  `make: <tool>: No such file or directory`.
- No annotations on source files — the failure is in the shell, before any code ran.
- The same command succeeds locally, where the tool is already on your PATH.

## Fix recipe

1. Identify the missing tool from the log tail.

2. Find how the tool is already installed elsewhere in the repo's workflows. Another workflow that
   runs the same check usually has the install step you need — copy it verbatim, including any
   pinned action version or SHA, so the two stay in sync.

   If no workflow installs it yet, add the tool's documented install step (its official setup
   action, or the package-manager command). Pin actions to a release tag or SHA — never reference
   a floating `@main`.

3. Add the install step to the failing workflow immediately before the step that needs the tool.

4. Confirm the check passes locally. The tool is already on your machine, so this just verifies the
   command itself is correct — it can't reproduce the runner's missing-tool state.

5. Return to SKILL.md step 6 (commit + push). A workflow-only change doesn't touch the working
   branch's history, so a plain push is fine unless the context file says otherwise.

## Red flags (stop, don't auto-fix)

- The tool is installed but a version mismatch causes the failure — that's version drift between
  workflows or between local and CI, a different problem from a missing install.
- You can't find a reference install step and the tool has no obvious official setup action — look
  it up rather than adding an unpinned third-party action on guesswork.
