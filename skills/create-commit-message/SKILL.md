---
name: create-commit-message
description: >
  Draft a commit message matching the project's conventions, for staged changes
  or for existing commits being reworded or squashed. Use whenever you are about
  to write a commit message: before any `git commit`, and when rewording or
  squashing commits. Does not stage or commit.
---

# Commit Message

Draft one commit message. The input is a diff: the staged changes for a new
commit, or the commits' own diff when rewording or squashing. The output is the
message. Staging and committing are outside this skill: what goes into the
commit has already been decided, and what happens to the message afterward is
up to whatever invoked the skill.

## 1. Read the change

```bash
git diff --cached            # a new commit: the staged changes
git diff --cached --stat     # files and scale, at a glance
git show <sha>               # rewording: the commit's own diff
git diff <first>^ <last>     # squashing: the combined diff of the range
```

The message must account for everything in the diff and nothing that isn't
there. If the change is plainly two unrelated concerns, say so rather than
writing a message that papers over the mix. That is a sign the commit should be
split, not that the message should stretch to cover it.

## 2. Match the project's convention

Take the format from these sources, in this order:

1. Stated conventions: CLAUDE.md, a `CONTRIBUTING` file, a commit template
   (`.gitmessage`), a commit-lint config (commitlint, gitlint). Where they
   speak, they win over everything below, including this skill's defaults.
2. The existing history, for whatever stated conventions leave open:

   ```bash
   git log --oneline -20   # subject style: prefixes, length, ticket refs
   git log -5              # full bodies: trailers, wrapping, tense
   ```

   Match what you observe: Conventional Commits (`feat(scope): ...`),
   subsystem prefixes (`net: ...`), ticket references (`[PROJ-123] ...`),
   subject length, body width, trailer format, capitalization, punctuation,
   tense.
3. The defaults in this skill, where neither settles a point. With no
   discernible convention, write an imperative subject under ~72 characters and
   a body wrapped at 72.

## 3. Fit the length to the change

The diff already shows what changed. The message explains what the diff cannot:
why. How long it runs depends on how much of that there is. A simple change may
need only the subject, or a sentence or two. A subtle bug, a non-obvious design,
or a complex implementation is often worth two or three paragraphs: what goes
wrong and under what conditions, why this change addresses the cause, and what a
future reader must know to avoid breaking it.

A message becomes verbose when it repeats what the diff already carries, not
when it runs long. Every sentence must tell the reader something the diff does
not. Do not narrate the diff file by file, list the functions touched, or
restate the subject in the body. A long body on a simple change usually means
one of these, or two unrelated concerns that belong in separate commits.

## 4. The subject says what; the body says why

The subject is a one-line summary of the change. The body is written for the
future developer who must modify this code and needs the intent behind it.

> "The log message that explains your changes is just as important as the
> changes themselves." -- the Git project

A good body does two things, in order:

1. State the problem in the present tense: what is wrong with the code as it
   stands. Write "The parser rejects empty input", not "used to reject". By
   convention the status quo is the code without this change, so there's no
   need to write "Currently".
2. Justify the change: why the result is better than the status quo.

Leave out alternatives that were considered and rejected, and the path that led
to the change. The message describes the change as it stands; that history
belongs in the pull request description.

Write the body in the imperative mood, as an instruction to the codebase: "Make
the parser accept empty input", not "I made" or "This patch makes". When the
body breaks into distinct points, use one bullet per point, each stating the
change and its reason together.

Keep it self-contained: summarize the relevant points of a discussion rather
than linking to a thread or issue that may rot.

Do not cite commits by hash, with one exception. A fix for a defect that a
commit already on the target branch introduced names that commit, in the
project's form where it has one (the kernel's `Fixes:` trailer), otherwise as
`abbreviated-hash (subject, date)`, e.g.
`f86a374 (pack-bitmap.c: fix a memleak, 2015-03-30)`. That commit has shipped,
so its hash is stable, and the citation tells a reader where the bug came from.

Never cite a commit on the same branch. Each commit on a branch is an atomic
unit that explains itself, and the branch reads in order, one commit after
another: a commit that uses a function introduced earlier describes its own
change, and the reader has already seen the function. Those hashes also change
on every rebase, so the citation soon points at nothing.

Avoid editorializing. State what the change does and why; do not characterize
the work ("comprehensive", "elegant", "long-standing gap") or describe what is
*not* in the commit. Use plain peer language a reviewer would use at a
whiteboard, not academic or business register.

## 5. Formatting code in the message

Follow stated conventions where they cover formatting. Otherwise use these
defaults; history is too inconsistent to infer code formatting from.

- Wrap code tokens (identifiers, commands, flags, paths, and short single-line
  statements like `export ENV=foo` or `let a = 10;`) in single backticks. They
  render as code on GitHub, GitLab, and most web forges, and a single backtick
  reads cleanly as plain text in `git log`.
- Use a fenced block (with a language specifier, Markdown-style) only when a
  snippet shows something the diff does not: a command to reproduce, a
  clarifying before/after, an error message, an external config. Never fence
  code that merely restates the diff. Most messages need only prose and inline
  backticks.

## Output

Return the finished message. Include the trailers the project's convention
calls for. The invoking agent may append trailers of its own, so don't add
trailers the convention doesn't call for.

## Examples

Adapted from real commits in PostgreSQL, Git, and the Linux kernel, rewritten to
follow the rules above.

<examples>

<example>

### Bug fix: problem, then justification

```
Improve plpgsql's error messages for incorrect %TYPE and %ROWTYPE

When one of these constructs references a nonexistent object, the whole
construct falls through to the core parser, which rejects it with an
unhelpful and misleading "syntax error". Make `plpgsql_parse_wordtype()`
and friends throw a useful error for incorrect input instead of
returning NULL.
```

The first sentence states the problem in the present tense. The second makes
the fix as an instruction. It stands on its own, with no link to a discussion.

</example>

<example>

### Refactor: the justification is what comes next

```
commit: allow parsing arbitrary buffers with headers

Only commits can be signed with headers, and tags need the same signing.
Factor the header parsing out of the commit code into
`parse_buffer_signed_by_header()` so both can use it.
```

A refactor's justification is often the work it enables. One clause says so;
the diff shows the rest.

</example>

<example>

### A short message is right for a simple change

```
btrfs: print correct subvol num if active swapfile prevents deletion

When a subvolume can't be deleted because it has an active swapfile, the
error message in `btrfs_delete_subvolume()` prints the number of the
parent rather than the target. Print the target's.
```

When the change is simple, a two-sentence body is the right length: it states
the problem precisely and stops. Pad nothing.

</example>

</examples>
