---
name: handle-ci-failure
description: "TRIGGER immediately — before any manual investigation — whenever the user mentions CI failures: 'CI is failing', 'why is CI red?', 'the build is broken', 'CI failed', a run ID, a GitHub Actions URL, or any question about a failed workflow run. Do not investigate manually first. This skill reads a project-local CI context file (.github/HANDLE_CI_FAILURE.md), diagnoses the failure, matches it to a known pattern, applies the fix, verifies, and commits + pushes. Targets mechanical CI failures — not open-ended debugging."
argument-hint: "[run-id | run-url]"
---

# Handle CI failure

## Project CI context

```!
cat .github/HANDLE_CI_FAILURE.md 2>/dev/null || echo "No .github/HANDLE_CI_FAILURE.md found. Derive the branch from \`git branch --show-current\`; find the check command from the Makefile, CI workflow, or package scripts (or ask the user); push without force unless you confirm the branch is rebased on a cycle. There are no project-specific failure patterns to consult."
```

The context file is the project's contract with this skill. It declares the working branch, the
check command, the push policy, the commands behind any auto-fixable failures (formatter,
regenerate, dependency tidy), and any project-specific failure patterns. Read it before matching.

## Inputs

The user may provide:

- A bare run ID (`12345678901`).
- A `View results:` URL from a GitHub notification email — extract the numeric run ID with
  `grep -oE 'actions/runs/[0-9]+' <<<"$input" | cut -d/ -f3`.
- Nothing — list recent failures on the working branch and ask.

## Workflow

### 1. Resolve the run ID

If the user gave one, use it. Otherwise:

```bash
gh run list --branch <branch> --status=failure --limit 5
```

Show the list with run IDs and ages, and ask which one to handle. Don't silently pick "most
recent" — it drifts between sessions.

### 2. Extract failure context

```bash
"$CLAUDE_SKILL_DIR"/scripts/ci-failure-context.sh <run-id>
```

Output JSON shape:

```json
{
  "run_id": "...", "run_url": "...", "workflow": "...",
  "branch": "...", "head_sha": "...", "conclusion": "failure",
  "failed_jobs": [
    {
      "id": 123, "name": "lint", "url": "...",
      "failed_step": "Run go vet",
      "annotations": [{"path": "...", "line": 15, "level": "failure", "message": "..."}],
      "log_tail": "... last 40 lines ..."
    }
  ]
}
```

Read the JSON once. Don't re-query `gh` for data the script already pulled.

### 3. Identify the real signal

`annotations` always contains runner-wrapper noise like `{path: ".github", message: "Process
completed with exit code 1."}`. Filter `path != ".github"` when looking for root cause. Real
signals point at real file paths with real messages. The `log_tail` is a fallback when annotations
don't tell the whole story.

### 4. Match a known failure pattern

Generic patterns the skill handles directly:

| Pattern                                                                                                                                            | Reference                              |
| -------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------- |
| A checked-in file no longer matches a deterministic command's output — a formatting, code-generation/snapshot, or dependency-lockfile check fails  | `references/checked-in-file-drift.md`  |
| A tool the workflow needs isn't installed on the runner (`command not found`, `No such file or directory`)                                         | `references/workflow-tool-missing.md`  |
| A single source file flagged with one clear, unambiguous lint/vet/type error                                                                       | Fix it directly — see the note below   |

Project-specific patterns live in the context file under a "Project failure patterns" heading
(merge-conflict resolution, framework-specific failures, and the like). Consult those too — they
point at project-local reference docs.

The direct-fix row is for cases where an annotation names a file and line and the fix is obvious
(an unused import after a rename, a symbol that moved). If the fix needs judgment, spans several
files, or you're guessing, treat it as open-ended debugging and escalate per the rules below
rather than committing a speculative change.

Quick triage heuristic:

- Annotation points at a real source file with a compile/lint/vet message and one obvious fix →
  fix directly.
- A `*-check` / `diff` / snapshot step shows a checked-in file differs from generated output, or
  `log_tail` lists files that "would be reformatted" / "is not tidy" → regenerate and commit.
- `log_tail` shows `command not found` or `No such file or directory` for a tool → workflow tool
  missing.
- The failure matches a pattern described in the context file → follow that reference.

If none fits, stop and report the diagnosis. This skill is for mechanical, well-understood failure
classes — not open-ended debugging. Adding a new generic pattern is cheap (drop a file in
`references/`, add a row here); a project-specific one goes in the context file instead. Either
should be done deliberately, after seeing the failure more than once.

### 5. Verify

Run the project's check command (the context file names it — e.g. `make check`, `rake`,
`npm test`). Must be green before committing. If it isn't, the fix is incomplete — don't paper
over with an inline suppression or by deleting tests.

### 6. Commit

Confirm the working branch matches what the context file specifies (`git branch --show-current`).
If it doesn't, stop.

Write the message to the project's commit conventions: its stated ones (CLAUDE.md, `CONTRIBUTING`,
a commit-lint config) first, then the style of its recent history. Explain why, not what.

Stage explicitly — `git add -u` for tracked files, `git add <path>` for new ones. Never
`git add -A`. Before committing, confirm any project instruction files the repo keeps out of
version control (e.g. `CLAUDE.md`, `CLAUDE.local.md`) are not staged.

### 7. Push

A normal branch takes a plain push:

```bash
git push origin <branch>
```

Use `--force-with-lease` only when the context file says the working branch is rebased or
force-pushed on a cycle (a syncing fork, for example), where a fast-forward push would be rejected:

```bash
git push --force-with-lease origin <branch>
```

Never plain `--force`. If `--force-with-lease` is rejected because an automated workflow updated
the remote between your fetch and push, re-fetch and re-evaluate rather than overriding the lease.

### 8. Confirm green

```bash
gh run list --branch <branch> --limit 3
```

Watch the new run with `gh run watch <run-id> --exit-status`. If it fails again with a different
error, start over at step 1.

## Guardrails

- Never commit project instruction files the repo keeps out of version control (e.g. `CLAUDE.md`,
  `CLAUDE.local.md`). Check `.gitignore` if unsure.
- Never pass `--no-verify` or skip commit signing.
- Never use `git add -A` or `git add .`. Always stage explicitly.
- Never amend a pushed commit. New fix → new commit.
- Never force-push a protected branch (`main`, `master`, a release branch).

## When to stop and escalate

- Failure doesn't match a generic pattern above or a project-specific one in the context file.
- Local reproduction gives a *different* error than CI reported — local state diverged; investigate
  before fixing.
- Check command won't go green after what looks like the right fix — don't commit a partial fix.
  Report what's left.
- Multiple jobs failed for unrelated reasons — handle them one at a time.
