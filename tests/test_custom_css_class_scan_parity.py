"""Both sides have to agree about what class a stylesheet defines.

The custom-UI review lives once, on the server, because a file save is already
a round trip -- there is deliberately no TypeScript twin of it, unlike
``page_review``. The **one** piece that does exist twice is this scan.

It has to. The Builder reads the sheet while somebody is typing, to offer the
class names it defines as suggestions (``customCssHelpers.ts``, feeding the
Custom classes control in the Properties panel). The AI write door reads the
same sheet to say "you named a class that does not exist". Neither can call the
other, and if they disagree then one surface offers a name the other reports as
missing -- with nothing on screen to say which one is right.

So the corpus below goes through both and the answers are compared exactly,
including order: the Builder shows them in the order the sheet first mentions
them, and a Python port that sorted would silently reorder somebody's list.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from tests import gates

from openavc.core.custom_ui_review import stylesheet_class_names

OPENAVC_ROOT = Path(__file__).resolve().parents[1]
HARNESS = OPENAVC_ROOT / "tests" / "fixtures" / "custom_css_class_scan_harness.cjs"
HELPERS = (
    OPENAVC_ROOT / "openavc" / "web" / "programmer" / "src" / "components" / "ui-builder"
    / "customCssHelpers.ts"
)
NODE_MODULES = OPENAVC_ROOT / "openavc" / "web" / "programmer" / "node_modules"
ESBUILD_DIR = NODE_MODULES / "esbuild"


#: Every case is a shape one side could plausibly get wrong on its own. The
#: comment on each says what breaks if it does.
CASES: dict[str, str] = {
    # The ordinary case, and the order it must come back in.
    "plain": ".brand { color: red }\n.wide { display: block }",
    # First mention wins, and a repeat is not a second entry.
    "repeated": ".b { color: red } .a { color: blue } .b { color: green }",
    # A declaration is not a selector: `border-radius: 0.5rem` holds a dot
    # followed by a word, and reading it as one offers `5rem` as a class.
    "declaration_with_a_dot": ".card { border-radius: 0.5rem; margin: 1.5rem }",
    # An at-rule's prelude is not a selector; the rules inside it are.
    "media_query": "@media (min-width: 37.5rem) { .wide { display: block } }",
    "nested_at_rule": "@supports (display: grid) { @media screen { .grid { display: grid } } }",
    # A quoted string is not a selector either.
    "content_string": '.tick::after { content: ".done" }',
    "attribute_selector": '.row[data-state=".on"] { color: red }',
    # A comment swallows what is inside it, and an unterminated one swallows
    # the rest of the sheet -- exactly as a browser does.
    "commented_out": "/* .old { color: red } */ .new { color: blue }",
    "unterminated_comment": ".before { color: red } /* .after { color: blue }",
    # Compound and descendant selectors, where several classes come from one rule.
    "compound": ".card.is-active .label { color: red }",
    "multiple_selectors": ".a, .b > .c { color: red }",
    # Names CSS allows and a naive pattern gets wrong.
    "leading_dash": ".-ghost { opacity: 0.5 }",
    "underscore": "._internal { display: none }",
    "digits_inside": ".col-2of3 { width: 66% }",
    # An element selector defines no class at all.
    "bare_element": "button { display: none }",
    "id_selector": "#main { color: red }",
    "empty": "",
    "whitespace_only": "   \n\t ",
    # A stylesheet somebody is halfway through typing.
    "unclosed_rule": ".half { color: red",
    "pseudo_state": ".btn:hover, .btn:focus-visible { outline: none }",
}


def _toolchain_reason() -> str | None:
    if shutil.which("node") is None:
        return "node not installed"
    if not ESBUILD_DIR.is_dir():
        return "esbuild not installed (run `npm ci` in openavc/web/programmer)"
    if not HARNESS.is_file():
        return "custom css class scan harness missing"
    if not HELPERS.is_file():
        return "customCssHelpers.ts missing"
    return None


@pytest.fixture(scope="module")
def builder_side(tmp_path_factory) -> dict[str, list[str]]:
    reason = _toolchain_reason()
    if reason:
        gates.skip_or_fail(gates.NODE, reason)

    cases_file = tmp_path_factory.mktemp("custom-css-parity") / "cases.json"
    cases_file.write_text(json.dumps(CASES), encoding="utf-8")

    proc = subprocess.run(
        ["node", str(HARNESS), str(HELPERS), str(cases_file)],
        capture_output=True,
        text=True,
        cwd=str(OPENAVC_ROOT),
        env={**os.environ, "NODE_PATH": str(NODE_MODULES)},
        timeout=180,
    )
    if proc.returncode != 0:
        raise AssertionError(
            f"custom css class scan harness crashed (rc={proc.returncode}):\n{proc.stderr}"
        )
    return json.loads(proc.stdout)


@pytest.mark.parametrize("case", sorted(CASES))
def test_both_sides_read_the_same_classes_out_of_the_same_sheet(case, builder_side):
    assert builder_side[case] == stylesheet_class_names(CASES[case]), (
        f"the Builder and the review disagree about the classes in {case!r}"
    )


def test_the_corpus_is_not_all_empty_answers(builder_side):
    """Parity over a corpus where both sides find nothing passes for free."""
    found = {name for names in builder_side.values() for name in names}
    assert len(found) >= 10, "the corpus barely exercises the scan"


def test_the_corpus_also_produces_silence(builder_side):
    """And parity where both sides find everything would be satisfied by two
    implementations that call every word a class."""
    silent = [case for case, names in builder_side.items() if not names]
    assert len(silent) >= 4, "no case in the corpus is supposed to find nothing"
