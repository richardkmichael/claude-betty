# Checked-in file drift

CI failed because a checked-in file no longer matches what a deterministic command would produce.
The file and the command have drifted apart. This is mechanical and safe to auto-fix: run the
canonical command, commit the result. You aren't making a judgment about behavior — you're
restoring a file to the output the project already expects.

Three common instances, all the same shape:

- Formatting. A format check (`gofmt -l`, `prettier --check`, `rubocop`, `black --check`) reports
  files that aren't formatted. Fix: run the formatter, commit.
- Code generation / snapshots. A generated artifact (an API client, a snapshot fixture, a CLI
  surface manifest, generated docs) is stale relative to its source. Fix: run the generate
  command, commit.
- Dependency lockfile. A tidy or lock check (`go mod tidy` + diff, `bundle lock`, `npm ci`) shows
  the lockfile is out of sync with the manifest. Fix: run the tidy/lock command, commit.

## Recognizing this pattern

- `failed_step` is a `*-check`, `verify`, `diff`, or snapshot step rather than a build or test step.
- `log_tail` shows a list of files that "would be reformatted", a `git diff` against a generated
  file, or a message like "X is not tidy — run Y".
- Annotations, if any, point at the checked-in artifact itself, not at logic in source.

## Fix recipe

1. Read the context file for the exact command. It declares the project's formatter, regenerate
   command, and dependency-tidy command. Use the project's command — don't guess at flags or
   reinvent the generator.

2. Run the command locally.

3. Confirm it produced exactly the drift CI complained about, and nothing more:

   ```bash
   git diff --stat
   ```

   If the command rewrote files unrelated to the CI failure, stop. Your local tool version
   probably differs from the runner's. Match the version (the context file or the workflow names
   it) before committing — committing a wider reformat than CI asked for creates churn and can
   fail CI again under the runner's version.

4. Stage the regenerated files explicitly, then run the check command (SKILL.md step 5) to confirm
   green.

5. Commit and return to SKILL.md step 7 (push).

## Red flags (stop, don't auto-commit)

- The command rewrites far more than CI flagged — version skew between your tool and the runner's.
  Reconcile the version first.
- There's no declared command for this artifact in the context file and you'd be guessing how it's
  generated. Find the real generator (usually a Makefile target, a script, or a CI step) before
  running anything.
- The "generated" file has been hand-edited since it was last generated — regenerating would drop
  those edits. That's a source-of-truth problem that needs a human.
