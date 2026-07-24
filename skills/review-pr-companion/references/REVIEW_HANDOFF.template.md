# Review handoff -- <PR #N, or the branch under review>

You are the author's coding agent. This branch is a code review of <target> delivered as commits
rather than comments. Walk the author through it unit by unit and apply what they accept.

Every commit is a proposal. The author may decline any of them, and a declined unit is simply not
cherry-picked. Do not push.

## Orient

```bash
git fetch origin
git log --oneline <pr-head>..origin/<review-branch>
git diff <pr-head>..origin/<review-branch>
```

Name the review branch `origin/<review-branch>`. A plain `git fetch` creates the remote-tracking ref
only, so the bare name does not resolve for anyone who has not checked the branch out.

`<pr-head>` is the head this review was written against. If the branch has moved on since, diff
against its current head instead and expect some fixups to need rebasing.

The same diff in a browser, for the author to read alongside you:
https://github.com/<owner>/<repo>/compare/pull/<N>/head...<review-branch>

## The units

| #  | Commit    | Kind       | Target    | Depends on | What                |
|----|-----------|------------|-----------|------------|---------------------|
| 1  | `<sha>`   | fixup      | `<sha>`   | --         | <one-line summary>  |
| 2  | `<sha>`   | standalone | --        | 1          | <one-line summary>  |

A fixup carries its `fixup!` subject through the cherry-pick, so the rebase at the end folds it into
the commit named in Target. A standalone commit survives into the history with its own message.

<Commit `<sha>` adds the review documents. It is not a unit -- do not cherry-pick it.>

## For each unit, in pick order

1. Read the matching numbered item in `REVIEW_PLAN.md` and explain to the author what the change is,
   why it was proposed, and what alternatives were weighed. Answer their questions from that item.
2. Show the diff: `git show <sha>`.
3. Ask the author to take it or skip it.
4. If taken, cherry-pick it onto their branch.

Where a unit lists a dependency, raise that when you reach it: taking a unit whose predecessor was
skipped may not apply cleanly, or may document something that does not exist.

## When the walk is done

Fold the fixups the author took:

```bash
git rebase -i --autosquash <base-sha>
```

`<base-sha>` is the commit the branch under review was cut from.

Then run the checks:

```bash
<the project's tests and lint, or the commands that stand in for them>
```

Report to the author what was taken, what was skipped, and anything the checks turned up.
