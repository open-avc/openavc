"""What a custom control will do wrong in *this* environment, read statically.

A script gets ``compile()`` before it lands and a driver gets its schema. A
custom control gets neither: there is no JS or CSS parser here, and there is
not going to be one -- ``core/custom_ui.py`` is stdlib-only by design so
``core`` never reaches into ``api``, and a parser dependency bought for one
tool is a bad trade. **So nothing here executes anything, and nothing that
calls it may imply otherwise.** The bytes landed; that is all a write knows.

Three things stand in for the syntax gate, and they are why the trade is
acceptable at all: the frame is sandboxed to ``allow-scripts`` and nothing
else, so a broken control is a dead box in a working panel; ``openavc:error``
is a one-line way for a control to say it broke from inside an opaque origin;
and the Builder's design canvas runs the thing live while its author watches.

What is left for this module is a lint, and every check earns its place by
catching something that fails **specifically here** -- which is exactly where a
model's training-data instincts are worst. A CDN script tag is normal on the
open web and renders as nothing in a room with no internet. ``localStorage``
is normal everywhere and *throws* in an opaque origin. ``fetch('/api/...')``
is normal in a web app and comes back 401 from inside a frame that carries no
credential. None of these are generic lint; a generic linter would pass every
one of them.

Everything WARNS. Nothing rejects.
---------------------------------
Same posture as ``page_review``, for the same reason: a rejection throws the
work away and costs a whole round trip, while a warning in the reply the caller
is already reading gets acted on in the turn it arrives. The refusals that do
exist belong to ``core/custom_ui.py`` -- path, extension, size, containment --
because those are about what may be *stored*, not about what will draw badly.

This review lives ONCE, on the server
-------------------------------------
``page_review`` exists twice, pinned message-for-message, because the Builder
needs a verdict on every drag with no round trip to get it. **A file save is
already a round trip.** So the IDE's editor shows these sentences by rendering
what the ``PUT`` response carries, and there is no TypeScript twin to keep
honest. The one piece that does cross is the stylesheet's class scan, which
already existed in the Builder (``customCssHelpers.ts``) to offer class-name
suggestions; :func:`stylesheet_class_names` is that scan, and
``tests/test_custom_css_class_scan_parity.py`` holds the two together.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

#: What a stylesheet finding names instead of a file path.
STYLESHEET = "the project stylesheet"

#: File types there is anything to say about. A ``.json`` a control reads, a
#: font, an image: stored, served, and none of this module's business.
REVIEWABLE_EXTENSIONS = frozenset({".html", ".htm", ".js", ".mjs", ".css"})

#: The extensions a page can be handed to as an entry point -- what an element's
#: ``custom_file`` or a page's ``custom_file`` names.
ENTRY_EXTENSIONS = frozenset({".html", ".htm"})


@dataclass(frozen=True)
class Finding:
    """One thing a control will do wrong, in one self-contained sentence.

    ``path`` is the file it is about (or :data:`STYLESHEET`), ``kind`` groups
    them for a caller that wants to count, and ``message`` is the whole finding
    -- because the consumer that matters reads prose and acts on it.
    """

    path: str
    kind: str
    message: str


# --- Reading references out of a file --------------------------------------
#
# One pass answers four checks: a reference that goes to the internet, one that
# starts at the server root, one that names a file nobody put in the folder, and
# a call to the platform's own API from inside a frame that holds no credential.

_ATTRIBUTE_REF = re.compile(r"""\b(?:src|href)\s*=\s*["']([^"']+)["']""", re.IGNORECASE)
_CSS_URL_REF = re.compile(r"""url\(\s*["']?([^"')]+)""", re.IGNORECASE)
_IMPORT_REF = re.compile(r"""@import\s+(?:url\(\s*)?["']([^"']+)["']""", re.IGNORECASE)
_CALL_REF = re.compile(r"""\b(?:fetch|importScripts)\(\s*["']([^"']+)["']""")

#: Schemes that never leave the page, so they are neither remote nor missing.
_INERT_PREFIXES = ("data:", "blob:", "#", "javascript:", "mailto:", "tel:")

_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
_HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
_LINE_COMMENT = re.compile(r"(?m)(?<![:\w])//[^\n\"']*$")


def _uncommented(path: str, source: str) -> str:
    """The source with its comments blanked, so a note about a CDN is not one.

    Rough on purpose: a `//` inside a string literal that also holds a quote
    would confuse it, which is why the line-comment pattern refuses to run past
    a quote. Being approximate is fine here -- the cost of a missed comment is
    one extra warning, and the cost of parsing CSS and JS properly is a
    dependency this module exists without.
    """
    text = _BLOCK_COMMENT.sub(" ", source)
    if _extension(path) in (".html", ".htm"):
        text = _HTML_COMMENT.sub(" ", text)
    if _extension(path) in (".js", ".mjs", ".html", ".htm"):
        text = _LINE_COMMENT.sub(" ", text)
    return text


def _extension(path: str) -> str:
    name = path.rsplit("/", 1)[-1]
    return ("." + name.rsplit(".", 1)[-1].lower()) if "." in name else ""


def _references(text: str) -> list[str]:
    """Every address this file asks a browser to load, in the order written."""
    found: list[str] = []
    seen: set[str] = set()
    for pattern in (_ATTRIBUTE_REF, _CSS_URL_REF, _IMPORT_REF, _CALL_REF):
        for match in pattern.finditer(text):
            ref = match.group(1).strip()
            if ref and ref not in seen:
                seen.add(ref)
                found.append(ref)
    return found


def _resolved(path: str, ref: str) -> str:
    """A relative reference, resolved against the file that makes it.

    Plain string work rather than ``PurePosixPath``: the answer is compared
    against the ``ui/`` listing, which is POSIX text either way, and a control
    that climbs out of its own folder with ``../`` is answered by returning
    something the listing cannot contain.
    """
    base = path.rsplit("/", 1)[0] if "/" in path else ""
    parts = [p for p in (base.split("/") if base else []) if p]
    for part in ref.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            if parts:
                parts.pop()
            else:
                return ref
        else:
            parts.append(part)
    return "/".join(parts)


def _listed(names: Iterable[str]) -> str:
    ordered = sorted(names)
    if not ordered:
        return "none yet"
    if len(ordered) <= 6:
        return ", ".join(ordered)
    return ", ".join(ordered[:6]) + f", and {len(ordered) - 6} more"


def _reference_findings(
    path: str, text: str, ui_files: set[str] | None,
) -> list[Finding]:
    findings: list[Finding] = []
    for ref in _references(text):
        lowered = ref.lower()
        if lowered.startswith(_INERT_PREFIXES):
            continue
        if lowered.startswith(("http://", "https://", "//")):
            findings.append(Finding(
                path, "remote_reference",
                f"{path} loads '{ref}' over the internet. A panel on a wall may have "
                f"no internet at all, and a remote script, font or image renders as "
                f"nothing with no error -- put the file in ui/ beside this one and "
                f"name it relatively.",
            ))
            continue
        if lowered.startswith("/api/"):
            findings.append(Finding(
                path, "uncredentialed_api_call",
                f"{path} calls the platform's API directly ('{ref}'). The control runs "
                f"in a sandboxed frame that carries no credential, so the request comes "
                f"back unauthorised -- ask the panel instead with an openavc:action "
                f"message (device.command, state.set, macro.run).",
            ))
            continue
        if ref.startswith("/"):
            findings.append(Finding(
                path, "absolute_path",
                f"{path} points at '{ref}', an absolute path. That resolves on the local "
                f"network and breaks through the cloud tunnel -- make it relative to "
                f"this file.",
            ))
            continue
        if ui_files is None:
            continue
        wanted = _resolved(path, ref.split("?")[0].split("#")[0])
        if wanted and wanted not in ui_files:
            findings.append(Finding(
                path, "dangling_reference",
                f"{path} loads '{ref}', which is not in the project's ui/ folder, so it "
                f"draws nothing. The files there are: {_listed(ui_files)}.",
            ))
    return findings


# --- What the sandbox makes fatal ------------------------------------------

#: Browser APIs that **throw** in a frame sandboxed without ``allow-same-origin``
#: -- which is every custom control, with no opt-in (that is the whole reason
#: author markup is a bounded risk here). The exception happens inside an opaque
#: origin, so nothing outside the frame ever sees it: the box just stops.
_SANDBOX_FATAL_RE = re.compile(
    r"\b(localStorage|sessionStorage|indexedDB)\b|\bdocument\s*\.\s*cookie\b"
)

#: Reaching for the panel's own window. ``parent.postMessage`` is the one door
#: and is deliberately absent from this pattern. Bare ``top`` is absent too --
#: the documented escape is ``window.top``, and half the controls ever written
#: hold a variable called ``top``.
_FRAME_ESCAPE_RE = re.compile(
    r"(?<![.\w$])(?:window\s*\.\s*(?:parent|top)|parent)\s*\.\s*(?!postMessage\b)(\w+)"
    r"|\bdocument\s*\.\s*referrer\b"
)

#: The same trap ``top`` was spared, one name over: a control that declares its
#: own ``parent`` shadows the global, and every read of it after that is local.
#: The pattern cannot require ``window.`` -- the documented bridge call is bare
#: ``parent.postMessage`` -- so the shadow has to be looked for instead. Found
#: on the first real AI-authored control (plan 11.11, T19), where a chip helper
#: took a ``parent`` element and cost the model a whole extra write.
#:
#: A declaration anywhere in the file silences the bare form for the whole file.
#: That is the deliberate half: there is no scope here to be more precise with,
#: and a file that has a local ``parent`` is a file whose bare reads cannot be
#: read as escapes without one. ``window.parent`` and ``window.top`` name the
#: global outright and go on firing either way, which is what keeps the check.
_PARENT_SHADOWED_RE = re.compile(
    r"""
      \b(?:const|let|var)\s+parent\b                    # const parent = ...
    | \b(?:const|let|var)\s+[\{\[][^;=]*?\bparent\b     # const {parent} = ...
    | \bfunction\b[^(){}]*\(\s*[^()]*?\bparent\b        # function draw(parent)
    | \(\s*[^()]*?\bparent\b[^()]*?\)\s*=>              # (el, parent) => ...
    | (?<![.\w$])parent\s*=>                            # parent => ...
    | \bcatch\s*\(\s*parent\b                           # catch (parent)
    """,
    re.VERBOSE,
)


def _shadows_parent(text: str) -> bool:
    """Does this file bind the name ``parent`` itself?"""
    return _PARENT_SHADOWED_RE.search(text) is not None


def _sandbox_findings(path: str, text: str) -> list[Finding]:
    findings: list[Finding] = []
    named: set[str] = set()
    for match in _SANDBOX_FATAL_RE.finditer(text):
        api = match.group(0).replace(" ", "")
        if api in named:
            continue
        named.add(api)
        findings.append(Finding(
            path, "sandbox_fatal_api",
            f"{path} uses {api}, which throws in a custom control: the frame is "
            f"sandboxed into its own opaque origin, so storage is unavailable and the "
            f"exception happens where nothing outside the box can see it. Keep what you "
            f"need in a var.* variable through an openavc:action state.set, or in a "
            f"JavaScript variable in the page.",
        ))
    reached: set[str] = set()
    shadowed = _shadows_parent(text)
    for match in _FRAME_ESCAPE_RE.finditer(text):
        what = match.group(0).replace(" ", "")
        if shadowed and what.startswith("parent."):
            continue
        if what in reached:
            continue
        reached.add(what)
        findings.append(Finding(
            path, "frame_escape",
            f"{path} reads {what}. A custom control cannot see the panel's page, its "
            f"session or the other controls -- the browser blocks it and says nothing. "
            f"parent.postMessage is the only way out, and openavc:init already carries "
            f"the state, theme and grant this control was given.",
        ))
    return findings


# --- Sizing, which is the difference between a control and a scrollbar ------

#: A style rule on the page itself, in a ``.css`` file or a ``<style>`` block.
#: What may sit between the name and the ``{`` is deliberately only selector
#: punctuation: ``if (body) {`` in a script is not a rule about the page, and
#: ``document.body.style`` is not one either (hence the lookbehind).
_BODY_RULE = re.compile(
    r"""(?<![\w.#-])(?:html|body)\b[\w\s.,:#*\[\]="'-]*\{([^{}]*)\}""",
    re.IGNORECASE | re.DOTALL,
)
_FIXED_SIZE = re.compile(
    r"\b(width|height|min-width|min-height)\s*:\s*(\d{2,}(?:\.\d+)?)px", re.IGNORECASE
)
_MARGIN_ZERO = re.compile(r"\bmargin(?:-\w+)?\s*:\s*0", re.IGNORECASE)


def _sizing_findings(path: str, text: str) -> list[Finding]:
    """The doc's own sizing rule, which is the one that bites on a wall.

    A control fills the element's box exactly, and content that does not fit
    **scrolls inside it** -- which on a panel means a scrollbar the room has to
    drag and content nobody can reach. Both halves of the rule are checked on
    the page's own root rules only: a fixed pixel size deeper inside a control
    is often exactly what the author meant.

    Read across the whole control rather than per file, because the page and the
    sheet that sizes it are usually two files -- checking ``index.html`` alone
    reports a missing ``margin: 0`` that is sitting in ``style.css``.
    """
    findings: list[Finding] = []
    bodies = [m.group(1) for m in _BODY_RULE.finditer(text)]
    if not bodies:
        return findings
    joined = "\n".join(bodies)
    if not _MARGIN_ZERO.search(joined):
        findings.append(Finding(
            path, "page_margin",
            f"{path} styles the page and never sets margin: 0. A browser's default body "
            f"margin draws as a gap inside the element's box and pushes the bottom of "
            f"the control out of it -- add `html, body {{ margin: 0; height: 100%; }}`.",
        ))
    fixed = _FIXED_SIZE.search(joined)
    if fixed:
        findings.append(Finding(
            path, "fixed_pixel_size",
            f"{path} sizes the page in pixels ({fixed.group(1)}: {fixed.group(2)}px). The "
            f"box is whatever was drawn in the Builder and it changes with the panel's "
            f"screen, so anything that does not fit scrolls out of reach -- size from "
            f"100% instead.",
        ))
    return findings


# --- The bridge, which is what makes it a control rather than a picture -----

_BRIDGE_INIT = "openavc:init"
_BRIDGE_ERROR = "openavc:error"


def review_file(
    path: str, source: str, *, ui_files: set[str] | None = None,
) -> list[Finding]:
    """Everything wrong with one file that can be seen from that file alone.

    ``ui_files`` is every path in the tree, or None for "no opinion" -- the same
    rule every injected lookup in ``page_references`` follows. A caller that
    cannot enumerate the folder must not turn every reference into a warning.
    """
    if _extension(path) not in REVIEWABLE_EXTENSIONS:
        return []
    text = _uncommented(path, source)
    findings = _reference_findings(path, text, ui_files)
    findings.extend(_sandbox_findings(path, text))
    return findings


def _control_sources(entry: str, sources: Mapping[str, str]) -> list[tuple[str, str]]:
    """The entry file plus the local scripts and sheets it pulls in.

    A control usually keeps its bridge in ``index.html``; one that keeps it in
    ``map.js`` is just as correct, and reporting "never listens for
    openavc:init" at a page whose script does exactly that is worse than saying
    nothing. So the whole-control checks read what the entry actually loads.
    """
    collected: list[tuple[str, str]] = [(entry, sources.get(entry, ""))]
    text = _uncommented(entry, sources.get(entry, ""))
    for ref in _references(text):
        if ref.lower().startswith(_INERT_PREFIXES) or "://" in ref or ref.startswith("/"):
            continue
        wanted = _resolved(entry, ref.split("?")[0].split("#")[0])
        if wanted in sources and wanted != entry:
            collected.append((wanted, sources[wanted]))
    return collected


def review_control(
    entry: str,
    sources: Mapping[str, str],
    *,
    holder: str | None = None,
    granted: Iterable[str] = (),
) -> list[Finding]:
    """What is wrong with a control taken as a whole, entry file and all.

    ``holder`` is how the element or page that points here reads mid-sentence
    (``element 'room_map'``); ``granted`` is the ids that element was given.
    Over-granting is the risk that is specific to a control somebody else wrote
    -- and it is the one thing here that is exactly detectable, because a
    control that never names an id cannot be using it.
    """
    if _extension(entry) not in ENTRY_EXTENSIONS:
        return []
    parts = _control_sources(entry, sources)
    whole = "\n".join(_uncommented(name, text) for name, text in parts)
    findings: list[Finding] = _sizing_findings(entry, whole)

    if _BRIDGE_INIT not in whole:
        findings.append(Finding(
            entry, "no_bridge",
            f"{entry} never listens for openavc:init, so the state, theme and grant the "
            f"panel sends it are all ignored and it draws the same thing whatever the "
            f"room is doing. Add a window 'message' listener, or leave it as decoration "
            f"on purpose.",
        ))
    if _BRIDGE_ERROR not in whole:
        findings.append(Finding(
            entry, "no_error_report",
            f"{entry} never reports its own errors. A control that throws without "
            f"window.onerror -> openavc:error is a blank rectangle on a wall panel with "
            f"no console to check. Add: window.onerror = (m) => parent.postMessage("
            f"{{type: 'openavc:error', message: String(m)}}, '*');",
        ))

    unused = [gid for gid in granted if gid and gid not in whole]
    if unused:
        who = holder or entry
        findings.append(Finding(
            entry, "over_granted",
            f"{who} is granted {', '.join(repr(g) for g in unused)}, which never "
            f"appear{'s' if len(unused) == 1 else ''} anywhere in {entry}. The control "
            f"cannot be using what it never names, and a grant is the whole reach model "
            f"-- take them out, or use them.",
        ))
    return findings


# --- The project stylesheet -------------------------------------------------
#
# The class scan below is a port of `stylesheetClassNames` in
# openavc/web/programmer/src/components/ui-builder/customCssHelpers.ts, which
# feeds the Builder's class-name suggestions. Two implementations exist because
# the Builder cannot call Python and the AI cannot call TypeScript; they are
# pinned against a shared corpus so "you named a class that does not exist"
# means the same thing on both sides.

_CLASS_IN_SELECTOR = re.compile(r"\.(-?[_a-zA-Z][-\w]*)")

#: A whole class name, anchored -- the same rule the panel's ``classList.add``
#: will accept, mirrored from ``invalidCssClassNames``.
VALID_CLASS_NAME = re.compile(r"^-?[_a-zA-Z][-\w]*$")


def _strip_comments(css: str) -> str:
    """``/* ... */`` removed. An unterminated one swallows the rest, like CSS."""
    out: list[str] = []
    i = 0
    while i < len(css):
        start = css.find("/*", i)
        if start == -1:
            out.append(css[i:])
            return "".join(out)
        out.append(css[i:start])
        end = css.find("*/", start + 2)
        if end == -1:
            return "".join(out)
        i = end + 2
    return "".join(out)


def _blank_strings_and_attributes(prelude: str) -> str:
    """Quoted strings and attribute selectors blanked out.

    ``content: ".foo"`` and ``[data-x=".bar"]`` both hold a dot followed by a
    word and neither is a class anyone can use. Blanking rather than deleting
    keeps everything else where it was.
    """
    out: list[str] = []
    quote: str | None = None
    in_attribute = False
    for ch in prelude:
        if quote:
            if ch == quote:
                quote = None
                out.append(ch)
            else:
                out.append(" ")
            continue
        if ch in ('"', "'"):
            quote = ch
            out.append(ch)
            continue
        if ch == "[":
            in_attribute = True
            out.append(ch)
            continue
        if ch == "]":
            in_attribute = False
            out.append(ch)
            continue
        out.append(" " if in_attribute else ch)
    return "".join(out)


def _style_rules(css: str | None) -> list[tuple[str, str]]:
    """The sheet's style rules as ``(selector, declarations)`` pairs.

    Only the text before a ``{`` is read as a selector, which is what keeps
    ``border-radius: 0.5rem`` from being offered as a class called ``5rem``. An
    at-rule's own prelude is skipped and the rules nested inside it are read
    normally, so a class that exists only inside a media query still counts.
    """
    if not css or not isinstance(css, str):
        return []
    text = _strip_comments(css)
    rules: list[tuple[str, str]] = []
    buffer: list[str] = []
    open_selector: str | None = None
    for ch in text:
        if ch == "{":
            prelude = "".join(buffer)
            open_selector = None if prelude.lstrip().startswith("@") else prelude
            buffer = []
            continue
        if ch == "}":
            if open_selector is not None:
                rules.append((open_selector, "".join(buffer)))
                open_selector = None
            buffer = []
            continue
        buffer.append(ch)
    return rules


def stylesheet_class_names(css: str | None) -> list[str]:
    """Every class the stylesheet defines, in the order it first mentions them."""
    found: list[str] = []
    seen: set[str] = set()
    for selector, _ in _style_rules(css):
        for match in _CLASS_IN_SELECTOR.finditer(_blank_strings_and_attributes(selector)):
            name = match.group(1)
            if name in seen:
                continue
            seen.add(name)
            found.append(name)
    return found


def css_class_list(value: str | None) -> list[str]:
    """The classes on one element. ``css_class`` is space-separated, like the
    attribute it becomes."""
    if not value or not isinstance(value, str):
        return []
    return [name for name in value.split() if name]


#: Selectors that hit controls the author never named. The panel re-marks every
#: declaration in this sheet ``!important`` so an ordinary rule works without
#: anybody having to know why it wouldn't -- which also means a rule on a bare
#: element name outranks what every control of that kind draws for itself.
_BARE_SELECTOR = re.compile(r"^[a-zA-Z][\w-]*$")
_GLOBAL_SELECTOR = re.compile(r"^(?:\*|:root|html|body)$")


def _selector_parts(selector: str) -> list[str]:
    return [part.strip() for part in selector.split(",") if part.strip()]


def review_stylesheet(
    css: str | None,
    *,
    used: Mapping[str, list[str]] | None = None,
) -> list[Finding]:
    """What the project stylesheet will do that its author did not ask for.

    ``used`` maps a class name to the element ids carrying it, or None for "no
    opinion" -- a caller that cannot enumerate the project must not report every
    class in the sheet as unused.
    """
    findings: list[Finding] = []
    text = css or ""

    stripped = _strip_comments(text)
    depth = stripped.count("{") - stripped.count("}")
    if depth > 0:
        findings.append(Finding(
            STYLESHEET, "unclosed_rule",
            f"The project stylesheet has {depth} unclosed rule"
            f"{'' if depth == 1 else 's'} -- a '{{' with no '}}' swallows everything "
            f"after it, so the rest of the sheet does nothing at all.",
        ))
    elif depth < 0:
        findings.append(Finding(
            STYLESHEET, "unclosed_rule",
            f"The project stylesheet has {-depth} '}}' more than it has '{{'. Everything "
            f"after the extra one is read as a selector rather than as a rule.",
        ))

    for selector, _ in _style_rules(text):
        for part in _selector_parts(selector):
            if _GLOBAL_SELECTOR.match(part.strip()):
                findings.append(Finding(
                    STYLESHEET, "global_selector",
                    f"'{part}' in the project stylesheet targets the whole panel. Every "
                    f"declaration in this sheet is applied !important, so this outranks "
                    f"the theme and everything the controls draw for themselves -- put "
                    f"the rule on a class and name that class in an element's css_class.",
                ))
            elif _BARE_SELECTOR.match(part.strip()):
                findings.append(Finding(
                    STYLESHEET, "bare_element_selector",
                    f"'{part}' in the project stylesheet hits every {part} element on "
                    f"every page, including the ones the panel draws itself. Every "
                    f"declaration here is applied !important, so this is not a "
                    f"suggestion -- put the rule on a class and name that class in an "
                    f"element's css_class.",
                ))

    for ref in _references(_uncommented("stylesheet.css", text)):
        if ref.lower().startswith(("http://", "https://", "//")):
            findings.append(Finding(
                STYLESHEET, "remote_reference",
                f"The project stylesheet loads '{ref}' over the internet. A panel on a "
                f"wall may have no internet at all, so the font or image simply never "
                f"arrives -- put the file in ui/ and point at it from there.",
            ))

    if used is not None:
        defined = stylesheet_class_names(text)
        for name, holders in sorted(used.items()):
            if name in defined:
                continue
            who = ", ".join(sorted(holders)[:4])
            findings.append(Finding(
                STYLESHEET, "undefined_class",
                f"{who} name{'s' if len(holders) == 1 else ''} css_class '{name}', which "
                f"the project stylesheet never defines, so nothing changes on the glass. "
                f"Add a '.{name}' rule, or take the class off the element.",
            ))
        for name in defined:
            if name not in used:
                findings.append(Finding(
                    STYLESHEET, "unused_class",
                    f"'.{name}' is defined in the project stylesheet and no element "
                    f"carries it. Put it on one with css_class, or drop the rule.",
                ))

    return findings


# --- Reading the folder, which every door that answers back has to do -------
#
# The checks above take text. Getting the text -- which files are in the tree,
# which of them make up one control, which element or page points at each entry
# and what it granted -- is the same job at the IDE editor's PUT and at the AI's
# write tool, and the two must not answer differently about the same save. So it
# is done once, here.


def _read_text(path: Path) -> str | None:
    """The file as text, or None when it is not text after all.

    A ``.js`` holding a stray byte is worth skipping rather than failing a save
    over: the review is advisory, and a file that cannot be decoded still runs
    (or doesn't) exactly as it did before.
    """
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _folder_of(relpath: str) -> str:
    return relpath.rsplit("/", 1)[0] if "/" in relpath else ""


def tree_sources(ui_dir: Path) -> dict[str, str]:
    """Every reviewable file in the tree, as ``{relative path: text}``."""
    from openavc.core.custom_ui import iter_files

    sources: dict[str, str] = {}
    for f in iter_files(ui_dir):
        rel = f.relative_to(ui_dir).as_posix()
        if _extension(rel) not in REVIEWABLE_EXTENSIONS:
            continue
        text = _read_text(f)
        if text is not None:
            sources[rel] = text
    return sources


def file_paths(ui_dir: Path) -> set[str]:
    """Every path in the tree, reviewable or not -- what a reference resolves against."""
    from openavc.core.custom_ui import iter_files

    return {f.relative_to(ui_dir).as_posix() for f in iter_files(ui_dir)}


def custom_file_uses(project: Any) -> list[Any]:
    """Every element and page in the project that points into ``ui/``.

    Returns ``page_references.CustomFileUse`` records. Imported where it is used
    rather than at module scope so the text checks above stay a leaf: this is
    the only function here that knows what a project is.
    """
    from openavc.ui.page_references import custom_file_references

    uses: list[Any] = []
    for page in getattr(getattr(project, "ui", None), "pages", None) or []:
        uses.extend(custom_file_references(page))
    return uses


def _holders_by_entry(project: Any) -> dict[str, tuple[str, tuple[str, ...]]]:
    """``{file: (how its holder reads, every id granted to it)}``.

    Two elements can point at one file, which is the whole point of a control
    written once and placed twice. Their grants are unioned: a warning that an
    id is never used has to be true of every element that granted it, or it is
    just wrong for the one that does use it.
    """
    by_entry: dict[str, tuple[str, tuple[str, ...]]] = {}
    for use in custom_file_uses(project):
        holder = f"{use.what} '{use.holder_id}'"
        existing = by_entry.get(use.file)
        if existing is None:
            by_entry[use.file] = (holder, tuple(use.granted))
        else:
            merged = tuple(
                g for g in use.granted if g in existing[1]
            )
            by_entry[use.file] = (existing[0], merged)
    return by_entry


def review_saved_file(
    ui_dir: Path, relpath: str, *, project: Any | None = None,
) -> list[Finding]:
    """Everything the review can say about a file that has just been written.

    The file itself, plus the control it belongs to: saving ``map.js`` can be
    what fixes -- or breaks -- the bridge in the ``index.html`` beside it, and a
    review that only ever looked at the file it was handed would report the page
    as bridgeless forever.
    """
    ui_files = file_paths(ui_dir)
    sources = tree_sources(ui_dir)
    findings: list[Finding] = []
    if relpath in sources:
        findings.extend(review_file(relpath, sources[relpath], ui_files=ui_files))

    holders = _holders_by_entry(project) if project is not None else {}
    folder = _folder_of(relpath)
    entries = []
    if _extension(relpath) in ENTRY_EXTENSIONS:
        entries.append(relpath)
    entries.extend(
        entry for entry in holders
        if entry not in entries and _folder_of(entry) == folder
        and _extension(entry) in ENTRY_EXTENSIONS
    )
    for entry in entries:
        holder, granted = holders.get(entry, (None, ()))
        findings.extend(review_control(
            entry, sources, holder=holder, granted=granted,
        ))
    return findings


def review_tree(ui_dir: Path, *, project: Any | None = None) -> list[Finding]:
    """The whole folder, file by file and control by control.

    What ``review_custom_ui`` answers, and deliberately unscoped: a control
    written forty calls ago is exactly the one whose warnings were lost.
    """
    ui_files = file_paths(ui_dir)
    sources = tree_sources(ui_dir)
    holders = _holders_by_entry(project) if project is not None else {}
    findings: list[Finding] = []
    for path in sorted(sources):
        findings.extend(review_file(path, sources[path], ui_files=ui_files))
    for path in sorted(sources):
        if _extension(path) not in ENTRY_EXTENSIONS:
            continue
        holder, granted = holders.get(path, (None, ()))
        findings.extend(review_control(path, sources, holder=holder, granted=granted))
    return findings


def stylesheet_class_usage(project: Any) -> dict[str, list[str]]:
    """``{class name: what carries it}``, across every page and master element.

    The other half of the stylesheet review: a class nothing carries is dead
    weight, and a class an element names that the sheet never defines is a
    control somebody styled and cannot see.
    """
    usage: dict[str, list[str]] = {}

    def note(name: str, holder: str) -> None:
        usage.setdefault(name, [])
        if holder not in usage[name]:
            usage[name].append(holder)

    ui = getattr(project, "ui", None)
    for page in getattr(ui, "pages", None) or []:
        for element in getattr(page, "elements", None) or []:
            for name in css_class_list(getattr(element, "css_class", None)):
                note(name, f"element '{getattr(element, 'id', '?')}'")
    for master in getattr(ui, "master_elements", None) or []:
        for name in css_class_list(getattr(master, "css_class", None)):
            note(name, f"master element '{getattr(master, 'id', '?')}'")
    return usage
