"""The sub-page container: what validates it, what switching it sends.

A sub-page is a page of type ``subpage`` that draws only inside a
``subpage_container`` element, shown by the ``ui.subpage`` action (the same
spelling for the binding action, the macro step and the WS frame). It is not
a navigation destination: a ``page_nav`` or ``ui.navigate`` aimed at one is a
wrong turn the reviews name. A switch names two ids of two kinds -- container
and sub-page -- and both are checked against their own kind, since a regular
page named in a switch is the wrong action picked, not an unknown id.

子页面容器：校验规则与切换动作的发送。子页面是 page_type 为 "subpage" 的
页面，只绘制在 subpage_container 元素内，由 ui.subpage 动作显示（绑定动作、
宏步骤与 WS 帧同一拼写）。它不是导航目的地：page_nav 或 ui.navigate 指向
它是走错了路，评审会点名。切换动作指名两类 id——容器与子页面——各自按其
类校验，因为切换动作里填普通页面是选错了动作，而不是未知 id。

Every device and macro here is invented. This tests a platform capability.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from openavc.core.macro_validation import validate_macro
from openavc.core.project_loader import MacroConfig, ProjectConfig, UIElement, load_project
from openavc.core.ui_events import DISPATCHED_ACTIONS
from openavc.ui.page_references import reference_findings

PAGES = {"main", "media_sub"}
SUBPAGES = {"media_sub"}
CONTAINERS = {"cam_box"}
DEVICES: set[str] = set()
MACROS: set[str] = set()


def _press(*actions) -> dict:
    return {"bindings": {"do": {"press": list(actions)}}}


def _project(main_elements: list | None = None, **ui_extra) -> ProjectConfig:
    return ProjectConfig.model_validate({
        "project": {"id": "sub", "name": "Sub", "description": ""},
        "ui": {
            "settings": {},
            "pages": [
                {
                    "id": "main", "name": "Main", "layouts": [],
                    "elements": main_elements if main_elements is not None else [{
                        "id": "cam_box", "type": "subpage_container",
                        "default_page": "media_sub",
                    }],
                },
                {"id": "media_sub", "name": "Media", "page_type": "subpage", "elements": []},
            ],
            **ui_extra,
        },
    })


# --- Macro step validation -----------------------------------------------------


def _warned(caplog, steps) -> str:
    with caplog.at_level("WARNING", logger="openavc.core.macro_validation"):
        refusal = validate_macro(steps, [], _project())
    assert refusal is None
    return " ".join(r.message for r in caplog.records)


def test_a_switch_to_a_real_container_and_subpage_is_not_warned(caplog) -> None:
    steps = [{"action": "ui.subpage", "container": "cam_box", "page": "media_sub"}]
    assert "not found" not in _warned(caplog, steps)


def test_a_switch_naming_an_unknown_container_is_warned(caplog) -> None:
    steps = [{"action": "ui.subpage", "container": "ghost_box", "page": "media_sub"}]
    logged = _warned(caplog, steps)
    assert "sub-page container 'ghost_box' not found in project" in logged


def test_a_switch_naming_a_regular_page_is_warned_as_the_wrong_action(caplog) -> None:
    # "main" exists -- the finding is about its KIND, not its existence.
    # "main" 存在——发现项针对的是它的类，而不是它是否存在。
    steps = [{"action": "ui.subpage", "container": "cam_box", "page": "main"}]
    logged = _warned(caplog, steps)
    assert "page 'main' is not a sub-page in the project" in logged


def test_a_switch_naming_an_unknown_subpage_is_warned(caplog) -> None:
    steps = [{"action": "ui.subpage", "container": "cam_box", "page": "nowhere"}]
    logged = _warned(caplog, steps)
    assert "page 'nowhere' is not a sub-page in the project" in logged


def test_a_switch_without_both_ids_is_refused() -> None:
    for step in (
        {"action": "ui.subpage"},
        {"action": "ui.subpage", "container": "cam_box"},
        {"action": "ui.subpage", "page": "media_sub"},
    ):
        refusal = validate_macro([step], [], _project())
        assert refusal is not None
        assert "ui.subpage step requires" in refusal


def test_a_navigate_to_a_subpage_is_warned_as_not_navigable(caplog) -> None:
    # A sub-page is container content, not a destination (子页面是容器的内容，
    # 不是导航目的地).
    steps = [{"action": "ui.navigate", "page": "media_sub"}]
    logged = _warned(caplog, steps)
    assert "not found" not in logged
    assert "is a sub-page, shown inside a sub-page container, not navigated to" in logged


def test_subpage_is_a_dispatched_action() -> None:
    # The chain in execute_action and the DISPATCHED_ACTIONS constant must
    # agree, or a binding the review blessed still does nothing on the glass.
    # execute_action 链与 DISPATCHED_ACTIONS 常量必须一致，否则评审认可的
    # 绑定在玻璃上仍然什么都不做。
    assert "ui.subpage" in DISPATCHED_ACTIONS


# --- Page reference findings ---------------------------------------------------


def _page(elements) -> object:
    return _project(main_elements=elements).ui.pages[0]


def _messages(elements, **kwargs) -> list[str]:
    return [
        f.message
        for f in reference_findings(
            _page(elements),
            page_ids=PAGES,
            subpage_ids=SUBPAGES,
            container_ids=CONTAINERS,
            device_ids=DEVICES,
            macro_ids=MACROS,
        )
    ]


@pytest.mark.parametrize(
    ("element", "expected"),
    [
        pytest.param(
            {"id": "nav_sub", "type": "page_nav", "target_page": "media_sub"},
            "navigates to page 'media_sub', which is a sub-page",
            id="page_nav to subpage",
        ),
        pytest.param(
            {"id": "btn", "type": "button",
             **_press({"action": "ui.navigate", "page": "media_sub"})},
            "navigates to page 'media_sub', which is a sub-page",
            id="navigate to subpage",
        ),
        pytest.param(
            {"id": "btn", "type": "button",
             **_press({"action": "ui.subpage", "container": "ghost_box", "page": "media_sub"})},
            "container 'ghost_box', which is not a sub-page container element",
            id="unknown container",
        ),
        pytest.param(
            {"id": "btn", "type": "button",
             **_press({"action": "ui.subpage", "container": "cam_box", "page": "main"})},
            "shows page 'main', which is not a sub-page",
            id="regular page in a switch",
        ),
        pytest.param(
            {"id": "btn", "type": "button",
             **_press({"action": "ui.subpage", "container": "cam_box", "page": "nowhere"})},
            "shows sub-page 'nowhere', which does not exist",
            id="unknown subpage",
        ),
        pytest.param(
            {"id": "cam_box", "type": "subpage_container", "default_page": "main"},
            "opens with page 'main', which is not a sub-page",
            id="default names a regular page",
        ),
        pytest.param(
            {"id": "cam_box", "type": "subpage_container", "default_page": "nowhere"},
            "opens with sub-page 'nowhere', which does not exist",
            id="default names nothing",
        ),
    ],
)
def test_subpage_findings(element, expected) -> None:
    assert any(expected in m for m in _messages([element])), _messages([element])


def test_a_correct_container_and_switch_come_back_clean() -> None:
    elements = [
        {"id": "cam_box", "type": "subpage_container", "default_page": "media_sub"},
        {"id": "btn", "type": "button",
         **_press({"action": "ui.subpage", "container": "cam_box", "page": "media_sub"})},
    ]
    assert _messages(elements) == []


def test_no_subpage_ids_is_no_opinion() -> None:
    # A caller that cannot enumerate sub-pages stays silent, the same contract
    # the other lookups keep (无法枚举子页面的调用方保持沉默，与其他查找同一契约).
    elements = [{"id": "nav_sub", "type": "page_nav", "target_page": "media_sub"}]
    findings = reference_findings(
        _page(elements),
        page_ids=PAGES,
        device_ids=DEVICES,
        macro_ids=MACROS,
    )
    assert all("which is a sub-page" not in f.message for f in findings)


# --- What a switch sends -------------------------------------------------------


class _Events:
    """Records the events a press emits, the way the trigger engine would hear them.
    记录按下所发出的事件，如同触发引擎听到的那样。"""

    def __init__(self) -> None:
        self.emitted: list = []

    async def emit(self, name: str, *rest) -> None:
        self.emitted.append(name)


async def _run_press(tmp_path: Path, elements: list[dict]) -> tuple[list, list]:
    """Press the first button and return (events, frames) the runtime sent.
    按下第一个按钮，返回运行时发出的 (事件, 帧)。"""
    from openavc.core.engine import Engine

    path = tmp_path / "project.avc"
    path.write_text(json.dumps({
        "openavc_version": "0.11.0",
        "project": {"id": "sim", "name": "Sim", "description": ""},
        "devices": [],
        "macros": [],
        "variables": [],
        "ui": {"settings": {}, "pages": [
            {"id": "main", "name": "Main", "elements": elements, "layouts": []},
        ]},
    }), encoding="utf-8")
    engine = Engine(path)
    engine.project = load_project(path)
    engine.events = _Events()
    frames: list = []

    async def _broadcast(message: dict) -> None:
        frames.append(message)

    engine.broadcast_ws = _broadcast  # type: ignore[method-assign]
    await engine.handle_ui_event("press", "btn")
    return engine.events.emitted, frames


@pytest.mark.asyncio
async def test_a_switch_press_emits_the_event_and_broadcasts_the_frame(tmp_path) -> None:
    elements = [{"id": "btn", "type": "button",
                 **_press({"action": "ui.subpage", "container": "cam_box", "page": "media_sub"})}]
    events, frames = await _run_press(tmp_path, elements)
    assert "ui.subpage.cam_box" in events
    assert {"type": "ui.subpage", "container_id": "cam_box", "page_id": "media_sub"} in frames


@pytest.mark.asyncio
async def test_a_switch_with_an_id_missing_sends_nothing(tmp_path) -> None:
    # The runtime skips an action with a blank id, as it does ui.navigate's --
    # the editor badges it Incomplete, and a hand-edited press is a no-op
    # rather than a half-move (运行时跳过缺 id 的动作，与 ui.navigate 一致——
    # 编辑器会标记 Incomplete，手写的按下操作是无操作而非半次切换).
    elements = [{"id": "btn", "type": "button",
                 **_press({"action": "ui.subpage", "container": "cam_box"})}]
    events, frames = await _run_press(tmp_path, elements)
    # The press event itself is expected; the switch's own event and frame
    # are not (按下事件本身是预期内的；切换自身的事件与帧不应出现).
    assert not any(e.startswith("ui.subpage.") for e in events)
    assert frames == []

# --- Page delete: switches naming dead containers go too -----------------------


def test_a_page_delete_drops_switches_naming_its_containers() -> None:
    """Deleting the page that held a container drops the switches naming it,
    even when their sub-page survives -- the same project the Builder's page
    delete leaves (删除持有容器的页面时，指名该容器的切换动作一并删除——即使
    其子页面还在；与 Builder 删页产出相同的项目).

    The AI's scrubber runs AFTER the page is gone, so its rule is phrased in
    the post-deletion state: a switch whose container no longer exists is dead
    weight, dropped whole. A switch naming a container that survives stays.
    AI 清理器在页面已删除之后运行，其规则以删除后状态表述：容器已不存在的
    切换是死重，整个删除；容器仍存活的切换保留。
    """
    from openavc.cloud.tools.ui_rewrites import scrub_page_references

    project = _project()
    # The survivor lives on a page the delete does not take (幸存容器所在的
    # 页面不被删除).
    next(p for p in project.ui.pages if p.id == "media_sub").elements.append(
        UIElement.model_validate({"id": "survivor_box", "type": "subpage_container", "default_page": "media_sub"})
    )
    project.macros.append(MacroConfig.model_validate({
        "id": "m_switch", "name": "Switch",
        "steps": [
            {"action": "ui.subpage", "container": "cam_box", "page": "media_sub"},
            {"action": "ui.subpage", "container": "survivor_box", "page": "media_sub"},
        ],
        "triggers": [],
    }))
    # The AI's flow: the page is already gone when the scrubber runs.
    # AI 的流程：清理器运行时页面已被删除。
    project.ui.pages = [p for p in project.ui.pages if p.id != "main"]
    scrub_page_references(project, "main")

    kept = [(s.container, s.page) for s in project.macros[0].steps]
    assert ("survivor_box", "media_sub") in kept
    assert ("cam_box", "media_sub") not in kept

