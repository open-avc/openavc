"""A human and an AI must be told the same thing about deleting one device.

``core/device_references.py`` answers "what still points at this device" for the
REST delete door and for the AI's ``check_references``/``delete_device``.
``findDeviceReferences`` in ``deviceUtils.ts`` answers it for the Builder's
delete confirmation. Two implementations exist on purpose: the dialog runs on
project state the user has not saved, which the server has never been sent.

Two implementations of one rule drift the moment either is edited, and the drift
is silent -- each side looks correct on its own. What is at stake is a delete:
one surface warns that a macro and four bound controls depend on the device
while the other says nothing, and which answer gets believed is whichever one
was read. So this pushes a corpus through both and compares **sentence for
sentence**, in order. The sentence is the deliverable -- "3 place(s)" teaches
nothing, ``Macro "Start Meeting": 2 step(s)`` can be acted on -- so a difference
in phrasing is a difference in the finding.

Not in the corpus: script references. The Builder cannot read a script file at
all, so there is nothing to compare; that half is a deliberate substring grep
covered by ``test_device_references.py``.

Every device, macro and page here is invented. This tests a platform capability.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from tests import gates

from openavc.core.device_references import find_device_references
from openavc.core.project_loader import ProjectConfig

OPENAVC_ROOT = Path(__file__).resolve().parents[1]
HARNESS = OPENAVC_ROOT / "tests" / "fixtures" / "device_references_parity_harness.cjs"
UTILS = (
    OPENAVC_ROOT / "openavc" / "web" / "programmer" / "src" / "views" / "devices"
    / "deviceUtils.ts"
)
NODE_MODULES = OPENAVC_ROOT / "openavc" / "web" / "programmer" / "node_modules"
ESBUILD_DIR = NODE_MODULES / "esbuild"


def _project(**sections) -> dict:
    return {"project": {"id": "parity", "name": "Parity", "description": ""}, **sections}


def _page(page_id: str, name: str, elements: list[dict]) -> dict:
    return {"id": page_id, "name": name, "elements": elements}


def _macro(macro_id: str, name: str, steps=(), triggers=()) -> dict:
    return {"id": macro_id, "name": name, "steps": list(steps), "triggers": list(triggers)}


# --- The corpus ------------------------------------------------------------
#
# One case per branch of the rule rather than one large project, so a parity
# failure names the branch that drifted instead of "something about a device".
# Every case is a (project, device_id) pair; the device being asked about is
# `probe` throughout, and `probe10` is its sibling -- the anchoring trap.

CASES: dict[str, dict] = {}


def _case(name: str, project: dict, device_id: str = "probe") -> None:
    CASES[name] = {"project": project, "device_id": device_id}


_case("empty_project", _project())

_case("group_membership", _project(device_groups=[
    {"id": "wall", "name": "Video Wall", "device_ids": ["probe", "other"]},
]))

_case("group_without_the_device", _project(device_groups=[
    {"id": "wall", "name": "Video Wall", "device_ids": ["other"]},
]))

_case("step_device_direct", _project(macros=[
    _macro("start", "Start Meeting", steps=[
        {"action": "device.command", "device": "probe", "command": "power_on"},
        {"action": "delay", "seconds": 2},
        {"action": "device.command", "device": "probe", "command": "input_hdmi1"},
    ]),
]))

_case("step_group_command", _project(
    device_groups=[{"id": "wall", "name": "Video Wall", "device_ids": ["probe"]}],
    macros=[_macro("shutdown", "Shut Down", steps=[
        {"action": "group.command", "group": "wall", "command": "power_off"},
    ])],
))

# A group.command naming a group that does not exist resolves to nothing -- the
# step is broken, but it is not a reference to THIS device.
_case("step_group_command_unknown_group", _project(macros=[
    _macro("shutdown", "Shut Down", steps=[
        {"action": "group.command", "group": "ghost", "command": "power_off"},
    ]),
]))

_case("step_state_key", _project(macros=[
    _macro("latch", "Latch", steps=[
        {"action": "state.set", "key": "device.probe.power", "value": True},
    ]),
]))

_case("step_condition_key", _project(macros=[
    _macro("guarded", "Guarded", steps=[
        {"action": "conditional",
         "condition": {"key": "device.probe.connected", "operator": "truthy"},
         "then_steps": [{"action": "delay", "seconds": 1}]},
    ]),
]))

_case("step_skip_if_key", _project(macros=[
    _macro("guarded", "Guarded", steps=[
        {"action": "delay", "seconds": 1,
         "skip_if": {"key": "device.probe.muted", "operator": "truthy"}},
    ]),
]))

_case("step_params_dynamic_reference", _project(macros=[
    _macro("mirror", "Mirror Volume", steps=[
        {"action": "device.command", "device": "other", "command": "set_level",
         "params": {"level": "$device.probe.volume"}},
    ]),
]))

_case("step_value_nested_tree", _project(macros=[
    _macro("stash", "Stash", steps=[
        {"action": "state.set", "key": "var.snapshot",
         "value": {"levels": ["var.a", "$device.probe.volume"]}},
    ]),
]))

# The outer conditional step does not itself name the device; the two nested
# ones do, and each is counted on its own.
_case("nested_branches_counted_separately", _project(macros=[
    _macro("either", "Either Way", steps=[
        {"action": "conditional",
         "condition": {"key": "var.mode", "operator": "eq", "value": "present"},
         "then_steps": [{"action": "device.command", "device": "probe", "command": "power_on"}],
         "else_steps": [{"action": "device.command", "device": "probe", "command": "power_off"}]},
    ]),
]))

# Everything below names probe10. Deleting probe must report none of it.
_case("sibling_id_never_matched", _project(
    device_groups=[{"id": "wall", "name": "Video Wall", "device_ids": ["probe10"]}],
    macros=[_macro("start", "Start", steps=[
        {"action": "device.command", "device": "probe10", "command": "power_on"},
        {"action": "state.set", "key": "device.probe10.power", "value": True},
    ], triggers=[
        {"id": "t1", "type": "state_change", "state_key": "device.probe10.connected"},
        {"id": "t2", "type": "event", "event_pattern": "device.disconnected.probe10"},
    ])],
    ui={"pages": [_page("home", "Home", [
        {"id": "vol", "type": "slider", "label": "Volume",
         "bindings": {"show.value": {"key": "device.probe10.volume"}}},
    ])]},
))

_case("trigger_state_key", _project(macros=[
    _macro("watch", "Watch", triggers=[
        {"id": "t1", "type": "state_change", "state_key": "device.probe.connected"},
    ]),
]))

_case("trigger_event_pattern", _project(macros=[
    _macro("watch", "Watch", triggers=[
        {"id": "t1", "type": "event", "event_pattern": "device.disconnected.probe"},
    ]),
]))

# A glob that fans out to every device is not a reference to this one.
_case("trigger_event_wildcard", _project(macros=[
    _macro("watch", "Watch", triggers=[
        {"id": "t1", "type": "event", "event_pattern": "device.disconnected.*"},
    ]),
]))

_case("trigger_conditions", _project(macros=[
    _macro("watch", "Watch", triggers=[
        {"id": "t1", "type": "schedule", "cron": "0 8 * * *", "conditions": [
            {"key": "var.occupied", "operator": "truthy"},
            {"key": "device.probe.connected", "operator": "truthy"},
        ]},
    ]),
]))

_case("binding_show_key", _project(ui={"pages": [_page("home", "Home", [
    {"id": "vol", "type": "slider", "label": "Volume",
     "bindings": {"show.value": {"key": "device.probe.volume"}}},
])]}))

_case("binding_do_device", _project(ui={"pages": [_page("home", "Home", [
    {"id": "pwr", "type": "button", "label": "Power On",
     "bindings": {"do.press": [{"action": "device.command", "device": "probe",
                                "command": "power_on"}]}},
])]}))

_case("binding_do_group", _project(
    device_groups=[{"id": "wall", "name": "Video Wall", "device_ids": ["probe"]}],
    ui={"pages": [_page("home", "Home", [
        {"id": "off", "type": "button", "label": "All Off",
         "bindings": {"do.press": [{"action": "group.command", "group": "wall",
                                    "command": "power_off"}]}},
    ])]},
))

# An unlabelled element is named by its id, on both sides.
_case("binding_element_without_label", _project(ui={"pages": [_page("home", "Home", [
    {"id": "led7", "type": "status_led",
     "bindings": {"show.state": {"key": "device.probe.connected"}}},
])]}))

# Master elements are repeated across pages and were invisible to every scanner
# before this module existed.
_case("master_element_binding", _project(ui={
    "pages": [_page("home", "Home", [])],
    "master_elements": [
        {"id": "banner", "type": "label", "label": "Room Status", "pages": "*",
         "bindings": {"show.text": {"key": "device.probe.status"}}},
    ],
}))

_case("master_element_without_the_device", _project(ui={
    "pages": [_page("home", "Home", [])],
    "master_elements": [
        {"id": "banner", "type": "label", "label": "Room Status", "pages": "*",
         "bindings": {"show.text": {"key": "var.status"}}},
    ],
}))

# Order is part of the contract: groups, then each macro's steps and triggers in
# turn, then page elements, then master elements. A caller renders the list as
# it comes.
_case("order_of_the_whole_walk", _project(
    device_groups=[{"id": "wall", "name": "Video Wall", "device_ids": ["probe"]}],
    macros=[
        _macro("start", "Start", steps=[
            {"action": "device.command", "device": "probe", "command": "power_on"},
        ], triggers=[
            {"id": "t1", "type": "state_change", "state_key": "device.probe.connected"},
            {"id": "t2", "type": "event", "event_pattern": "device.error.probe"},
            {"id": "t3", "type": "schedule", "cron": "0 8 * * *",
             "conditions": [{"key": "device.probe.power", "operator": "truthy"}]},
        ]),
        _macro("stop", "Stop", steps=[
            {"action": "device.command", "device": "probe", "command": "power_off"},
        ]),
    ],
    ui={
        "pages": [
            _page("home", "Home", [
                {"id": "vol", "type": "slider", "label": "Volume",
                 "bindings": {"show.value": {"key": "device.probe.volume"}}},
            ]),
            _page("av", "AV", [
                {"id": "pwr", "type": "button", "label": "Power",
                 "bindings": {"do.press": [{"action": "device.command", "device": "probe",
                                            "command": "power_toggle"}]}},
            ]),
        ],
        "master_elements": [
            {"id": "banner", "type": "label", "label": "Status", "pages": "*",
             "bindings": {"show.text": {"key": "device.probe.status"}}},
        ],
    },
))


def _toolchain_reason() -> str | None:
    if shutil.which("node") is None:
        return "node not installed"
    if not ESBUILD_DIR.is_dir():
        return "esbuild not installed (run `npm ci` in openavc/web/programmer)"
    if not HARNESS.is_file():
        return "device references parity harness missing"
    if not UTILS.is_file():
        return "deviceUtils.ts missing"
    return None


@pytest.fixture(scope="module")
def verdicts(tmp_path_factory) -> dict[str, tuple[list[str], list[str]]]:
    """Both sides' sentences, keyed by case, from the SAME bytes.

    The projects go through the Pydantic models first and the harness is fed the
    dump rather than the literal above, so a default the loader fills in reaches
    both sides identically. Comparing a model against a hand-written dict would
    test the loader, not the two walks.
    """
    reason = _toolchain_reason()
    if reason:
        gates.skip_or_fail(gates.NODE, reason)

    projects = {
        name: ProjectConfig.model_validate(case["project"]) for name, case in CASES.items()
    }
    dumps = {
        name: {"project": projects[name].model_dump(mode="json"),
               "device_id": case["device_id"]}
        for name, case in CASES.items()
    }

    cases_file = tmp_path_factory.mktemp("device-refs-parity") / "cases.json"
    cases_file.write_text(json.dumps(dumps), encoding="utf-8")

    proc = subprocess.run(
        ["node", str(HARNESS), str(UTILS), str(cases_file)],
        capture_output=True,
        text=True,
        cwd=str(OPENAVC_ROOT),
        env={**os.environ, "NODE_PATH": str(NODE_MODULES)},
        timeout=180,
    )
    if proc.returncode != 0:
        raise AssertionError(
            f"device references parity harness crashed (rc={proc.returncode}):\n{proc.stderr}"
        )
    try:
        builder = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:  # pragma: no cover - defensive
        raise AssertionError(
            f"could not parse harness output:\n{proc.stdout}\n---\n{proc.stderr}"
        ) from exc

    return {
        name: (
            [r.message for r in find_device_references(projects[name], CASES[name]["device_id"])],
            builder[name],
        )
        for name in CASES
    }


@pytest.mark.parametrize("case", sorted(CASES))
def test_both_surfaces_say_exactly_the_same_thing(case, verdicts) -> None:
    server, builder = verdicts[case]
    assert server == builder, (
        f"{case}: the server and the Builder disagree about what references this device.\n"
        f"  server : {server}\n"
        f"  builder: {builder}\n"
        "One of core/device_references.py and deviceUtils.ts has been edited alone."
    )


def test_the_corpus_actually_exercises_every_branch(verdicts) -> None:
    """A corpus of silent cases would pass the parity test and prove nothing."""
    server_sentences = [s for server, _ in verdicts.values() for s in server]
    for fragment in (
        'Device group "Video Wall"',
        'Macro "Start Meeting": 2 step(s)',       # counted, not merely found
        'Macro "Either Way": 2 step(s)',          # then_steps + else_steps
        'Macro "Watch" trigger: device.probe.connected',
        'Macro "Watch" trigger: device.disconnected.probe',
        'Macro "Watch" trigger condition: device.probe.connected',
        'UI page "Home" element "Volume"',
        'UI page "Home" element "led7"',          # unlabelled falls back to id
        'UI master element "Room Status"',
    ):
        assert any(fragment in s for s in server_sentences), (
            f"no case in the corpus produces {fragment!r} -- the branch that emits it "
            "is untested on both sides at once"
        )


def test_the_corpus_also_produces_silence(verdicts) -> None:
    """And a rule that reported everything would pass it too."""
    silent = {
        "empty_project",
        "group_without_the_device",
        "step_group_command_unknown_group",
        "sibling_id_never_matched",
        "trigger_event_wildcard",
        "master_element_without_the_device",
    }
    for case in silent:
        server, builder = verdicts[case]
        assert server == [] and builder == [], (
            f"{case} must find nothing: server={server} builder={builder}"
        )
