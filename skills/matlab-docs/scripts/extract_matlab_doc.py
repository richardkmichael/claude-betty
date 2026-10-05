#!/usr/bin/env python3
"""Extract clean text from a MATLAB documentation page.

Resolves a function name, HTML file path, or mathworks.com URL to documentation
and prints LLM-friendly Markdown to stdout.  Abstracts local-vs-remote sources.

Resolution chain:
  1. HTTP(S) URL (e.g. mathworks.com page) -- fetched directly
  2. Local HTML (documentation Add-On or MATLAB install help)
  3. Name -> URL: the local referenceapi catalog, else MathWorks's doc-search
     API for products absent from the catalog, else a matlab/ref guess
  4. Remote: fetch the resolved URL, pinned to the installed release

Usage:
    extract_matlab_doc.py profile
    extract_matlab_doc.py lsqnonlin
    extract_matlab_doc.py /path/to/some_page.html
    extract_matlab_doc.py https://www.mathworks.com/help/.../foo.html
"""

from __future__ import annotations

import argparse
import functools
import glob
import http.client
import itertools
import json
import os
import re
import sys
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from pathlib import Path
from typing import NamedTuple

# --- Path discovery -----------------------------------------------------------

# Local documentation lives at the platform's default MATLAB locations. Each
# list holds the macOS, Windows, and Linux candidates; only the running
# platform's paths exist, so globbing them all and keeping the newest release
# resolves the right install without a platform switch. '*'/'R*' match any
# installed release.

# Help roots that contain matlab/ref/<fn>.html: the user documentation Add-On
# and the MATLAB install's own bundled help.
_HELP_ROOT_PATTERNS = [
    "~/Documents/MATLAB/SupportPackages/*/help",
    "~/Library/Application Support/MathWorks/MATLAB/SupportPackages/*/help",
    "C:/ProgramData/MATLAB/SupportPackages/*/help",
    "/usr/local/MATLAB/SupportPackages/*/help",
    "/Applications/MATLAB_R*.app/help",
    "C:/Program Files/MATLAB/R*/help",
    "/usr/local/MATLAB/R*/help",
]

# referenceapi catalog inside the MATLAB install (resolves toolbox-specific
# hrefs such as optim/ug/lsqnonlin.html).
_REFERENCEAPI_PATTERNS = [
    "/Applications/MATLAB_R*.app/help/docCatalog/referenceapi",
    "C:/Program Files/MATLAB/R*/help/docCatalog/referenceapi",
    "/usr/local/MATLAB/R*/help/docCatalog/referenceapi",
]


def _release_key(path: Path) -> tuple[int, str]:
    """Sort key ordering a path by its MATLAB release (newest first)."""
    m = re.search(r"R(20\d{2})([ab])", str(path))
    return (int(m.group(1)), m.group(2)) if m else (0, "")


def _installed_paths(patterns: list[str]) -> list[Path]:
    """Existing paths matching any pattern, de-duplicated, newest release first."""
    matches = {
        Path(match) for pattern in patterns for match in glob.glob(str(Path(pattern).expanduser()))
    }
    return sorted(matches, key=lambda path: (_release_key(path), str(path)), reverse=True)


def _find_help_roots() -> list[Path]:
    """All existing local help roots, newest release first."""
    return _installed_paths(_HELP_ROOT_PATTERNS)


def _find_referenceapi_dir() -> Path | None:
    """The newest install's referenceapi directory, or None."""
    return next(iter(_installed_paths(_REFERENCEAPI_PATTERNS)), None)


class _CatalogPage(NamedTuple):
    """A referenceapi item: a page and the lowercased names of the entities it documents."""

    help_location: str
    href: str
    names: tuple[str, ...]


