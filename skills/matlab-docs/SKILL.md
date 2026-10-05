---
name: matlab-docs
description: >
  Fetch the MathWorks documentation page for a MATLAB or toolbox function, or for a
  mathworks.com URL, as markdown: syntax, every name-value option with its default, and
  examples. Resolves names whose URL cannot be guessed (interp1 lives at double.interp1.html),
  preferring the locally installed docs. Use it whenever an answer or a code change depends on
  a MATLAB function's exact arguments, options, defaults or behaviour, such as "what options
  does fmincon take", "how do I make interp1 extrapolate" or "does griddedInterpolant support
  makima", and whenever a mathworks.com docs URL is pasted, even if the user does not ask for
  documentation: options and defaults change between releases, so check them rather than
  recall them. Also use it before recommending a MATLAB function, to confirm it has the option
  you cite. It does not run or profile MATLAB code; matlab-profile covers profiling.
allowed-tools: "Bash(${CLAUDE_SKILL_DIR}/scripts/extract_matlab_doc.py *), Read, Grep"
argument-hint: "<function | mathworks.com URL | file.html>"
metadata:
  version: "1.0"
---

# MATLAB documentation lookup

Run the script with the function name, URL or file path exactly as given:

```bash
${CLAUDE_SKILL_DIR}/scripts/extract_matlab_doc.py <target>
```

The target is one of:

- A function name, bare (`profile`, `lsqnonlin`) or namespaced
  (`compiler.runtime.createDockerImage`). Case does not matter.
- A mathworks.com URL. A `#anchor` fragment or a `?s_tid=...` query string is fine.
- A path to a local `.html` file.

Do not look up the URL or download the page first: finding the page is the script's job.

## What the script does

The documentation URL cannot be guessed from the name: interp1 is at `ref/double.interp1.html`,
not `ref/interp1.html`. The script reads MATLAB's own documentation index, the `referenceapi`
catalog inside the install, to map the name to its page. It ranks exact and base-MATLAB matches
first, so a bare name resolves to the page it most likely means. It reads the page from the
local documentation when that is installed, and otherwise fetches it from mathworks.com.

The online path is common. Recent MATLAB releases install the catalog but leave the pages on the
web, so the catalog supplies the URL for pages that exist only online. A fetched URL is pinned to
the installed release. When MathWorks does not serve that release's page, the script fetches the
latest release's page instead.

For a function from a toolbox that is not installed, and so absent from the catalog, the script
asks MathWorks's doc search for the page. If the search finds nothing, it tries
`matlab/ref/<name>.html`, and failing that reports not-found rather than a wrong page.

## Reading the output

The first line names where the page came from: `Source: <file or URL>`. A local file path
includes its release (`.../R2025b/help/...`), as does a pinned URL (`/help/releases/R2025b/...`).
A URL without `/releases/` is the latest release, which may be newer than the user's MATLAB;
a page fetched from mathworks.com also has a `Release:` line under its title.
Say so when the answer depends on whether an option exists in their release.

The page follows as markdown-style text: `#`, `##` and `###` headings, fenced code blocks for
examples, and table rows separated by `|`, each row on one line so an option's name and its
description stay together. The syntax lines come out as inline code, one per line. Formulas
come out as LaTeX-style linear text, `∇_{xx}^2 L(x, λ) = ∇^2 f(x) + ∑ λ_i ∇^2 c_i(x)`, with
matrices as `[a, b; c, d]`, fractions as `num/den` and a displayed system of equations one
equation per line. When a URL or file is not a documentation page (a product or search page),
the script says so on stderr and prints the page's main text.

Long pages exceed what one command result shows; fmincon's page is about 80,000 characters.
Claude Code then saves the whole output to a file and shows only its start and the file's path.
Grep that file for the heading or option you need (for example `^## Input Arguments`, or
`MaxIterations`) and Read the lines around it. Quote the relevant section to the user rather
than the whole page.

## When the script cannot resolve a target

It prints `Could not find documentation for: <target>` to stderr and exits non-zero. Retry
through the same script with a different target:

- If a namespaced name failed, try the bare name. A bare name already matches every namespace,
  so the reverse does not help.
- Try a mathworks.com topic URL (for example an "Install and Configure ..." page) instead of a
  function name.
- If the user gave you a local `.html` path, pass that.

If none of these resolves, tell the user the page could not be found. A not-found result is
information, not an obstacle to work around.

Do not fall back to either of these:

- `WebFetch` or `curl` on mathworks.com. The script finds the right URL, pins it to the installed
  release, reads local pages, detects MathWorks's not-found page and returns the full page text.
  WebFetch returns a small model's summary of the page, which can paraphrase or drop the exact
  syntax lines and option tables, and needs a URL you would have to know already.
- `find`, `grep` or `ls` over `~/Documents/MATLAB/`, the MATLAB application or any other local
  documentation. The script already searches them, and searching by hand fills the transcript
  with permission prompts and raw commands.

## When not to use it

- The question is about the MATLAB language rather than a function, such as how implicit
  expansion works. Answer from knowledge, or pass the relevant user-guide URL to the script.
- The user has asked you not to consult the documentation.
