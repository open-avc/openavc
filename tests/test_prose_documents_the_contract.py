"""The driver guide must describe the contract the registry declares.

``docs/creating-drivers.md`` is a hand-written description of the same
.avcdriver contract ``server/drivers/spec.py`` generates the schemas from, and
nothing else pins the two together. Every other mirror of that contract is
generated and CI-compared; this one is prose, and prose drifts silently. When
it does, the cost is not a red test — it is an author who reads the guide,
writes what it describes, and gets a driver the platform refuses (or, worse,
one that loads and quietly does nothing).

So this is a floor, not a spec check. It asserts the two things that actually
go wrong:

* **A field nobody documented.** Every field name in the registry appears
  somewhere in the guide. A new contract field has to be written up in the
  same change that adds it, or say here why it is deliberately undocumented.
* **A documented field that no longer exists.** Every key the guide presents
  as a driver field resolves in the registry — both the ones it names in prose
  (``options_state: <key>`` in a bullet) and the ones its YAML examples use.
  When a field is cut, the guide has to lose it in the same change.

Deliberately NOT generating the prose. Generated documentation is worse than
stale documentation: it reads like a schema dump, so nobody reads it, and the
explanations that make a guide worth having (when to reach for a field, what
it costs, what it pairs with) cannot come out of a registry. The floor buys
the mechanical half and leaves the writing to a person.

Three limits, recorded so nobody re-derives them:

* **"Documented" here means "mentioned".** A field name that appears once in
  an unrelated sentence passes. This catches the field nobody wrote up at all,
  which is the recurring failure; it cannot judge whether the writing is any
  good.
* **The prose sweep only sees key-shaped mentions** — a name in backticks
  followed by a colon, which is how both guides introduce a field. A stale
  field mentioned only as bare prose ("the options source key") is invisible
  to it. Widening the pattern to every backticked token drowns the signal in
  command names, state keys and file paths.
* **The YAML sweep only walks fences it can root.** A fence is checked when
  its top level looks like a driver definition; a fragment rooted deeper (a
  bare ``params:`` block) is skipped, and the count of skipped fences is
  asserted so the coverage cannot quietly fall to zero.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

import yaml

from openavc.drivers import spec
from tests.test_driver_contract_field_implemented import _contract_field_names

DOC_PATH = Path(__file__).resolve().parents[1] / "docs" / "creating-drivers.md"

# Contract fields the guide deliberately does not describe. Each entry says
# why, so a wrong one is visible rather than inherited. Keep this short: a
# field an author can set but cannot read about is normally a documentation
# bug, not an exemption.
UNDOCUMENTED = {
    "confidence": "Catalog metadata set by the driver's maintainer during "
                  "review, not by the author writing the driver. The "
                  "community repo's contributing guide owns it.",
    "compatible_models": "Catalog metadata for the device index. Written up "
                         "in the community repo's contributing guide, which "
                         "is where a driver gets submitted.",
    "deprecated": "Catalog lifecycle metadata. A driver is retired through "
                  "the community repo, so its contributing guide owns the "
                  "field and the process around it.",
    "replacement_id": "Catalog lifecycle metadata, set with 'deprecated' to "
                      "point at the driver that supersedes this one.",
    "notes": "Catalog metadata: a free-text maintainer note on the device "
             "index entry. Not something the runtime or the Builder reads.",
}

# Key-shaped tokens the guide writes that the registry has no field for. Each
# entry says what the name actually belongs to; without them the prose sweep
# reports every device-config key and Python attribute the guide mentions.
# Most are not driver-file keys at all. The exception is a key inside a block
# the registry deliberately leaves open — the simulator's state-machine
# transitions are validated by simulator/validate.py, not by the registry —
# and those entries say so, because an entry here is a claim about the
# contract's shape and a wrong one hides a real cut field.
NOT_A_CONTRACT_KEY = {
    "connected": "A Python driver attribute (self.connected), not a "
                 "definition key.",
    "reject": "A simulator state-machine transition key. The registry models "
              "that a machine has transitions, not what one contains; "
              "openavc/simulator/validate.py checks their shape.",
    "listen_port": "A device connection setting an OSC driver puts in "
                   "default_config, not a contract field.",
    "login": "The device's own Telnet prompt text, quoted in the "
             "authentication section.",
    "password": "Same: the device's Telnet prompt, and a config field name "
                "an author chooses.",
    "poll_interval": "A default_config key. The contract declares the "
                     "polling block; the cadence is a config value.",
    "tcp_keepalive": "A default_config key that turns on OS-level socket "
                     "keepalive.",
    "verify_timeout": "A default_config key that bounds the pre-connect "
                      "reachability probe.",
}

_FENCE = re.compile(r"^```yaml\n(.*?)^```", re.DOTALL | re.MULTILINE)

# A field the guide introduces, written the way both guides write one:
# `name:` or `name: value` inside backticks.
_KEY_IN_TICKS = re.compile(r"`([a-z][a-z0-9_]*):(?:\s|`)")


@lru_cache(maxsize=1)
def _doc_text() -> str:
    text = DOC_PATH.read_text(encoding="utf-8")
    assert len(text) > 50_000, "the driver guide went missing — the path is wrong"
    return text


def _documents(name: str) -> bool:
    return re.search(rf"\b{re.escape(name)}\b", _doc_text()) is not None


@lru_cache(maxsize=1)
def _keys_named_in_prose() -> frozenset[str]:
    return frozenset(_KEY_IN_TICKS.findall(_doc_text()))


def _resolve(node: object) -> dict:
    """Follow a registry $ref into DEFS."""
    for _ in range(10):
        if not (isinstance(node, dict) and "ref" in node):
            break
        node = spec.DEFS.get(node["ref"], {})
    return node if isinstance(node, dict) else {}


def _walk_example(value: object, node: object, path: str, unknown: list, seen: set) -> None:
    """Walk one YAML example beside the registry, flagging keys it cannot place.

    Only descends where the registry says the keys are fixed. An open map
    (``commands``, ``state_variables``, ``default_config`` …) holds
    author-chosen names, so its keys are skipped and only its values are
    followed into the value node the registry declares for them.
    """
    node = _resolve(node)
    if isinstance(value, dict):
        fields = node.get("fields")
        extra = node.get("extra")
        for key, child in value.items():
            if not isinstance(key, str):
                continue
            if isinstance(fields, dict):
                if key in fields:
                    seen.add(key)
                    _walk_example(child, fields[key], f"{path}.{key}", unknown, seen)
                elif isinstance(extra, dict):
                    _walk_example(child, extra, f"{path}.*", unknown, seen)
                else:
                    unknown.append(f"{path}.{key}".lstrip("."))
            elif isinstance(extra, dict):
                _walk_example(child, extra, f"{path}.*", unknown, seen)
    elif isinstance(value, list):
        items = node.get("items")
        if isinstance(items, dict):
            for item in value:
                _walk_example(item, items, f"{path}[]", unknown, seen)


@lru_cache(maxsize=1)
def _example_sweep() -> tuple[tuple[str, ...], frozenset[str], int, int]:
    """(keys the examples use that the contract has no place for, keys
    checked, fences rooted, fences skipped)."""
    unknown: list[str] = []
    seen: set[str] = set()
    rooted = skipped = 0
    root = {"fields": spec.FIELDS}
    for body in _FENCE.findall(_doc_text()):
        try:
            example = yaml.safe_load(body)
        except yaml.YAMLError:
            skipped += 1
            continue
        if not isinstance(example, dict) or not set(example) & set(spec.FIELDS):
            skipped += 1  # not a driver definition (a project snippet, a fragment)
            continue
        rooted += 1
        _walk_example(example, root, "", unknown, seen)
    return tuple(sorted(set(unknown))), frozenset(seen), rooted, skipped


def test_every_contract_field_is_documented() -> None:
    missing = sorted(
        name
        for name in _contract_field_names()
        if name not in UNDOCUMENTED and not _documents(name)
    )
    assert not missing, (
        f"declared in the driver contract but never mentioned in "
        f"{DOC_PATH.name}: {missing}. Write the field up, or — if the guide "
        f"deliberately leaves it to another document — add it to UNDOCUMENTED "
        f"with the reason."
    )


def test_every_key_the_guide_names_is_a_real_contract_field() -> None:
    stale = sorted(
        key
        for key in _keys_named_in_prose()
        if key not in NOT_A_CONTRACT_KEY and key not in _contract_field_names()
    )
    assert not stale, (
        f"{DOC_PATH.name} introduces these as driver fields, but the contract "
        f"does not declare them: {stale}. If the field was cut, cut it from "
        f"the guide too; if the name belongs to something else (a config key, "
        f"a Python attribute), add it to NOT_A_CONTRACT_KEY with what it is."
    )


def test_every_yaml_example_uses_only_contract_keys() -> None:
    unknown, _, _, _ = _example_sweep()
    assert not unknown, (
        f"YAML examples in {DOC_PATH.name} use keys the contract has no place "
        f"for: {list(unknown)}. An author copies an example verbatim, so a "
        f"key here that the loader refuses ships as a broken driver."
    )


def test_the_opt_out_lists_hold_nothing_stale() -> None:
    """Both lists may only shrink.

    Documenting a field makes its UNDOCUMENTED entry untrue, and a list that
    keeps untrue entries is how the exemptions become the place fields get
    parked. Failing the moment an entry stops being true means it has to go in
    the same change that makes it stale.
    """
    now_documented = sorted(name for name in UNDOCUMENTED if _documents(name))
    assert not now_documented, (
        f"now described in the guide — delete from UNDOCUMENTED: {now_documented}"
    )
    names = _contract_field_names()
    unknown_exemption = sorted(set(UNDOCUMENTED) - names)
    assert not unknown_exemption, (
        f"UNDOCUMENTED names fields the contract does not declare (renamed or "
        f"removed?): {unknown_exemption}"
    )
    became_real = sorted(key for key in NOT_A_CONTRACT_KEY if key in names)
    assert not became_real, (
        f"now real contract fields — delete from NOT_A_CONTRACT_KEY: {became_real}"
    )
    unused = sorted(set(NOT_A_CONTRACT_KEY) - _keys_named_in_prose())
    assert not unused, (
        f"NOT_A_CONTRACT_KEY names tokens the guide no longer writes: {unused}"
    )


def test_the_sweeps_still_reach_the_document() -> None:
    """Guard the extractors themselves.

    Every assertion above passes vacuously if a sweep stops finding anything,
    and a formatting change to the guide could do that silently — a renamed
    fence language, a switch from backticks to bold. These are floors on what
    each sweep must still see.
    """
    assert len(_contract_field_names()) > 150, "the registry walk collapsed"

    prose = _keys_named_in_prose()
    assert len(prose) > 30, f"the prose sweep found only {len(prose)} keys"
    for name in ("transport", "options_state", "child_entity_types"):
        assert name in prose or _documents(name), f"the guide stopped naming {name}"

    unknown, checked, rooted, skipped = _example_sweep()
    assert rooted > 20, f"only {rooted} YAML examples rooted as driver definitions"
    assert len(checked) > 50, f"the example sweep placed only {len(checked)} keys"
    # Fences it cannot root are fine and expected (fragments, project files);
    # this only pins that rooting has not silently become the rare case.
    assert skipped < rooted, f"{skipped} fences skipped vs {rooted} rooted"


def test_the_code_fences_are_balanced() -> None:
    """An odd number of ``` markers inverts every fence after the break.

    Found by an outside audit: the http_listener push example had lost its
    opening fence, so the whole back half of this guide was inside-out — the
    YAML examples read as prose and the prose read as YAML. That matters twice
    over. A reader sees a mangled page, and ``_example_sweep`` above walks
    exactly these fences, so its coverage silently moved to the wrong half of
    the document while every assertion in this file still passed.

    Counting is the whole check. It cannot say a fence opens in a sensible
    place, only that they pair up, which is the failure that actually happened.
    """
    fences = [
        (n, line)
        for n, line in enumerate(DOC_PATH.read_text(encoding="utf-8").splitlines(), 1)
        if line.startswith("```")
    ]
    assert len(fences) % 2 == 0, (
        f"{DOC_PATH.name} has {len(fences)} code-fence markers — an odd count, "
        f"so one block is unterminated and every fence after it is inverted. "
        f"Last few: {[n for n, _ in fences[-6:]]}"
    )