@functools.cache
def _catalog() -> tuple[_CatalogPage, ...]:
    """Every page of the local referenceapi catalog in catalog order; empty without an
    install. Read once per process: a lookup consults the catalog for the local page and
    again for the remote URL.

    Entity names are lowercased here because the entries are mixed-case, like
    `compiler.runtime.createDockerImage`, while queries arrive lowercased.
    """
    api_dir = _find_referenceapi_dir()
    if api_dir is None:
        return ()

    pages = []
    for json_file in sorted(api_dir.glob("*.json")):
        try:
            data = json.loads(json_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        help_location = data.get("helpLocation", "")
        for item in data.get("refItems", []):
            href = item.get("href", "")
            if href:
                names = tuple(
                    entity.get("name", "").lower() for entity in item.get("refentity", [])
                )
                pages.append(_CatalogPage(help_location, href, names))
    return tuple(pages)


def _name_matches(name: str, query: str) -> bool:
    """Match a lowercased query against a lowercased referenceapi entity name.

    A bare name (`createdockerimage`) also matches its namespaced entry
    (`compiler.runtime.createdockerimage`); a namespaced query is accepted only
    as an exact match.
    """
    if not query:
        return False
    return name == query or ("." not in query and name.endswith("." + query))


def _referenceapi_matches(fn_name: str) -> list[tuple[str, str]]:
    """Return (help_location, href) pairs for referenceapi entries matching
    fn_name, best match first.

    A bare name can collide across products -- e.g. base MATLAB `fft`
    (matlab/ref/fft.html) and a toolbox method of the same name -- and with method pages that list
    the bare name as an entity too: containers.map.size.html carries `size` as well
    as `containers.Map.size`. Rank, in order: exact-name matches ahead of namespaced
    ones; a page named after the query (<q>.html, or double.<q>.html where MATLAB
    documents the numeric function) ahead of others; base MATLAB (matlab/...) ahead
    of toolbox locations; and pages that document no namespaced name ahead of method
    and property pages. Ties keep catalog order, the sort being stable. The page a
    bare name most likely means wins.
    """
    query = fn_name.lower()

    def rank(page: _CatalogPage) -> tuple[bool, bool, bool, bool]:
        is_exact = query in page.names
        is_named_page = page.href.rpartition("/")[2].lower() in (
            f"{query}.html",
            f"double.{query}.html",
        )
        is_base = f"{page.help_location}/{page.href}".startswith("matlab/")
        is_plain = not any("." in name for name in page.names)
        return (not is_exact, not is_named_page, not is_base, not is_plain)

    matches = [
        page for page in _catalog() if any(_name_matches(name, query) for name in page.names)
    ]
    return [(page.help_location, page.href) for page in sorted(matches, key=rank)]


def _resolve_local_path(fn_name: str) -> Path | None:
    """Try to find the local HTML file for a function.

    First checks matlab/ref/<fn>.html under each help root (covers most core
    functions). If not found, searches the referenceapi JSON files for the
    correct href (toolbox functions live at different paths, e.g.,
    optim/ug/lsqnonlin.html) and resolves it against the help roots.
    """
    help_roots = _find_help_roots()

    # Direct path for core MATLAB functions
    for root in help_roots:
        direct = root / "matlab" / "ref" / f"{fn_name}.html"
        if direct.is_file():
            return direct

    # Search referenceapi for the correct href -- toolbox functions and class
    # methods live at other paths (e.g. optim/ug/lsqnonlin.html), best first.
    for help_location, href in _referenceapi_matches(fn_name):
        for root in help_roots:
            page = root / help_location / href
            if page.is_file():
                return page
    return None


# --- HTML to Markdown ---------------------------------------------------------


class _Lines:
    """Markdown text built one line at a time.

    Inline text accumulates into the current line, whose whitespace is collapsed when the
    line ends, so a paragraph wrapped over several source lines comes out as one line. A
    verbatim block (a <pre>) keeps its own line breaks and indentation. Paragraph breaks
    never stack: the text carries at most one blank line between two lines.
    """

    def __init__(self) -> None:
        self._lines: list[str] = []
        self._fragments: list[str] = []
        # A heading's or list item's marker, prepended to its line when the line ends.
        self._prefix = ""

    def write(self, text: str) -> None:
        self._fragments.append(text)

    def end_line(self) -> None:
        text = " ".join("".join(self._fragments).split())
        if text:
            self._lines.append(self._prefix + text)
        self._fragments.clear()
        self._prefix = ""

    def start_line(self, prefix: str) -> None:
        """End the current line and mark the next one as a heading or list item."""
        self.end_line()
        self._prefix = prefix

    def break_paragraph(self) -> None:
        # A break at the start of a heading or list item, before any of its text, is
        # already there: the marker opens the line and the text must follow it on that
        # line (MathWorks wraps every list item's text in a <p>).
        if self._prefix and not "".join(self._fragments).strip():
            return
        self.end_line()
        if self._lines and self._lines[-1]:
            self._lines.append("")

    def write_verbatim(self, text: str) -> None:
        """A fenced code block, keeping the text's own line breaks and indentation."""
        self.break_paragraph()
        self._lines.append("```")
        self._lines.extend(line.rstrip() for line in text.strip("\n").splitlines())
        self._lines.append("```")
        self.break_paragraph()

    def text(self) -> str:
        self.end_line()
        return "\n".join(self._lines).strip("\n")


class _Enclosure:
    """Whether the parser is inside one element: open from its start tag until the end tag
    that closes it, counting nested elements of the same tag."""

    def __init__(self) -> None:
        self._tag: str | None = None
        self._depth = 0

    def __bool__(self) -> bool:
        return self._tag is not None

    def open(self, tag: str) -> None:
        self._tag, self._depth = tag, 1

    def start(self, tag: str) -> None:
        if tag == self._tag:
            self._depth += 1

    def end(self, tag: str) -> bool:
        """Note an end tag; True when it closes the enclosure."""
        if tag != self._tag:
            return False
        self._depth -= 1
        if self._depth:
            return False
        self._tag = None
        return True


# --- MathML to linear text ----------------------------------------------------

# MathWorks pages carry their formulas as MathML alone, with no LaTeX annotation or alt
# text, so a formula is rendered from its element tree into LaTeX-style linear text:
# `∇_{xx}^2 L(x, λ) = ∇^2 f(x) + ∑ λ_i ∇^2 c_i(x)`. The markup is flat and literal: a
# multi-letter name arrives as single-letter <mi> elements (`c`, `e`, `q`), a number as
# single-digit <mn> elements, and no <mo> carries a form attribute, so an operator's role
# (a binary or a unary minus, an opening or a closing bar) is read from its position. The
# display attribute is not read: the pages mark inline formulas display="block" as well,
# and the enclosing <p> or <span> already places the formula on its own line or in the
# sentence.

_TOKENS = frozenset({"mi", "mn", "mo", "mtext", "ms"})
_DROPPED = frozenset(
    {"annotation", "annotation-xml", "maligngroup", "malignmark", "mphantom", "mprescripts", "none"}
)

# What a rendered run is to its neighbour, at each of its two edges. The pair of kinds
# that meet between two runs decides whether a space separates them.
_ATOM = "atom"  # a letter, a number, an ellipsis: glues to another atom (`ceq`, `100`, `2x`)
_WORD = "word"  # a multi-letter name or text (`min`, `such that`), a function form (`sqrt(x)`)
_OPEN, _CLOSE = "open", "close"  # fences
_INFIX = "infix"  # a binary or relation operator, spaced on both sides
_PREFIX = "prefix"  # a unary sign, ∇, ∂: hugs its operand
_LARGE = "large"  # ∑, ∏, ∫, with their limits: a space follows
_POSTFIX = "postfix"  # a prime, a factorial: hugs its operand
_PUNCT = "punct"  # a comma or semicolon: a space follows
_TIGHT = "tight"  # a fraction slash, a decimal point: no space on either side
_COMPOUND = "compound"  # a fraction or a matrix: set apart from what it multiplies
_SCRIPTED = "scripted"  # the right edge of a scripted base: a following operand is set apart
_SPACE = "space"  # an explicit space in the markup, absorbed into the gap it sits in

_SIGNS = frozenset("+-−±∓–~")  # unary after an operator or a fence, binary after an operand
_BARS = frozenset("|‖∣∥")  # opening after an operator or a fence, closing after an operand
_OPERATOR_KINDS = {
    **dict.fromkeys("([{⟨〈⌊⌈⟦", _OPEN),
    **dict.fromkeys(")]}⟩〉⌋⌉⟧", _CLOSE),
    **dict.fromkeys("∇∂¬√∀∃△▵▽#", _PREFIX),
    **dict.fromkeys("∑∏∐∫∬∭∮∯∰⋃⋂⋁⋀⨁⨂⨀⨄", _LARGE),
    **dict.fromkeys("!′″‴'\"%°", _POSTFIX),
    **dict.fromkeys(",;", _PUNCT),
    **dict.fromkeys("/.^_", _TIGHT),
    **dict.fromkeys("…⋯⋮⋱⋰∞ℓ", _ATOM),
}  # every other operator is binary or a relation
# The kinds after which a sign is unary and a bar opens; None is the start of a group.
_OPERATOR_SIDE = frozenset({None, _OPEN, _INFIX, _PREFIX, _LARGE, _PUNCT, _SPACE})

# Operators that mark an accent when set over or under a base, by the LaTeX macro's name.
_ACCENTS = {
    "^": "hat", "ˆ": "hat", "¯": "bar", "‾": "bar", "˜": "tilde", "~": "tilde", "˙": "dot",
    "¨": "ddot", "⃛": "dddot", "→": "vec", "⇀": "vec", "︷": "overbrace", "︸": "underbrace",
}  # fmt: skip
_SCRIPT_MARKS = {
    "msub": "_", "msup": "^", "msubsup": "_^", "munder": "_", "mover": "^", "munderover": "_^"
}  # fmt: skip
# MathWorks's <mfenced> names two of its delimiters instead of giving the characters.
_FENCE_NAMES = {"anglebracketleft": "⟨", "anglebracketright": "⟩"}
# Zero-width spaces and the invisible operators (function application, times, separator).
_INVISIBLE = re.compile("[\u200b\u2060-\u2064\ufeff]")


class _Piece(NamedTuple):
    """A rendered run of a formula with the kind of each of its edges. `token` marks the run
    of a single token of the markup, which a script leaves unbraced (`x_i`, not `x_{i}`)."""

    text: str
    left: str
    right: str
    token: bool = False


_SPACER = _Piece("", _SPACE, _SPACE)


class _MathNode:
    """A MathML element: its tag, attributes and child elements, and for a token its text."""

    def __init__(self, tag: str, attributes: dict[str, str | None], text: str = "") -> None:
        self.tag, self.attributes, self.text = tag, attributes, text
        self.children: list[_MathNode] = []


class _Formula:
    """A <math> element's tree, built from the parser's events up to its end tag."""

    def __init__(self) -> None:
        self._root = _MathNode("math", {})
        self._open = [self._root]

    def start(self, tag: str, attributes: dict[str, str | None]) -> None:
        node = _MathNode(tag, attributes)
        self._open[-1].children.append(node)
        self._open.append(node)

    def end(self, tag: str) -> bool:
        """Close the innermost open element of this tag, and whatever is still open inside
        it; True once the <math> itself is closed. An end tag with no open element is noise."""
        if any(node.tag == tag for node in self._open):
            while self._open.pop().tag != tag:
                pass
        return not self._open

    def data(self, text: str) -> None:
        node = self._open[-1]
        if node.tag in _TOKENS:
            node.text += text
        elif text.strip():
            # Text the markup left outside any token element.
            node.children.append(_MathNode("mtext", {}, text))

    def render(self) -> str:
        return _string(self._root.children)


def _gap(left: str, right: str, tight: bool, spaced: bool) -> str:
    """The separator where two runs meet, from the kinds of their facing edges.

    Fences, punctuation and prefixes hug their operands (`f(x)`, `x, y`, `0.5`, `-x`, `∇f`).
    An explicit space in the markup, a binary or relation operator, a comma and a large
    operator separate (`a + b`, `∑ x_i`), but not inside a script, where `x_{i+1}` and
    `∑_{i=1}^n` stay tight. Two operands meet with a space (`x_i y`, `2 sqrt(x)`, `1/n (…)`)
    unless they glue into one name, number or application (`ceq`, `100`, `2x`, `)x`, `f(`).
    """
    if right in (_CLOSE, _PUNCT, _POSTFIX, _TIGHT) or left in (_OPEN, _PREFIX, _TIGHT):
        return ""
    if spaced:
        return " "
    if _INFIX in (left, right) or left in (_PUNCT, _LARGE) or right in (_PREFIX, _LARGE):
        return "" if tight else " "
    if right == _OPEN:
        return " " if left == _COMPOUND else ""
    if left in (_ATOM, _CLOSE) and right == _ATOM:
        return ""
    return " "


def _join(pieces: list[_Piece], tight: bool = False) -> str:
    """The runs as one string, each gap set by `_gap`; a spacer widens the gap it sits in."""
    text, previous, spaced = "", None, False
    for piece in pieces:
        if piece.left == _SPACE:
            spaced = True
            continue
        if previous is not None:
            text += _gap(previous.right, piece.left, tight, spaced)
        text += piece.text
        previous, spaced = piece, False
    return text.strip()


def _operator_kind(text: str, previous: str | None, form: str | None) -> str:
    """An <mo>'s kind: from the character, a sign or a bar being read from its form attribute
    when it has one and otherwise from its position."""
    if text in _SIGNS:
        form = form or ("prefix" if previous in _OPERATOR_SIDE else "infix")
        return {"prefix": _PREFIX, "postfix": _POSTFIX}.get(form, _INFIX)
    if text in _BARS:
        form = form or ("prefix" if previous in _OPERATOR_SIDE else "postfix")
        return _OPEN if form == "prefix" else _CLOSE
    return _OPERATOR_KINDS.get(text, _INFIX)


def _token(node: _MathNode, previous: str | None) -> list[_Piece]:
    """A token's run, with a spacer for whitespace at either end (an <mtext> can read
    ` such that `); a blank token is a spacer alone."""
    text = _INVISIBLE.sub("", node.text.replace("\xa0", " "))
    core = " ".join(text.split())
    if not core:
        return [_SPACER] if text else []
    if node.tag == "mo":
        kind = _operator_kind(core, previous, node.attributes.get("form"))
    elif len(core) == 1:
        # A punctuation mark set as text (<mtext>.</mtext>) still punctuates.
        kind = _OPERATOR_KINDS.get(core, _ATOM)
    else:
        kind = _ATOM if node.tag == "mn" else _WORD
    pieces = [_Piece(core, kind, kind, token=True)]
    if text[0].isspace():
        pieces.insert(0, _SPACER)
    if text[-1].isspace():
        pieces.append(_SPACER)
    return pieces


def _parts(node: _MathNode, count: int) -> list[_MathNode]:
    """The node's children, padded with empty groups up to `count` of them."""
    children = node.children
    return children + [_MathNode("mrow", {}) for _ in range(count - len(children))]


def _braced(pieces: list[_Piece]) -> str:
    """A script's text, set tight: bare when it is one token (`x_i`, `∇^2`), braced
    otherwise (`x_{i+1}`, `e^{1/n}`), and empty when the script is."""
    text = _join(pieces, tight=True)
    if len(pieces) == 1 and pieces[0].token and " " not in text:
        return text
    return f"{{{text}}}" if text else ""


def _scripted(node: _MathNode) -> _Piece:
    """A base with its scripts, `x_i`, `∇_{xx}^2`, `∑_{i=1}^n`, `f′`; an accent set over or
    under the base names it instead, `hat(x)`, `vec(v)`, `underbrace(Xβ)_{fixed}`."""
    marks = _SCRIPT_MARKS[node.tag]
    children = _parts(node, 1 + len(marks))
    # The scripts are the last children and the base is the rest: one element, or the
    # unwrapped sequence a page sometimes leaves there. An empty base, a script following
    # another base's scripts, hugs what precedes it.
    base, scripts = children[: -len(marks)], children[-len(marks) :]
    pieces = _group(base, None) or [_Piece("", _TIGHT, _TIGHT)]
    text, left, right = _join(pieces), pieces[0].left, pieces[-1].right
    # A scripted operator, ∑ or min, takes what follows as its operand; a scripted name
    # (`x_i y`, `hat(d)_k(p)`) does not.
    operator = left == _LARGE or (left == _WORD and pieces[0].token)
    for script, mark in zip(scripts, marks):
        parts = _group([script], None)
        token = parts[0] if len(parts) == 1 and parts[0].token else None
        if token and token.text in _ACCENTS:
            text, left, right = f"{_ACCENTS[token.text]}({text})", _WORD, _WORD
        elif token and token.left == _POSTFIX and mark == "^":
            # A prime set as a superscript is the prime itself: `f′`, not `f^′`.
            text, right = text + token.text, _POSTFIX
        elif braced := _braced(parts):
            text, right = text + mark + braced, _LARGE if operator else _SCRIPTED
    return _Piece(text, left, right)


def _attach(text: str, scripts: list[_MathNode]) -> str:
    """`text` with alternating sub- and superscripts appended, empty ones skipped."""
    for script, mark in zip(scripts, itertools.cycle("_^")):
        if braced := _braced(_group([script], None)):
            text += mark + braced
    return text


def _multiscripts(node: _MathNode) -> _Piece:
    """<mmultiscripts>: a base followed by pairs of sub- and superscripts, those after an
    <mprescripts/> set before it and hugging what precedes (`σ^2 I` when `σ` precedes)."""
    base, *scripts = _parts(node, 1)
    tags = [script.tag for script in scripts]
    split = tags.index("mprescripts") if "mprescripts" in tags else len(scripts)
    text = _attach(_string([base]), scripts[:split])
    prescripts = _attach("", scripts[split + 1 :])
    if prescripts:
        return _Piece(f"{prescripts} {text}", _TIGHT, _SCRIPTED)
    return _Piece(text, _WORD, _SCRIPTED)


def _enclosed(text: str) -> bool:
    """Whether one pair of parentheses spans the text, a bare name applied to it aside:
    `(a + b)`, `sin(x + y)`."""
    head, paren, body = text.partition("(")
    if not paren or not (head == "" or head.isalnum()):
        return False
    depth = 1
    for index, char in enumerate(body):
        depth += (char == "(") - (char == ")")
        if depth == 0:
            return index == len(body) - 1
    return False


def _bound(node: _MathNode) -> str:
    """A fraction's part, parenthesised when the slash would otherwise bind inside it:
    `1/n`, `∂F/∂x`, `(a + b)/(2c)`, `(x_i − x_j)^2/n`, `1/(a/b)`."""
    pieces = _group([node], None)
    text = _join(pieces)
    if len(pieces) == 1:
        loose = pieces[0].left == _COMPOUND
    else:
        loose = (" " in text or "/" in text) and not _enclosed(text)
    return f"({text})" if loose else text


def _fraction(node: _MathNode) -> _Piece:
    """`num/den`, each part bound as `_bound` says."""
    numerator, denominator = _parts(node, 2)[:2]
    return _Piece(f"{_bound(numerator)}/{_bound(denominator)}", _COMPOUND, _COMPOUND)


def _root(node: _MathNode) -> _Piece:
    """`sqrt(x)` for <msqrt>, whose children form the radicand; `root(n, x)` for <mroot>."""
    if node.tag == "msqrt":
        return _Piece(f"sqrt({_string(node.children)})", _WORD, _WORD)
    base, index = _parts(node, 2)[:2]
    return _Piece(f"root({_string([index])}, {_string([base])})", _WORD, _WORD)


def _fenced(node: _MathNode) -> _Piece:
    """<mfenced>: its children between its delimiters, comma-separated: `(a, b)`, `|+⟩`."""
    delimiters = (node.attributes.get("open", "("), node.attributes.get("close", ")"))
    opening, closing = (_FENCE_NAMES.get(name or "", name or "") for name in delimiters)
    body = ", ".join(_string([child]) for child in node.children)
    return _Piece(f"{opening}{body}{closing}", _OPEN, _CLOSE)


def _table(node: _MathNode, previous: str | None) -> _Piece:
    """Rows of cells. First in its context the table lays out the lines of an equation, one
    row each, which the page writes as separate lines; after an opening fence it is the
    body of a matrix, `[a, b; c, d]`, the fence being there already; elsewhere it brackets
    itself."""
    rows = [[_string(cell.children) for cell in row.children] for row in node.children]
    if previous is None:
        text = "\n".join(" ".join(filter(None, cells)) for cells in rows)
    else:
        body = "; ".join(", ".join(cells) for cells in rows)
        text = body if previous == _OPEN else f"[{body}]"
    return _Piece(text, _COMPOUND, _COMPOUND)


_COMPOSITES = {
    "mfrac": _fraction,
    "msqrt": _root,
    "mroot": _root,
    "mfenced": _fenced,
    "mmultiscripts": _multiscripts,
    **dict.fromkeys(_SCRIPT_MARKS, _scripted),
}


def _pieces(node: _MathNode, previous: str | None) -> list[_Piece]:
    """The runs of one element, given the kind of the run before it."""
    if node.tag in _TOKENS:
        return _token(node, previous)
    if node.tag == "mspace":
        return [_SPACER]
    if node.tag in _COMPOSITES:
        return [_COMPOSITES[node.tag](node)]
    if node.tag == "mtable":
        return [_table(node, previous)]
    if node.tag in _DROPPED:
        return []
    # A grouping element: <mrow>, <mstyle>, <mpadded>, <semantics> (its first child, the
    # presentation; the rest are annotations) and anything unknown.
    children = node.children[:1] if node.tag == "semantics" else node.children
    return _group(children, previous)


def _group(nodes: list[_MathNode], previous: str | None) -> list[_Piece]:
    """The runs of a sequence of elements, each seeing the kind of the run before it."""
    pieces: list[_Piece] = []
    for node in nodes:
        pieces += _pieces(node, previous)
        if pieces:
            previous = pieces[-1].right
    return pieces


def _string(nodes: list[_MathNode]) -> str:
    """Elements rendered on their own, as a script, a cell or a radicand is."""
    return _join(_group(nodes, None))


# The elements a page's text is read from, by preference: MathWorks's documentation
# container, anchored on its stable id rather than its tag (attributes such as xmlns
# precede the id, and the container is a <section> today but could become a <div>); then
# <main>; then the whole page.
_RANK_DOC_CONTENT, _RANK_MAIN, _RANK_PAGE = 2, 1, 0


def _content_rank(tag: str, attributes: dict[str, str | None]) -> int:
    if attributes.get("id") == "doc_center_content":
        return _RANK_DOC_CONTENT
    if tag == "main":
        return _RANK_MAIN
    return _RANK_PAGE


_HEADINGS = {"h1": "# ", "h2": "## ", "h3": "### ", "h4": "#### ", "h5": "##### ", "h6": "###### "}
_CELLS = ("td", "th")
_CODE = ("code", "pre")
_LINE_BLOCKS = frozenset(
    {
        "address", "article", "aside", "blockquote", "dd", "div", "dl", "dt", "figcaption",
        "figure", "footer", "header", "hr", "main", "nav", "ol", "section", "table", "tbody",
        "tfoot", "thead", "ul",
    }
)  # fmt: skip
# Elements that start a new line outside a table cell, so inside one they must at least
# separate the words on either side.
_BREAKING = _LINE_BLOCKS | _HEADINGS.keys() | {"p", "br", "li", "tr", *_CELLS}
_SKIPPED = ("script", "style")


class _MarkdownPage(HTMLParser):
    """Convert a MathWorks documentation page to Markdown.

    Headings become `#` lines, <code> inline code, <pre> fenced code, list items `- `
    lines and table rows `| ... |` lines. Each table cell is flattened onto its row's
    line (its paragraphs, lists and line breaks become inline), so a row keeps an
    option's name and its description together. A formula (MathML) becomes LaTeX-style
    linear text, see `_Formula`. Page furniture goes: scripts, styles and the "collapse
    all in page" toggle carry no content, and the release mark beside the title
    (<strong>R</strong>2026<strong>b</strong>), which would otherwise come out as
    "R 2026 b", becomes a `Release: R2026b` line of its own.
    """

    def __init__(self) -> None:
        super().__init__()
        self._out = _Lines()
        self._rank = _RANK_PAGE
        self._converting = True
        self._content = _Enclosure()
        self._skipped = _Enclosure()
        self._release_mark = _Enclosure()
        self._release: list[str] = []
        self._pre: list[str] | None = None
        self._formula: _Formula | None = None
        self._cells = 0
        self._code = 0

    @property
    def is_documentation(self) -> bool:
        """Whether the page carried MathWorks's documentation container."""
        return self._rank == _RANK_DOC_CONTENT

    @property
    def markdown(self) -> str:
        return self._out.text()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        rank = _content_rank(tag, attributes)
        if rank > self._rank:
            # A better container: convert it instead of what has been read so far.
            self._rank, self._converting = rank, True
            self._content.open(tag)
            self._out = _Lines()
        else:
            self._content.start(tag)
        if not self._converting:
            return
        if self._skipped:
            self._skipped.start(tag)
        elif tag in _SKIPPED or attributes.get("id") == "expandAllPage":
            self._skipped.open(tag)
        elif self._pre is not None:
            # Markup inside <pre> is syntax colouring; only a <br> means anything.
            if tag == "br":
                self._pre.append("\n")
        elif self._formula is not None:
            self._formula.start(tag, attributes)
        elif self._release_mark:
            self._release_mark.start(tag)
        elif tag == "span" and "release_mark" in (attributes.get("class") or ""):
            self._release_mark.open(tag)
        elif tag == "math":
            self._formula = _Formula()
        elif self._cells:
            self._start_in_cell(tag)
        else:
            self._start_in_text(tag)

    def handle_endtag(self, tag: str) -> None:
        if not self._converting:
            return
        if self._skipped:
            self._skipped.end(tag)
        elif self._pre is not None:
            if tag == "pre":
                self._out.write_verbatim("".join(self._pre))
                self._pre = None
        elif self._formula is not None:
            if self._formula.end(tag):
                self._write_formula(self._formula.render())
                self._formula = None
        elif self._release_mark:
            if self._release_mark.end(tag):
                self._out.break_paragraph()
                self._out.write("Release: " + "".join("".join(self._release).split()))
                self._out.break_paragraph()
                self._release.clear()
        elif self._cells:
            self._end_in_cell(tag)
        else:
            self._end_in_text(tag)
        if self._content.end(tag):
            self._converting = False

    def handle_data(self, data: str) -> None:
        if not self._converting or self._skipped:
            return
        if self._pre is not None:
            self._pre.append(data)
        elif self._formula is not None:
            self._formula.data(data)
        elif self._release_mark:
            self._release.append(data)
        else:
            self._out.write(data)

    def _write_formula(self, text: str) -> None:
        """A formula's lines (the rows of an equation layout) each go on a line of their own,
        except in a table cell, whose row holds the whole formula."""
        if self._cells:
            self._out.write(text.replace("\n", " "))
            return
        *lines, last = text.split("\n")
        for line in lines:
            self._out.write(line)
            self._out.end_line()
        self._out.write(last)

    def _start_in_text(self, tag: str) -> None:
        if tag in _CELLS:
            self._cells += 1
            self._out.write("| ")
        elif tag == "tr":
            self._out.end_line()
        elif tag == "table":
            self._out.break_paragraph()
        elif tag == "pre":
            self._pre = []
        elif tag == "code":
            self._open_code()
        elif tag in _HEADINGS:
            self._out.break_paragraph()
            self._out.start_line(_HEADINGS[tag])
        elif tag == "p":
            self._out.break_paragraph()
        elif tag == "li":
            self._out.start_line("- ")
        elif tag == "br" or tag in _LINE_BLOCKS:
            self._out.end_line()

    def _end_in_text(self, tag: str) -> None:
        if tag == "tr":
            self._out.write("|")
            self._out.end_line()
        elif tag == "table":
            self._out.break_paragraph()
        elif tag == "code":
            self._close_code()
        elif tag in _HEADINGS:
            self._out.break_paragraph()
        elif tag == "li" or tag in _LINE_BLOCKS:
            self._out.end_line()

    def _start_in_cell(self, tag: str) -> None:
        if tag in _CELLS:
            self._cells += 1
        if tag in _CODE:
            self._open_code()
        elif tag == "li":
            self._out.write(" - ")
        elif tag in _BREAKING:
            self._out.write(" ")

    def _end_in_cell(self, tag: str) -> None:
        if tag in _CELLS:
            self._cells -= 1
        if tag in _CODE:
            self._close_code()
        elif tag in _BREAKING:
            self._out.write(" ")

    # Syntax lines nest <code> inside <code> (the output names are marked up within the
    # signature); only the outermost pair becomes backticks.
    def _open_code(self) -> None:
        if not self._code:
            self._out.write("`")
        self._code += 1

    def _close_code(self) -> None:
        self._code -= 1
        if not self._code:
            self._out.write("`")


def _convert_page(raw_html: str) -> _MarkdownPage:
    page = _MarkdownPage()
    page.feed(raw_html)
    page.close()
    return page


# --- Remote fetch -------------------------------------------------------------

_MATHWORKS_BASE = "https://www.mathworks.com/help"
_TIMEOUT_SECONDS = 20

# mathworks.com answers urllib's bare default request with a 403: the help pages
# reject urllib's default User-Agent and accept any other, and the product pages
# (which a pasted URL can point at) also want a browser's Accept and
# Accept-Language. So the request names this script and asks for HTML in
# English, which curl's defaults happened to satisfy.
_REQUEST_HEADERS = {
    "User-Agent": "matlab-docs-skill/1.0",
    "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
    "Accept-Language": "en",
}

# MathWorks serves a styled "page not found" body when a doc URL is missing,
# sometimes with a 200. The phrase below is the visible text on that page;
# treat it as a failed fetch so the caller surfaces a not-found instead of the
# error page.
_NOT_FOUND_SENTINEL = "The page you were looking for does not exist"


def _fetch(url: str) -> str | None:
    """The body of a URL that answers 200, decoded as UTF-8, or None on any other
    status, a transport error or a timeout."""
    request = urllib.request.Request(url, headers=_REQUEST_HEADERS)
    try:
        with urllib.request.urlopen(request, timeout=_TIMEOUT_SECONDS) as response:
            if response.status != 200:
                return None
            return response.read().decode("utf-8", errors="replace")
    except (OSError, http.client.HTTPException):
        return None


def _fetch_page(url: str) -> str | None:
    """Fetch a documentation page. Returns the HTML on a 200, or None on a failed
    fetch or a body that is not a page: too short to be one, or MathWorks's
    styled not-found body served with a 200."""
    body = _fetch(url)
    if body is None or len(body) <= 1000 or _NOT_FOUND_SENTINEL in body:
        return None
    return body


def _installed_release() -> str | None:
    """Release token (e.g. 'R2025b') of the install whose catalog resolves the
    path, or None when no install is found. Read from the referenceapi directory
    the hrefs come from, so the pinned URL matches the release that produced it.
    """
    api_dir = _find_referenceapi_dir()
    m = re.search(r"R20\d{2}[ab]", str(api_dir)) if api_dir else None
    return m.group(0) if m else None


_TYPEAHEAD_URL = (
    "https://services.mathworks.com/typeahead/grouped"
    "?resources=latestdoc&site_domain=www&site_language=en"
    "&result_count=10&format=doc&q="
)


def _typeahead_path(fn_name: str) -> str | None:
    """Resolve a name to its canonical /help/ path via MathWorks's doc-search
    typeahead service, or None.

    Covers functions documented online but absent from the local catalog (a
    toolbox the user has not installed). Returns the path of the first 'doc'
    suggestion whose title equals the name, case-insensitive. This is an
    undocumented MathWorks endpoint; the caller falls back to a name-based guess
    on any failure, so a change there degrades gracefully.
    """
    body = _fetch(_TYPEAHEAD_URL + urllib.parse.quote(fn_name))
    if body is None:
        return None
    try:
        data = json.loads(body)
    except ValueError:
        return None
    query = fn_name.lower()
    for page in data.get("pages", []):
        for item in page.get("suggestions", []):
            title = "".join(item.get("title", []))
            if item.get("type") == "doc" and title.lower() == query and item.get("path"):
                return item["path"]
    return None


# Names worth sending to mathworks.com. A function name is an identifier, possibly
# namespaced; doc search also knows app names, which contain spaces. Anything else
# (shell syntax, paths) cannot name a page, so it is not looked up online.
_IDENTIFIER = re.compile(r"[a-z][a-z0-9_.]*")
_SEARCHABLE = re.compile(r"[a-z0-9][a-z0-9_. -]*")


def _remote_url_candidates(fn_name: str) -> list[str]:
    """MathWorks doc URLs to try for a function, best first.

    Resolves the path from the local referenceapi catalog (installed products);
    if the catalog has no entry (e.g. an uninstalled toolbox), asks MathWorks's
    doc search for the canonical path; failing that, guesses the core matlab/ref
    location. Pins each URL to the installed release with the unversioned (latest)
    URL as a fallback in case mathworks.com does not serve that release's page.
    """
    catalog_paths = (
        f"{help_location}/{href}"
        for help_location, href in _referenceapi_matches(fn_name)
        if help_location
    )
    path = next(catalog_paths, None)
    if path is None and _SEARCHABLE.fullmatch(fn_name):
        path = _typeahead_path(fn_name)
    if path is None and _IDENTIFIER.fullmatch(fn_name):
        path = f"matlab/ref/{fn_name}.html"
    if path is None:
        return []

    release = _installed_release()
    urls = []
    if release:
        urls.append(f"{_MATHWORKS_BASE}/releases/{release}/{path}")
    urls.append(f"{_MATHWORKS_BASE}/{path}")
    return urls


def _fetch_remote(fn_name: str) -> tuple[str, str] | None:
    """Fetch documentation from mathworks.com, trying the release-pinned URL
    first and the unversioned (latest) URL if that page is missing. Returns
    (html, url fetched), or None."""
    for url in _remote_url_candidates(fn_name):
        raw = _fetch_page(url)
        if raw:
            return raw, url
    return None


def _function_name_from_help_url(url: str) -> str | None:
    """Return the page base name from a mathworks.com help URL (e.g. 'interp1'
    from .../matlab/ref/interp1.html), or None if it is not such a URL.

    Used to recover from a 404 on a bare ref URL: some functions are documented
    at a different path (interp1 lives at double.interp1.html), which the name
    resolver knows even though the bare URL does not exist.
    """
    if "mathworks.com/help/" not in url:
        return None
    # Read the name from the path alone: links from MathWorks search carry a
    # query string (?s_tid=...) after the .html.
    page = urllib.parse.urlsplit(url).path.rstrip("/").rpartition("/")[2]
    if not page.endswith(".html"):
        return None
    return page[: -len(".html")] or None


# --- Main ---------------------------------------------------------------------


def _note_if_not_documentation(page: _MarkdownPage, source: str) -> None:
    """Say on stderr when a page has no documentation content, so the converted text
    of some other page (a product or search page) is not taken for a reference page."""
    if not page.is_documentation:
        print(
            f"note: {source} is not a documentation page; showing its main text",
            file=sys.stderr,
        )


def extract_doc(target: str) -> tuple[str, str] | None:
    """Extract documentation for a function name, file path, or HTTP(S) URL.

    Returns (clean text, source), the source being the file read or the URL
    fetched, or None if the function could not be found.
    """
    target = target.strip()

    # If target is an HTTP(S) URL, fetch and clean it directly. Strip any
    # fragment first.
    if target.startswith(("http://", "https://")):
        url = urllib.parse.urldefrag(target).url
        raw = _fetch_page(url)
        if raw:
            page = _convert_page(raw)
            _note_if_not_documentation(page, url)
            return page.markdown, url
        # A bare mathworks.com ref URL can 404 when the function is documented
        # at a different path (e.g. interp1 -> double.interp1.html). Recover by
        # re-resolving the page's function name through the normal name path.
        name = _function_name_from_help_url(url)
        return extract_doc(name) if name else None

    # An existing .html file is read directly. Any other file of that name,
    # such as a file called "profile" in the working directory, is not a page,
    # so the target falls through to name resolution.
    html_file = Path(target)
    if html_file.suffix.lower() in (".html", ".htm") and html_file.is_file():
        page = _convert_page(html_file.read_text(encoding="utf-8", errors="replace"))
        _note_if_not_documentation(page, target)
        return page.markdown, str(html_file.resolve())

    # A name given as a page name ("fmincon.html") that is not a file means the
    # function.
    fn_name = re.sub(r"\.html?$", "", target.lower())
    if not fn_name:
        return None

    # Try local HTML first
    local_path = _resolve_local_path(fn_name)
    if local_path:
        raw = local_path.read_text(encoding="utf-8", errors="replace")
        return _convert_page(raw).markdown, str(local_path)

    # Try remote
    fetched = _fetch_remote(fn_name)
    if fetched:
        raw, url = fetched
        return _convert_page(raw).markdown, url

    return None


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Extract clean text from MATLAB documentation pages."
    )
    parser.add_argument(
        "target",
        help="Function name (e.g. 'profile', 'lsqnonlin'), mathworks.com URL, "
        "or path to an HTML file",
    )
    args = parser.parse_args()
    if not args.target.strip():
        parser.error("target is empty: give a function name, URL or HTML file path")

    result = extract_doc(args.target)
    if result is None:
        print(f"Could not find documentation for: {args.target}", file=sys.stderr)
        sys.exit(1)

    # Name the source first: the release in a fetched URL, or the install a local
    # file belongs to, shows which release the page documents.
    text, source = result
    try:
        print(f"Source: {source}\n")
        print(text)
        sys.stdout.flush()
    except BrokenPipeError:
        # The reader stopped early (e.g. `| head`), which is not an error. Point
        # stdout at devnull so the flush at exit does not raise again.
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())


if __name__ == "__main__":
    main()
