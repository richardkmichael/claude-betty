---
name: review-pr-companion
description: >
  Package a code review as a review branch -- small, reviewer-shaped fix commits
  plus a REVIEW_HANDOFF.md written for the author's coding agent -- pushed for the
  author to integrate, with no pull request opened. Use when reviewing a pull
  request or a local branch and you want to hand back working commits and the
  reasoning behind them, not just inline comments.
---

# Review-PR Companion

Turn a code review into a branch the author can integrate directly. Instead of
leaving comments and waiting for someone to re-derive the fixes, you deliver:

- a review branch of small, reviewer-shaped commits for the agreed fixes;
- `REVIEW_HANDOFF.md`, whose whole audience is the author's coding agent;
- `REVIEW_PLAN.md`, the numbered item list, build order, and follow-ups;
- optional follow-up specs for work deliberately left out of scope; and
- a drafted PR comment that points the author's agent at the handoff.

The premise: the fastest way to discuss code is a diff plus a briefing the other
side's agent can read.

Two rules hold throughout. Make no commits until the reviewer approves the plan.
Keep credentials in environment variables or `.env`, never in the branch.

## 1. Locate the target and create the review branch

The target is a GitHub pull request (a number or URL) or a local branch with no
PR. For a pull request, fetch the head commit through the PR ref using git:

```bash
git fetch origin pull/<N>/head        # refs/pull/<N>/head -- the PR's current tip
```

`refs/pull/<N>/head` is a read-only ref GitHub keeps on the base repo, pointing
at the PR's tip whether the source branch lives on this repo or on a fork. The
source branch by name (`git fetch origin <headRefName>`) only exists on the base
repo for same-repo pull requests, so prefer the PR ref -- it is fork-proof and
needs only `origin`.

The PR ref gives you the commit, not the branch name. Read the name and author
from `gh` when it is installed; it is optional, and the review commands in step 2
already need it:

```bash
gh pr view <N> --json number,headRefName,baseRefName,author,title
```

Name the review branch `review/pr-<N>-<slug>`, where `<slug>` is the source
branch with `/` replaced by `-` (a raw slash would nest the ref). Fall back to
`review/pr-<N>` when the name is unavailable. For a local branch under review,
branch off it as `review/<branch-slug>`.

```bash
git switch -c review/pr-42-hitchhikers-guide FETCH_HEAD
```

Add a worktree if the review benefits from an isolated checkout.

## 2. Review the diff and build the plan

Run the review in a subagent so its verbose output stays out of this thread.
Launch a general-purpose subagent to run `/code-review` or `/review-pr` (the
plugin commands), focused as the reviewer directs, and have it return the
findings. Fan out further subagents to verify each finding when the review
warrants it.

Ask the reviewer how deep to review, and pass that through as `/code-review`'s
level -- its effort scale from `low` to `max`. Any of those levels runs in the
subagent. The billed cloud tier, `/code-review ultra`, is the exception: it is
user-triggered, and neither the skill nor a subagent can launch it, so the
reviewer runs it and brings its findings into the triage below.

`/code-review` is built to post its verdict as a PR comment. That is off-message
here -- the reviewer writes their own comment and turns findings into commits --
so tell the subagent to report the findings back, not post them.

Triage the findings with the reviewer, then write `REVIEW_PLAN.md`: a numbered
table of items (issues and nits), each marked as a fixup (with its target commit)
or standalone; the build order and pick-order dependencies; and a follow-ups
section for work that is real but out of scope for this branch.

## 3. Approval gate

Present the plan and stop. Make no commits until the reviewer approves it. The
reviewer decides which findings become commits; this gate is what keeps the
branch a set of agreed changes rather than an imposition.

## 4. Implement the agreed fixes

Commit each agreed fix in the form that matches how it will integrate -- see
"The commits are units of feedback" below. In short: when a fix amends one
identifiable commit on the author's branch, commit it as a fixup to that commit
(`git commit --fixup=<sha>`); when it is genuinely new work with no single
target, commit it standalone with a final-form message. Run the project's tests
and lint after each commit, and never truncate the test output.

