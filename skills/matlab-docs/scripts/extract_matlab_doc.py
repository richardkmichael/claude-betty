#!/usr/bin/env python
"""Extract clean text from a MATLAB documentation page.

Resolves a function name, HTML file path, or mathworks.com URL to documentation
and prints LLM-friendly Markdown to stdout.  Abstracts local-vs-remote sources.

Resolution chain:
  1. HTTP(S) URL (e.g. mathworks.com page) -- curl-fetched directly
  2. Local HTML (Support Packages install)
  3. Local referenceapi JSON (app bundle) -- for toolbox path resolution
  4. Remote: plain curl to mathworks.com

Usage:
    extract_matlab_doc.py profile
    extract_matlab_doc.py lsqnonlin
    extract_matlab_doc.py /path/to/some_page.html
    extract_matlab_doc.py https://www.mathworks.com/help/.../foo.html
"""

import argparse
import glob
import html
import json
import os
import re
import subprocess
import sys
from pathlib import Path


# --- Path discovery -----------------------------------------------------------

# Support Packages docs (user install, most complete)
_SUPPORT_PKG_PATTERN = os.path.expanduser(
    "~/Documents/MATLAB/SupportPackages/*/help"
)

# App bundle referenceapi (for resolving toolbox-specific hrefs)
_APP_BUNDLE_PATTERN = "/Applications/MATLAB_R*.app/help/docCatalog/referenceapi"


def _find_support_pkg_help() -> str | None:
    """Return the newest Support Packages help root, or None."""
    candidates = sorted(glob.glob(_SUPPORT_PKG_PATTERN), reverse=True)
    return candidates[0] if candidates else None


def _find_referenceapi_dir() -> str | None:
    """Return the newest app-bundle referenceapi directory, or None."""
    candidates = sorted(glob.glob(_APP_BUNDLE_PATTERN), reverse=True)
    return candidates[0] if candidates else None


def _name_matches(entity_name: str, query: str) -> bool:
    """Match a query against a referenceapi entity name.

    Handles two cases the entity-name field alone doesn't cover:
    case differences (entries are mixed-case like
    `compiler.runtime.createDockerImage`, queries arrive lowercased),
    and bare names (`createDockerImage` should resolve the namespaced
    entry). For a namespaced query, only exact matches are accepted.
    """
    en = entity_name.lower()
    q = query.lower()
    if en == q:
        return True
    if "." not in q and en.endswith("." + q):
        return True
    return False


def _resolve_local_path(fn_name: str) -> str | None:
    """Try to find the local HTML file for a function.

    First checks matlab/ref/<fn>.html (covers most core functions).
    If not found, searches the referenceapi JSON files for the correct href
    (toolbox functions live at different paths, e.g., optim/ug/lsqnonlin.html).
    """
    help_root = _find_support_pkg_help()
    if not help_root:
        return None

    # Direct path for core MATLAB functions
    direct = os.path.join(help_root, "matlab", "ref", f"{fn_name}.html")
    if os.path.isfile(direct):
        return direct

    # Search referenceapi JSON for the correct href
    api_dir = _find_referenceapi_dir()
    if not api_dir:
        return None

    for json_file in sorted(glob.glob(os.path.join(api_dir, "*.json"))):
        try:
            with open(json_file) as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            continue

        ref_items = data.get("refItems", [])
        help_location = data.get("helpLocation", "")

        for item in ref_items:
            for entity in item.get("refentity", []):
                if _name_matches(entity.get("name", ""), fn_name):
                    href = item.get("href", "")
                    if href:
                        full_path = os.path.join(help_root, help_location, href)
                        if os.path.isfile(full_path):
                            return full_path
    return None


# --- HTML extraction ----------------------------------------------------------

