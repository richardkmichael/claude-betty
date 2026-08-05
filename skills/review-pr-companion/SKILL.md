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
- `REVIEW_PLAN.md`, the reviewer's document: numbered items, the reasoning
  behind each, and follow-ups;
- `REVIEW_HANDOFF.md`, whose whole audience is the author's coding agent;
- optional follow-up specs for work deliberately left out of scope; and
- a comment on the pull request introducing all of it to the author, with the one
  command that starts their agent on the branch.

The premise: the fastest way to discuss code is a diff plus a briefing the other
side's agent can read.

Make no commits until the reviewer approves the plan.

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

Triage the findings with the reviewer, then write `REVIEW_PLAN.md` from
`references/REVIEW_PLAN.template.md`. It is the reviewer's document and it reads
standalone: identifiers, scope, an overview table, one numbered section per item,
verification, follow-ups.

Two fields in the header do work later and are easy to leave vague. The PR head
SHA is what the whole review is written against. The base -- `git merge-base
<base-branch> <pr-head>` -- is what the author rebases onto at the end, so record
the SHA, not a description of it.

Each item's numbered section is where the review's thinking lives: what changes
and why, the alternatives explored and rejected, any pick-order dependency, and
what was verified. Write it to be read on its own, because the handoff sends the
author's agent to one section at a time, and the item number is the only thing
tying the two documents together.

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

Target the root commit, never another fixup. `git commit --fixup=<a fixup's sha>`
takes that commit's subject verbatim and prefixes it again, giving
`fixup! fixup! <subject>`. Autosquash still folds it into the root -- it strips
prefixes until one matches, and it does not need the intermediate to be present
-- but the chained subject hides which commit the unit is really for, and it
means the fix was written on top of another fix, so the two probably need a
pick-order dependency in the plan.

Autosquash matches on the subject line, so a fixup whose root subject matches no
commit on the author's branch folds nowhere and survives the rebase as a stray.
Nothing warns about it. Check the whole set before writing the docs -- silence
means every fixup resolves:

```bash
"$CLAUDE_SKILL_DIR"/scripts/check-fixup-targets.sh <base> <pr-head>
```

It reports two things: a fixup with no target on the author's branch, and a
chained one to retarget. Fix what it finds and run it again.

## 5. Write the handoff and follow-up docs

`REVIEW_HANDOFF.md` is addressed entirely to the author's coding agent, not to a
human, and it is a walkthrough script, not a static summary. It carries the
operational detail and defers every explanation to `REVIEW_PLAN.md`.

Fill in `references/REVIEW_HANDOFF.template.md`. Its shape is the deliverable --
role and framing, orient commands, unit table, the per-unit loop, the finish --
so follow it rather than composing a new one. Three things it cannot enforce for
you:

- Write the base SHA out literally in the rebase command. An agent that has to
  derive it can pick the PR head instead, and the rebase then folds nothing.
- Mention `REVIEW_PLAN.md` exactly once, in the loop's first step, where the
  template already does, and name it through the ref rather than as a path. That
  single instruction carries the whole document, and the kick-off command does not
  check the branch out, so a bare filename is not guaranteed to resolve. Repeating
  the pointer, or copying rationale and follow-ups back into the handoff, is what
  makes the two files duplicate each other.
- Name the docs commit and say it is not a unit. It is the reviewer's notes
  rather than a proposal, and an agent walking the branch will otherwise offer
  it as one more unit or fold it into the author's history.

Commit all the docs as one separate meta commit, clearly apart from the code
fixes, so the author can drop or ignore them without touching the fixes.

## 6. Push the branch

Push the branch and open no pull request:

```bash
git push -u origin review/pr-42-hitchhikers-guide
```

GitHub cannot delete a pull request, so opening one would leave a closed PR in
the repo's list after every review, for everyone else to filter out. A compare
view gives the same side-by-side diff and commit list and leaves nothing behind.

## 7. Write and post the review comment

Fill in `references/REVIEW_COMMENT.template.md`. The comment is a high-level
introduction for the author and their exact next step -- a command they can copy
and paste, and summary prose. That is the whole job. The branch and the two
documents are the review; the comment is what gets the author to them. Its four
parts:

- Claude kick-off is the literal command the author pastes to put their agent on
  `REVIEW_HANDOFF.md`. It is first because it is the only thing the comment asks
  them to do. It reads the handoff out of the ref -- `git fetch origin, then
  follow git show origin/<review-branch>:REVIEW_HANDOFF.md` -- so the agent starts
  without checking the branch out or leaving the author's own. Substitute the real
  branch name; a placeholder that survives into the posted comment is not
  copy-pasteable.