## 5. Write the handoff and follow-up docs

`REVIEW_HANDOFF.md` is addressed entirely to the author's coding agent, not to a
human, and it is a walkthrough script, not a static summary. It tells that agent
to walk the author through the branch unit by unit and apply what they accept:
for each item in plan order, explain what it is and why (from the reasoning in
this file), show the diff (`git show <sha>`), and ask the author to take or skip
it; cherry-pick each accepted unit onto the author's branch (a fixup keeps its
`fixup!` target); then `git rebase -i --autosquash <base>` to fold the fixups,
run the tests, and report what was taken and skipped. Every unit is a proposal --
the agent does not push, and the author can decline any of them.

So the file must carry: the agent's role and the proposal framing; how to orient
(`git fetch`, `git log`, `git diff`, `git show`); the ordered unit list with each
unit's fixup target or standalone mark and any dependency; the changes by area;
and the reasoning behind contested decisions, including rejected alternatives, so
the agent can answer "why not X" during the walk. `REVIEW_PLAN.md` from step 2
and any follow-up specs ride along.

Commit all the docs as one separate meta commit, clearly apart from the code
fixes, so the author can drop or ignore them without touching the fixes.

## 6. Publish

Push the branch and open no pull request:

```bash
git push -u origin review/pr-42-hitchhikers-guide
```

Draft -- but do not post -- a PR comment that points the author's agent at
`REVIEW_HANDOFF.md`, and let the reviewer post it. Verify the branch is up and
the doc links resolve; a branch name with a slash resolves fine in a
`blob/<branch>/FILE` URL, but confirm it rather than assume.

## The commits are units of feedback

The branch is not meant to be merged as-is. Each commit is a discrete piece of
feedback the author cherry-picks and folds into their own history, so the shape
follows the integration:

- Prefer a fixup to the target. When a fix amends one identifiable commit on the
  author's branch, and the region is not churned by a later commit, commit it as
  `git commit --fixup=<that commit>`. The message becomes `fixup! <target
  subject>`; cherry-pick preserves it, and the author's `git rebase -i
  --autosquash <base>` folds it into the right commit. Its message is discarded
  in the squash, so it needs no polish -- the reasoning lives in the handoff.
- Standalone otherwise. New work, a cross-cutting change, or a fix with no clean
  single target lands as its own commit with a final-form message, because it
  survives into the author's history as a real commit.

`REVIEW_PLAN.md` records, per item, whether it is a fixup (and its target) or
standalone, plus any pick-order dependency between items.

Where fixup-mode does not fit: the fixup's diff is computed against the branch
tip, so folding it into an earlier commit can conflict if a later commit touched
the same lines -- use it when the fix is localized to code the target owns.
Autosquash matches by subject, so a reworded target just leaves an unmatched
`fixup!` commit for the author to squash by hand. And it presumes the author
wants an autosquashed history; selectivity holds either way, since a rejected
unit is simply not cherry-picked.

## The handoff is written for the author's agent

The delivery model is that the author pulls the review branch and points their
own coding agent at `REVIEW_HANDOFF.md`. That file drives the session: the agent
walks the author through the branch, one unit at a time, and cherry-picks what
they accept -- it does not wait to be asked, and it treats every commit as a
proposal, not a mandate. Everything it needs to run that walk -- the orient
commands, the ordered unit list, the per-area summary, the decision reasoning --
lives in that one file.

## Guardrails

- No commits until the reviewer approves the plan.
- Credentials stay in environment variables or `.env`; never commit secrets.
- Commits matched to integration: fixup to the target when clean, standalone
  otherwise.
- Stage explicit paths; never `git add -A`.
- The handoff walks the author through the branch; every unit is a proposal.
- Tests and lint after changes; never truncate test output.
- Use `gh` and git for GitHub.
- Push the branch, open no pull request, and let the reviewer post the comment.
- Verify the pushed branch and that the handoff links resolve.