def _extract_doc_content(raw_html: str) -> str:
    """Extract the documentation section and convert to clean text."""
    # Target the doc_center_content section (both local and remote pages)
    match = re.search(
        r'<section\s+id="doc_center_content"[^>]*>(.*)',
        raw_html,
        re.DOTALL,
    )
    if match:
        content = match.group(1)
        # Find the closing </section> at the right nesting level
        depth = 1
        pos = 0
        while depth > 0 and pos < len(content):
            open_match = re.search(r"<section[\s>]", content[pos:])
            close_match = re.search(r"</section>", content[pos:])
            if close_match is None:
                break
            if open_match and open_match.start() < close_match.start():
                depth += 1
                pos += open_match.end()
            else:
                depth -= 1
                if depth == 0:
                    content = content[: pos + close_match.start()]
                pos += close_match.end()
    else:
        # Fallback: try <main>
        main_match = re.search(r"<main[^>]*>(.*?)</main>", raw_html, re.DOTALL)
        content = main_match.group(1) if main_match else raw_html

    # Strip script and style blocks
    content = re.sub(r"<script[^>]*>.*?</script>", "", content, flags=re.DOTALL)
    content = re.sub(r"<style[^>]*>.*?</style>", "", content, flags=re.DOTALL)

    # Convert headings to markdown
    content = re.sub(r"<h1[^>]*>(.*?)</h1>", r"\n# \1\n", content, flags=re.DOTALL)
    content = re.sub(r"<h2[^>]*>(.*?)</h2>", r"\n## \1\n", content, flags=re.DOTALL)
    content = re.sub(r"<h3[^>]*>(.*?)</h3>", r"\n### \1\n", content, flags=re.DOTALL)

    # Convert code tags to backticks
    content = re.sub(r"<code[^>]*>(.*?)</code>", r"`\1`", content, flags=re.DOTALL)

    # Convert <pre> blocks to fenced code
    content = re.sub(
        r"<pre[^>]*>(.*?)</pre>",
        lambda m: "\n```\n" + re.sub(r"<[^>]+>", "", m.group(1)) + "\n```\n",
        content,
        flags=re.DOTALL,
    )

    # Convert list items
    content = re.sub(r"<li[^>]*>", "\n- ", content)

    # Convert table rows to pipe-delimited (basic)
    content = re.sub(r"<tr[^>]*>", "\n| ", content)
    content = re.sub(r"<t[hd][^>]*>", " | ", content)
    content = re.sub(r"</t[hd]>", "", content)
    content = re.sub(r"</tr>", " |", content)

    # Convert <p> to paragraph breaks
    content = re.sub(r"<p[^>]*>", "\n\n", content)

    # Convert <br> to newlines
    content = re.sub(r"<br\s*/?>", "\n", content)

    # Strip all remaining HTML tags
    content = re.sub(r"<[^>]+>", " ", content)

    # Decode HTML entities
    content = html.unescape(content)

    # Collapse whitespace within lines, preserve paragraph structure
    lines = content.split("\n")
    cleaned = []
    for line in lines:
        line = re.sub(r"[ \t]+", " ", line).strip()
        cleaned.append(line)
    content = "\n".join(cleaned)

    # Collapse excessive blank lines
    content = re.sub(r"\n{3,}", "\n\n", content)

    return content.strip()


# --- Remote fetch -------------------------------------------------------------

_MATHWORKS_BASE = "https://www.mathworks.com/help"

# MathWorks responds 200 OK with a styled "page not found" body when a doc
# URL is missing. The phrase below is the visible text on that page; treat
# it as a failed fetch so the caller surfaces an error.
_NOT_FOUND_SENTINEL = "The page you were looking for does not exist"


def _curl_fetch(url: str) -> str | None:
    """Fetch a URL via curl. Returns HTML string or None on failure."""
    cmd = ["curl", "-sSL", "--max-time", "20", url]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=25)
        if result.returncode == 0 and len(result.stdout) > 1000:
            if _NOT_FOUND_SENTINEL in result.stdout:
                return None
            return result.stdout
    except (subprocess.TimeoutExpired, OSError):
        pass
    return None


def _resolve_remote_url(fn_name: str) -> str:
    """Construct a MathWorks URL for a function.

    Tries matlab/ref/<fn>.html first. If referenceapi JSON is available,
    uses it to find the correct toolbox-specific path.
    """
    # Check referenceapi for the exact href
    api_dir = _find_referenceapi_dir()
    if api_dir:
        for json_file in sorted(glob.glob(os.path.join(api_dir, "*.json"))):
            try:
                with open(json_file) as f:
                    data = json.load(f)
            except (json.JSONDecodeError, OSError):
                continue
            help_location = data.get("helpLocation", "")
            for item in data.get("refItems", []):
                for entity in item.get("refentity", []):
                    if _name_matches(entity.get("name", ""), fn_name):
                        href = item.get("href", "")
                        if href and help_location:
                            return f"{_MATHWORKS_BASE}/{help_location}/{href}"

    # Default: assume core MATLAB
    return f"{_MATHWORKS_BASE}/matlab/ref/{fn_name}.html"


def _fetch_remote(fn_name: str) -> str | None:
    """Fetch documentation from mathworks.com."""
    return _curl_fetch(_resolve_remote_url(fn_name))


# --- Main ---------------------------------------------------------------------

def extract_doc(target: str) -> str | None:
    """Extract documentation for a function name, file path, or HTTP(S) URL.

    Returns clean text or None if the function could not be found.
    """
    # If target is an HTTP(S) URL, fetch it directly via curl (bypasses
    # WebFetch's 403 from the MathWorks WAF). Strip any fragment first.
    if target.startswith(("http://", "https://")):
        raw = _curl_fetch(target.split("#", 1)[0])
        return _extract_doc_content(raw) if raw else None

    # If target looks like a file path, read it directly
    if os.path.isfile(target):
        with open(target) as f:
            return _extract_doc_content(f.read())

    fn_name = target.lower()

    # Try local HTML first
    local_path = _resolve_local_path(fn_name)
    if local_path:
        with open(local_path) as f:
            return _extract_doc_content(f.read())

    # Try remote
    raw = _fetch_remote(fn_name)
    if raw:
        return _extract_doc_content(raw)

    return None


def main():
    parser = argparse.ArgumentParser(
        description="Extract clean text from MATLAB documentation pages."
    )
    parser.add_argument(
        "target",
        help="Function name (e.g., 'profile', 'lsqnonlin') or path to HTML file",
    )
    args = parser.parse_args()

    text = extract_doc(args.target)
    if text is None:
        print(f"Could not find documentation for: {args.target}", file=sys.stderr)
        sys.exit(1)

    print(text)


if __name__ == "__main__":
    main()