- Review summary is the introduction: what the review found and what the author
  has to deal with, in prose they can read in a minute. Lead with whatever they hit
  first whichever units they take -- a hazard in the integration, a break the
  rebase introduces, a conflict resolution that is correct but silently drops
  something. Omit the section when the branch says everything.
- Corrections on a branch states what the branch is, links the compare view, and
  tables the units by the commit each one targets -- the author's own commit, not
  the review commit's SHA, because the target is what tells them where the fix
  lands. Two units aimed at one commit get two rows; a standalone unit has no
  target.
- The closing prose expands the one or two units whose point the author cannot get
  from a one-liner, then links the two documents and states what the review was
  based on and what it was not.

Summarize; do not reproduce. The table's one-liners plus the expansion of the
substantive units is the whole budget. `REVIEW_PLAN.md` holds the reasoning, the
alternatives weighed and the verification, and a comment that works through those
leaves the author no reason to open the branch.

The compare link is the plain URL, in `pull/<N>/head...<review-branch>` form --
the same string the handoff's orient section uses. It shows the review commits as
a diff against the PR head, which is the proposed delta and the thing to read
first, and it tracks the PR as it advances, which is what the handoff already
tells the reader to do when the branch has moved on; a form pinned to the
review-time SHA would contradict that instruction. For a local branch with no PR,
compare against the branch itself: `compare/<branch>...<review-branch>`.

The two documents are linked as `blob/<review-branch>/FILE`. Nothing links the
branch root -- the kick-off command names the branch and the blob links reach it.

Write the body to a file outside the repository. It is not part of the docs
commit, and posting from a file rather than an inline `--body` keeps the shell
from mangling the fenced code blocks and the table.

Show the reviewer the body and post it once they approve. A review that was
requested belongs on the PR as a review submission with a verdict rather than a
loose comment, so ask the reviewer which verdict:

```bash
gh pr review <N> --request-changes --body-file <path>   # or --approve, --comment
gh pr comment <N> --body-file <path>                    # when no review was requested
```

Verify before posting: the branch is up, the compare refs resolve, and the doc
links resolve. A branch name with a slash works in `blob/<branch>/FILE` and
`compare/...` URLs, but confirm it rather than assume.

```bash
gh api "repos/<owner>/<repo>/compare/pull/<N>/head...<review-branch>" --jq .status
```

Report the posted comment's URL back to the reviewer.

## The commits are units of feedback

The branch is not meant to be merged as-is. Each commit is a discrete piece of
feedback the author cherry-picks and folds into their own history, so the shape
follows the integration:

- Prefer a fixup to the target. When a fix amends one identifiable commit on the
  author's branch, and the region is not churned by a later commit, commit it as
  `git commit --fixup=<that commit>`. The message becomes `fixup! <target
  subject>`; cherry-pick preserves it, and the author's `git rebase -i
  --autosquash <base>` folds it into the right commit. Its message is discarded
  in the squash, so it needs no polish -- the reasoning lives in the plan.
- Standalone otherwise. New work, a cross-cutting change, or a fix with no clean
  single target lands as its own commit with a final-form message, because it
  survives into the author's history as a real commit.

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
proposal, not a mandate.

The split between the two documents follows from their audiences. The plan is the
reviewer's: the thinking, the alternatives weighed, the verification, written
once and read by whoever wants it. The handoff is the author's agent's: what to
do, in what order, against which SHAs. Reasoning in the handoff is reasoning the
plan already holds, so it stays in the plan and the handoff cites the item
number. The two join on that number, not on prose either one repeats.

## Guardrails

- No commits until the reviewer approves the plan.
- Commits matched to integration: fixup to the target when clean, standalone otherwise.
- Fixups target the root commit; `check-fixup-targets.sh` is clean before the docs are written.
- Stage explicit paths; never `git add -A`.
- All three deliverables are filled in from their templates in `references/`.
- The handoff walks the author through the branch; every unit is a proposal.
- Reasoning lives in the plan; the handoff names it once and cites item numbers.
- The handoff writes out the rebase base SHA and excludes the docs commit.
- Tests and lint after changes; never truncate test output.
- Use `gh` and git for GitHub.
- Push the branch and open no pull request.
- The comment is an introduction and a next step: a copy-pasteable kick-off
  command with the real branch name in it, and summary prose.
- The comment summarizes; the plan's reasoning is not reproduced in it.
- The comment's table keys each unit to the author's commit it targets.
- Show the comment body, ask which verdict, and post only once approved.
- Verify the pushed branch, the compare refs, and the doc links resolve.
