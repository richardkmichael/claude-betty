---
name: matlab-docs
description: >
  Look up MATLAB / MathWorks docs by function name, mathworks.com URL,
  or local help-HTML path. Use in preference to WebFetch for any
  mathworks.com URL — WebFetch gets HTTP 403 from the MathWorks WAF;
  this skill uses curl. Triggers on "matlab docs", "mathworks docs",
  a pasted mathworks.com docs URL, or any time you need to consult MATLAB
  documentation.
allowed-tools: Bash(*/extract_matlab_doc.py:*) Read
argument-hint: "[target]"
---

# MATLAB Documentation Lookup

Resolve a function name, mathworks.com URL, or local help-HTML path to
clean documentation text.

## Why this skill exists

MathWorks fronts its docs with a WAF that returns HTTP 403 to Claude's
`WebFetch` tool.  So whenever you need MathWorks docs, route through this skill
instead of WebFetch.

A second reason: MATLAB ships substantial documentation locally, and the
user may have the Support Packages installed. Local lookups are faster, work
offline, and match the exact MATLAB release the user has -- so the
skill checks local first and only falls back to the network when the
function is not present locally.

## Usage

```bash
${CLAUDE_SKILL_DIR}/scripts/extract_matlab_doc.py <target>
```

`<target>` is one of:

- A function name. Bare (`profile`, `lsqnonlin`) or namespaced
  (`compiler.runtime.createDockerImage`). Case-insensitive.
- A mathworks.com URL, with or without an `#anchor` fragment.
- A path to a local `.html` file.

The script writes markdown-flavoured text to stdout (`#` headings,
fenced code blocks for syntax and examples). Exit status is non-zero
with a stderr message if the function cannot be resolved.

## When to use this skill

Reach for it whenever you need to know what a MATLAB function does,
what arguments or name-value pairs it accepts, or whether a better
alternative exists. Common situations:

- An unfamiliar function appears in a profile hotspot list and you
  need to decide whether it is the cause of slowness or merely
  incidental.
- Someone else's MATLAB code uses a function you do not have memorized
  and you want the spec before suggesting changes.
- The user pastes a mathworks.com URL and asks about it.
- You are about to recommend an alternative (e.g.
  `griddedInterpolant` instead of repeated `interp1`) and want to
  confirm the alternative actually has the option you are about to
  cite.

When the user gives you a function name, just run the script with the
name. When they give you a URL, run the script with the URL. There is
no need to download the page yourself first.

## When not to use this skill

- When you already have the function spec in your head and the user
  is not going to act on the recommendation. Don't burn a tool call
  to confirm something obvious.
- When the question is about MATLAB language semantics, not a specific
  function (e.g. "how does broadcasting work in MATLAB?") -- those are
  better answered from training knowledge or by directing the user to
  the relevant Mathworks user guide page (and if you do that, you can
  pass the user-guide URL to this skill).
- When the user has asked you not to consult docs.

## When the script can't resolve a target

The script writes a clear message to stderr (e.g.
`Could not find documentation for: foo`) and exits non-zero. The
correct response is to retry with a different target through the
**same script** — never to bypass it.

Things to try, in order:

- A different spelling: `lsqnonlin` vs `lsqNonlin`, etc. (the script
  is case-insensitive but exact-token).
- A more- or less-namespaced form: `createDockerImage` vs
  `compiler.runtime.createDockerImage`.
- A topic URL on `mathworks.com` (e.g. an "Install and Configure ..."
  page) instead of a function name.
- A direct path to a local help HTML file under
  `~/Documents/MATLAB/SupportPackages/<release>/help/` or under the
  MATLAB application bundle, if you can identify one. The script
  accepts an absolute file path as a target.

If none of those resolve, accept that the doc isn't easily findable
and say so to the user. The script's not-found result is information,
not a prompt to circumvent it.

## Constraints

- Do not modify the script for project-specific function names or
  paths. The locality of `~/Documents/MATLAB` is a user-environment
  fact, not a project convention.
- This skill is the **only** route to MATLAB docs. Once it returns a
  not-found message, do not fall back to:
  - `WebFetch` against any `mathworks.com` URL (HTTP 403 from the
    MathWorks WAF — that's exactly why this skill exists).
  - `curl` against `mathworks.com` (the script already does this
    internally with the right headers; rolling your own bypasses
    the skill's request hygiene).
  - `find` / `grep` / `ls` against `~/Documents/MATLAB/`, the MATLAB
    application bundle, or any other local doc store. The script
    already consults these. Searching them yourself produces
    user-visible noise (permission prompts, raw `Bash(find ...)`
    calls in the transcript) the user installed this skill to avoid.
  Each of these is a "fallback" that defeats the skill's purpose.
  Use the script with a different target instead.
- The script's output can be large for long doc pages. Quote the
  relevant section back to the user, do not paste the whole thing
  unless they ask.
