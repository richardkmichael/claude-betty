# Review plan -- <PR #N, or the branch under review>

<The PR title, or a line describing the branch.>

- Target: <PR URL, or branch name>
- Author: <author>
- Head at review time: `<sha>`
- Base, the commit the target branched from: `<sha>`
- Review branch: `<name>`

<One paragraph framing the review: how many items, the split between fixup and standalone, and
anything the reader needs before the table.>

## Scope

<What was in range, and what was found but deliberately left out. Findings in files the target does
not touch are real but belong to separate changes -- say so here and record them under Follow-ups.>

## Items

Pick order. Each item is independently cherry-pickable except where its section says otherwise.

| #  | Item                       | Kind       | Target    |
|----|----------------------------|------------|-----------|
| 1  | <one-line summary>         | fixup      | `<sha>`   |
| 2  | <one-line summary>         | standalone | --        |

## 1. <summary>

<What changes and why. Cite the evidence: file and line references, and what the code actually does
as opposed to what it is documented to do.>

<The alternatives explored and why they were rejected. This is the part the author's agent cannot
reconstruct from the diff, and the part most likely to be argued with.>

<Any pick-order dependency, and the reason for it. Say whether it is hard -- the unit is broken
without its predecessor -- or a preference.>

<What was verified, and how.>

## 2. <summary>

<As above.>

## Verification

<The project's tests, lint, and any manual checks, with what they cover and what they do not. If the
project has none, say so and list what was run instead.>

## Follow-ups

<Real findings that are out of scope for this branch, each with enough detail to act on later
without re-deriving it: what is wrong, where, and what the fix would involve.>
