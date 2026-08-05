## Claude Review

### Claude kick-off

```
claude 'git fetch origin, then follow git show origin/<review-branch>:REVIEW_HANDOFF.md'
```

### Review summary

<The introduction: a few paragraphs telling the author what the review found and what they have to
deal with. Lead with whatever they hit first whichever units they take -- a hazard in the
integration, a break the rebase introduces, a conflict resolution that is correct but silently drops
something.>

<Show the evidence -- a diff fragment, a file and line -- and say what was run to establish it, and
what would not have caught it.>

<Omit this whole section when the branch and the table say everything.>

### Corrections on a branch

The review is <N> fixup commits on `<review-branch>`, each with a `fixup!` subject aimed at one
<PR> commit, so `git rebase -i --autosquash` folds each into place. Take or drop them individually.

https://github.com/<owner>/<repo>/compare/pull/<N>/head...<review-branch>

| Target    | What                                                          |
| --------- | ------------------------------------------------------------- |
| `<sha>`   | <one line, lower case, no trailing period>                     |
| `<sha>`   | <a second unit aimed at the same commit gets its own row>      |
| --        | <a standalone commit has no target>                            |

<The one or two units the author most needs to understand, in prose: what breaks, why the code reads
as correct, and what makes this case different from the one it was modelled on. Name it by its
position in the table -- "the first is substantive" -- rather than restating its summary.>

<Where a unit's root cause sits outside this change, say where it is and what fixing it there would
involve.>

- [`REVIEW_PLAN.md`](https://github.com/<owner>/<repo>/blob/<review-branch>/REVIEW_PLAN.md) on the
  review branch has the reasoning for each commit<, plus <N> items with nothing to apply --
  including: the one worth reading anyway>.

- [`REVIEW_HANDOFF.md`](https://github.com/<owner>/<repo>/blob/<review-branch>/REVIEW_HANDOFF.md) is
  written for a coding agent if you'd rather have one walk you through the branch.

<Scope, where part of the review is not something a commit on this branch can carry: a correction to
documentation in another repo, a finding in a file this change does not touch.>

<What the review was based on, and what it was not: which sources were read, which suite was run,
whether anything ran against a live environment.>
