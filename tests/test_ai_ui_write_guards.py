"""The AI's UI writes come back with their own mistakes attached.

The panel that started this returned ``{"status": "created"}`` for 67 elements,
14 of which could not draw their captions. Every check here is about that one
sentence: the write still lands, the reply still says created, and the reply now
also says what will be wrong with it and by how many pixels.

The rule the whole design hangs on is that **rejecting is the only thing that
costs a round trip**, so nothing here rejects. Several tests exist purely to
hold that line -- a starved control, an overlap and an out-of-range fader all
have to keep succeeding.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from openavc.cloud.ai_tool_handler import AIToolHandler, _tool_result_is_error


class _FakeDriver:
    """A driver that declares a channel fader, invented for this test.

    Deliberately not a MagicMock: the point of these tests is what the write
    path reads out of a driver, and a mock answers every attribute, which would
    let a lookup that never happened look like one that succeeded.
    """

    DRIVER_INFO = {
        "id": "acme_amp",
        "state_variables": {
            "master_level": {"type": "float", "min": -60.0, "max": 6.0, "unit": "dB"},
        },
        "child_entity_types": {
            "channel": {
                "id_format": {"type": "integer", "pad_width": 2},
                "state_variables": {
                    "level": {
                        "type": "float", "min": -80.0, "max": 0.0,
                        "step": 0.5, "unit": "dB",
                    },
                },
            },
        },
    }

    def get_child_schema(self, child_type: str, local_id):
        return dict(
            self.DRIVER_INFO["child_entity_types"][child_type]["state_variables"]
        )


def _make_project():
    from openavc.core.project_loader import (
        DeviceConfig, Layout, Placement, ProjectConfig, ProjectMeta,
        UIConfig, UIElement, UIPage,
    )
    return ProjectConfig(
        project=ProjectMeta(id="test_project", name="Test Room"),
        devices=[DeviceConfig(id="amp", driver="acme_amp", name="Amplifier")],
        ui=UIConfig(pages=[
            UIPage(
                id="main", name="Main",
                elements=[
                    UIElement(id="strip", type="group", label="Channel 1"),
                    UIElement(id="title", type="label", text="Amplifier"),
                ],
                layouts=[Layout(id="landscape", primary=True, placements={
                    "strip": Placement(x=0, y=10, w=25, h=80),
                    "title": Placement(x=0, y=0, w=40, h=8),
                })],
            ),
        ]),
    )


def _tool_call(tool_name, tool_input=None, request_id="req-1"):
    from openavc.cloud.protocol import AI_TOOL_CALL, _now_iso

    return {
        "type": AI_TOOL_CALL,
        "ts": _now_iso(),
        "seq": 1,
        "session": "test",
        "payload": {
            "request_id": request_id,
            "tool_name": tool_name,
            "tool_input": tool_input or {},
        },
    }


async def _drain():
    for _ in range(10):
        await asyncio.sleep(0)  # noqa: ASYNC110 - bounded drain, not a busy-wait
        others = [
            t for t in asyncio.all_tasks()
            if t is not asyncio.current_task() and not t.done()
        ]
        if not others:
            return
        await asyncio.wait(others, timeout=2.0)


@pytest.fixture
def mock_agent():
    agent = MagicMock()
    agent.send_message = AsyncMock()
    agent.state = MagicMock()
    agent.state.snapshot.return_value = {}
    return agent


@pytest.fixture
def mock_devices():
    devices = MagicMock()
    devices.list_devices.return_value = []
    devices.get_driver.return_value = _FakeDriver()
    return devices


@pytest.fixture
def mock_engine():
    engine = MagicMock()
    engine.project = _make_project()
    engine.project_path = MagicMock()
    engine.broadcast_ws = AsyncMock()
    engine._project_revision = 0

    async def _apply_edit(mutate):
        new_project = engine.project.model_copy(deep=True)
        mutate(new_project)
        engine.project = new_project
        engine._project_revision += 1
        return engine._project_revision

    engine.apply_project_edit = AsyncMock(side_effect=_apply_edit)
    return engine


@pytest.fixture(autouse=True)
def _patch_save_project():
    with patch("openavc.core.project_loader.save_project"):
        yield


@pytest.fixture
def handler(mock_agent, mock_devices):
    return AIToolHandler(mock_agent, mock_devices, MagicMock(emit=AsyncMock()))


async def _run(handler, engine, agent, tool, payload):
    with patch.object(handler, "_get_engine", return_value=engine):
        await handler.handle(_tool_call(tool, payload))
        await _drain()
    return agent.send_message.call_args[0][1]


def _result(payload):
    assert payload["success"] is True, payload
    return payload["result"]


# --- The write lands, and says what is wrong with it -----------------------


@pytest.mark.asyncio
async def test_a_starved_control_is_created_and_reported(handler, mock_engine, mock_agent):
    """Both halves of D1 in one assertion: created, and warned about.

    The LED is 2% of a 1280px page -- 26px, of which 20 is a dot that does not
    shrink, so the caption it was given has 6px and renders as nothing. Before
    this, the only thing that came back was "created".
    """
    payload = await _run(handler, mock_engine, mock_agent, "add_ui_elements", {
        "page_id": "main",
        "elements": [{
            "id": "ready_led", "type": "status_led", "label": "Ready",
            "placement": {"x": 50, "y": 50, "w": 2, "h": 5},
        }],
    })
    result = _result(payload)

    assert result["status"] == "created"
    assert result["element_ids"] == ["ready_led"]
    assert any("ready_led" in w and "needs 29px" in w for w in result["warnings"])
    assert "warning_note" in result
    # The element is really on the page: a warning is not a rollback.
    page = mock_engine.project.ui.pages[0]
    assert any(el.id == "ready_led" for el in page.elements)


@pytest.mark.asyncio
async def test_a_control_added_to_a_custom_page_is_told_it_will_not_draw(
    handler, mock_engine, mock_agent,
):
    """The page draws its own markup, so the control lands and is never seen.

    Without this the write comes back "created" with a clean review, and the
    only way to find out is to stand in front of the panel. It replaces the
    geometry findings rather than joining them: advice about the pixels of a
    control nobody will see is worse than silence.
    """
    page = mock_engine.project.ui.pages[0]
    page.render_mode = "custom"
    page.custom_file = "room_map/index.html"

    result = _result(await _run(handler, mock_engine, mock_agent, "add_ui_elements", {
        "page_id": "main",
        "elements": [{
            "id": "ready_led", "type": "status_led", "label": "Ready",
            "placement": {"x": 50, "y": 50, "w": 2, "h": 5},
        }],
    }))
    assert result["status"] == "created"
    assert any(
        "shows room_map/index.html" in w and "not drawn" in w
        for w in result["warnings"]
    ), result["warnings"]
    # And nothing about the pixels of a control that will never be on screen.
    assert not any("needs 29px" in w for w in result["warnings"]), result["warnings"]


@pytest.mark.asyncio
async def test_a_warned_write_is_still_a_success_to_the_caller(
    handler, mock_engine, mock_agent,
):
    """The tool_result must not carry is_error, or the model treats it as failure.

    ``_tool_result_is_error`` is what sets that flag, and it keys off ``error``
    and ``success``. A warnings list has to stay clear of both.
    """
    payload = await _run(handler, mock_engine, mock_agent, "add_ui_elements", {
        "page_id": "main",
        "elements": [{
            "id": "ready_led", "type": "status_led", "label": "Ready",
            "placement": {"x": 50, "y": 50, "w": 2, "h": 5},
        }],
    })
    assert _tool_result_is_error(_result(payload)) is False


@pytest.mark.asyncio
async def test_a_clean_write_carries_no_warnings_field_at_all(
    handler, mock_engine, mock_agent,
):
    """Silence is the normal case and has to look like it.

    An empty list every time trains the reader to skip the field, which is the
    same as not having it.
    """
    result = _result(await _run(handler, mock_engine, mock_agent, "add_ui_elements", {
        "page_id": "main",
        "elements": [{
            "id": "power_btn", "type": "button", "label": "Power",
            "placement": {"x": 50, "y": 50, "w": 12, "h": 10},
        }],
    }))
    assert "warnings" not in result
    assert "warning_note" not in result
    assert "auto_filled" not in result


@pytest.mark.asyncio
async def test_overlapping_and_overhanging_writes_are_reported_together(
    handler, mock_engine, mock_agent,
):
    result = _result(await _run(handler, mock_engine, mock_agent, "add_ui_elements", {
        "page_id": "main",
        "elements": [
            {"id": "src_label", "type": "label", "text": "Source", "parent": "strip",
             "placement": {"x": 5, "y": 10, "w": 50, "h": 10}},
            {"id": "src_value", "type": "label", "text": "HDMI", "parent": "strip",
             "placement": {"x": 30, "y": 10, "w": 80, "h": 10}},
        ],
    }))
    warnings = " | ".join(result["warnings"])
    assert "src_label" in warnings and "src_value" in warnings
    assert "overlap by" in warnings
    assert "past the right" in warnings


@pytest.mark.asyncio
async def test_a_page_created_with_its_elements_is_reviewed_too(
    handler, mock_engine, mock_agent,
):
    """The call that built the panel that started this was exactly this one."""
    result = _result(await _run(handler, mock_engine, mock_agent, "add_ui_page", {
        "id": "amp", "name": "Amplifier",
        "elements": [{
            "id": "clip_led", "type": "status_led", "label": "Clip",
            "placement": {"x": 2, "y": 2, "w": 1.8, "h": 4},
        }],
    }))
    assert result["status"] == "created"
    assert any("clip_led" in w for w in result["warnings"])


@pytest.mark.asyncio
async def test_a_master_element_gets_the_same_measurement(
    handler, mock_engine, mock_agent,
):
    result = _result(await _run(handler, mock_engine, mock_agent, "add_master_element", {
        "id": "conn_led", "type": "status_led", "label": "Online",
        "placements": {"landscape": {"x": 1, "y": 1, "w": 1.5, "h": 4}},
    }))
    assert result["status"] == "created"
    assert any("conn_led" in w for w in result["warnings"])


# --- Scope -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_an_update_answers_for_its_own_element_only(
    handler, mock_engine, mock_agent,
):
    """A page full of pre-existing problems must not be re-reported every call.

    Two starved LEDs already exist; the edit moves one. Reporting both would
    make every subsequent write louder than the last until the field is
    worthless.
    """
    await _run(handler, mock_engine, mock_agent, "add_ui_elements", {
        "page_id": "main",
        "elements": [
            {"id": "led_a", "type": "status_led", "label": "A",
             "placement": {"x": 60, "y": 10, "w": 2, "h": 5}},
            {"id": "led_b", "type": "status_led", "label": "B",
             "placement": {"x": 70, "y": 10, "w": 2, "h": 5}},
        ],
    })
    result = _result(await _run(handler, mock_engine, mock_agent, "update_ui_element", {
        "element_id": "led_a", "placement": {"x": 65},
    }))
    assert result["status"] == "updated"
    assert all("led_b" not in w for w in result["warnings"])
    assert any("led_a" in w for w in result["warnings"])


@pytest.mark.asyncio
async def test_moving_a_box_through_the_page_tool_is_reviewed(
    handler, mock_engine, mock_agent,
):
    result = _result(await _run(handler, mock_engine, mock_agent, "update_ui_page", {
        "page_id": "main",
        "layouts": [{"id": "landscape", "placements": {
            "title": {"x": 0, "y": 0, "w": 40, "h": 120},
        }}],
    }))
    assert result["changed"] == ["layouts"]
    assert any("title" in w and "past the bottom" in w for w in result["warnings"])


@pytest.mark.asyncio
async def test_a_page_rename_measures_nothing(handler, mock_engine, mock_agent):
    result = _result(await _run(handler, mock_engine, mock_agent, "update_ui_page", {
        "page_id": "main", "name": "Renamed",
    }))
    assert result["changed"] == ["name"]
    assert "warnings" not in result


# --- The one auto-fill -----------------------------------------------------


@pytest.mark.asyncio
async def test_an_omitted_fader_bound_is_filled_from_the_driver(
    handler, mock_engine, mock_agent,
):
    """The defect this exists for: a dB fader with no ``max``.

    The renderer substitutes 100 for an absent maximum, so a -80..0 dB fader
    silently becomes -80..+100 and the top of the throw commands +100dB. The
    driver declares 0 through its child-entity schema, and until now nothing on
    this path ever looked.
    """
    result = _result(await _run(handler, mock_engine, mock_agent, "add_ui_elements", {
        "page_id": "main",
        "elements": [{
            "id": "ch1_fader", "type": "fader", "min": -80,
            "placement": {"x": 60, "y": 20, "w": 8, "h": 40},
            "bindings": {"show": {"value": {"key": "device.amp.channel.01.level"}}},
        }],
    }))

    filled = " | ".join(result["auto_filled"])
    assert "max was not set" in filled and "filled in as 0" in filled
    assert "unit" in filled and "dB" in filled

    element = next(
        el for el in mock_engine.project.ui.pages[0].elements if el.id == "ch1_fader"
    )
    assert (element.min, element.max, element.step, element.unit) == (-80, 0.0, 0.5, "dB")


@pytest.mark.asyncio
async def test_a_device_level_key_resolves_too(handler, mock_engine, mock_agent):
    result = _result(await _run(handler, mock_engine, mock_agent, "add_ui_elements", {
        "page_id": "main",
        "elements": [{
            "id": "master", "type": "fader",
            "placement": {"x": 60, "y": 20, "w": 8, "h": 40},
            "bindings": {"show": {"value": {"key": "device.amp.master_level"}}},
        }],
    }))
    element = next(
        el for el in mock_engine.project.ui.pages[0].elements if el.id == "master"
    )
    assert (element.min, element.max) == (-60.0, 6.0)
    assert "auto_filled" in result


@pytest.mark.asyncio
async def test_a_deliberately_narrower_range_survives_the_write(
    handler, mock_engine, mock_agent,
):
    """A volume ceiling is authoring. Nothing may quietly widen it back."""
    result = _result(await _run(handler, mock_engine, mock_agent, "add_ui_elements", {
        "page_id": "main",
        "elements": [{
            "id": "ch1_fader", "type": "fader", "min": -40, "max": -10,
            "placement": {"x": 60, "y": 20, "w": 8, "h": 40},
            "bindings": {"show": {"value": {"key": "device.amp.channel.01.level"}}},
        }],
    }))
    element = next(
        el for el in mock_engine.project.ui.pages[0].elements if el.id == "ch1_fader"
    )
    assert (element.min, element.max) == (-40, -10)
    assert all("min" not in w and "max" not in w for w in result.get("warnings", []))


@pytest.mark.asyncio
async def test_a_range_wider_than_the_device_warns_and_still_lands(
    handler, mock_engine, mock_agent,
):
    result = _result(await _run(handler, mock_engine, mock_agent, "add_ui_elements", {
        "page_id": "main",
        "elements": [{
            "id": "ch1_fader", "type": "fader", "min": -80, "max": 10,
            "placement": {"x": 60, "y": 20, "w": 8, "h": 40},
            "bindings": {"show": {"value": {"key": "device.amp.channel.01.level"}}},
        }],
    }))
    assert result["status"] == "created"
    assert any("max 10 is above the 0" in w for w in result["warnings"])
    element = next(
        el for el in mock_engine.project.ui.pages[0].elements if el.id == "ch1_fader"
    )
    assert element.max == 10


@pytest.mark.asyncio
async def test_an_unrelated_edit_does_not_rewrite_a_range(
    handler, mock_engine, mock_agent,
):
    """Auto-fill completes what the caller was expressing, nothing else.

    Renaming a control says nothing about its range, and filling one in there
    would be a silent mutation the caller has no reason to expect. On a write
    that does touch the range or the binding, it is exactly what was meant.
    """
    await _run(handler, mock_engine, mock_agent, "add_ui_elements", {
        "page_id": "main",
        "elements": [{
            "id": "ch1_fader", "type": "fader",
            "placement": {"x": 60, "y": 20, "w": 8, "h": 40},
        }],
    })
    result = _result(await _run(handler, mock_engine, mock_agent, "update_ui_element", {
        "element_id": "ch1_fader", "label": "Channel 1",
    }))
    assert "auto_filled" not in result
    element = next(
        el for el in mock_engine.project.ui.pages[0].elements if el.id == "ch1_fader"
    )
    assert element.max is None


# --- Nothing new refuses ---------------------------------------------------


@pytest.mark.asyncio
async def test_the_structural_rejection_is_unchanged(handler, mock_engine, mock_agent):
    """The one class that rejects still rejects, and still says why."""
    payload = await _run(handler, mock_engine, mock_agent, "add_ui_elements", {
        "page_id": "main",
        "elements": [{
            "id": "orphan", "type": "label", "parent": "nowhere",
            "placement": {"x": 0, "y": 0, "w": 10, "h": 10},
        }],
    })
    assert payload["success"] is False
    assert "not an element on page" in payload["result"]["error"]


@pytest.mark.asyncio
async def test_a_driver_that_cannot_answer_costs_the_write_nothing(
    handler, mock_engine, mock_agent, mock_devices,
):
    """The range lookup is advisory, so its failure has to be survivable.

    A disabled device, an orphaned driver and a driver whose schema lookup
    throws all mean the same thing here: no opinion. None of them may turn a
    working UI write into an error.
    """
    mock_devices.get_driver.side_effect = RuntimeError("driver is on fire")
    result = _result(await _run(handler, mock_engine, mock_agent, "add_ui_elements", {
        "page_id": "main",
        "elements": [{
            "id": "ch1_fader", "type": "fader",
            "placement": {"x": 60, "y": 20, "w": 8, "h": 40},
            "bindings": {"show": {"value": {"key": "device.amp.channel.01.level"}}},
        }],
    }))
    assert result["status"] == "created"
    assert "auto_filled" not in result


@pytest.mark.asyncio
async def test_a_label_on_a_float_is_told_to_round_it(
    handler, mock_engine, mock_agent,
):
    """A float32 reading of 0.06 arrives as 0.06000000238418579.

    `_labelValueText` prints a number unchanged when display_decimals is
    absent, and a label is the only type that does -- a fader falls back to 1,
    a slider derives one from its step. So this is the one place the renderer's
    own default cannot save the author, and it is visible from across a room.
    """
    result = _result(await _run(handler, mock_engine, mock_agent, "add_ui_elements", {
        "page_id": "main",
        "elements": [{
            "id": "level_readout", "type": "label",
            "placement": {"x": 0, "y": 0, "w": 20, "h": 10},
            "bindings": {"show": {"value": {
                "source": "state", "key": "device.amp.master_level",
            }}},
        }],
    }))
    warnings = " | ".join(result.get("warnings", []))
    assert "display_decimals" in warnings, warnings


@pytest.mark.asyncio
async def test_a_typod_property_is_caught_like_a_typod_device(
    handler, mock_engine, mock_agent,
):
    """The half that used to pass silently.

    A bad device, command, macro and page were all caught while the property
    after the device id was not -- and a typo there is at least as common.
    """
    result = _result(await _run(handler, mock_engine, mock_agent, "add_ui_elements", {
        "page_id": "main",
        "elements": [{
            "id": "ghost_readout", "type": "label", "display_decimals": 1,
            "placement": {"x": 0, "y": 0, "w": 20, "h": 10},
            "bindings": {"show": {"value": {
                "source": "state", "key": "device.amp.channel.01.no_such_property",
            }}},
        }],
    }))
    warnings = " | ".join(result.get("warnings", []))
    assert "does not declare that" in warnings, warnings
    assert "level" in warnings, "the real ones are named"


@pytest.mark.asyncio
async def test_a_platform_property_no_driver_declares_stays_quiet(
    handler, mock_engine, mock_agent,
):
    """`device.<id>.online` is the commonest binding on any panel.

    It appears in no DRIVER_INFO anywhere, so a property check that did not
    know the platform-set keys would fire on the most correct page there is.
    """
    result = _result(await _run(handler, mock_engine, mock_agent, "add_ui_elements", {
        "page_id": "main",
        "elements": [{
            "id": "online_led", "type": "status_led", "label": "Online",
            "placement": {"x": 0, "y": 0, "w": 5, "h": 8},
            "bindings": {"show": {"look": {"key": "device.amp.online"}}},
        }],
    }))
    warnings = " | ".join(result.get("warnings", []))
    assert "does not declare" not in warnings, warnings


@pytest.mark.asyncio
async def test_a_declared_child_property_stays_quiet(handler, mock_engine, mock_agent):
    result = _result(await _run(handler, mock_engine, mock_agent, "add_ui_elements", {
        "page_id": "main",
        "elements": [{
            "id": "ch_readout", "type": "label", "display_decimals": 1,
            "placement": {"x": 0, "y": 0, "w": 20, "h": 10},
            "bindings": {"show": {"value": {"key": "device.amp.channel.01.level"}}},
        }],
    }))
    warnings = " | ".join(result.get("warnings", []))
    assert "does not declare" not in warnings, warnings


# --- The matrix config is a schema now, and it refuses ---------------------


@pytest.mark.asyncio
async def test_an_invented_matrix_config_is_refused_rather_than_stored(
    handler, mock_engine, mock_agent,
):
    """It used to store perfectly and draw nothing.

    `matrix_config` is dict[str, Any] at every layer, so counts at the wrong
    level round-tripped through save, export and reload without a word -- and
    the panel drew an empty box, because nothing in that shape resolves to a
    destination.
    """
    payload = await _run(handler, mock_engine, mock_agent, "add_ui_elements", {
        "page_id": "main",
        "elements": [{
            "id": "mx1", "type": "matrix",
            "placement": {"x": 0, "y": 0, "w": 60, "h": 60},
            "matrix_config": {"destinations": {"count": 8}},
        }],
    })
    assert payload["success"] is False
    assert "inside 'from'" in payload["result"]["error"]
    page = mock_engine.project.ui.pages[0]
    assert not any(el.id == "mx1" for el in page.elements)


@pytest.mark.asyncio
async def test_a_destination_with_no_value_is_refused_on_an_update(
    handler, mock_engine, mock_agent,
):
    """The write door refuses, so a list can never draw shorter than it reads."""
    from openavc.core.project_loader import UIElement

    mock_engine.project.ui.pages[0].elements.append(
        UIElement(id="mx2", type="matrix", matrix_config={"sources": [1], "destinations": []}),
    )
    payload = await _run(handler, mock_engine, mock_agent, "update_ui_element", {
        "element_id": "mx2",
        "matrix_config": {"destinations": [{"label": "Main LCD", "route_key": "device.mx.out"}]},
    })
    assert payload["success"] is False
    assert "no 'value'" in payload["result"]["error"]


@pytest.mark.asyncio
async def test_a_matrix_config_that_says_what_it_means_lands(
    handler, mock_engine, mock_agent,
):
    payload = await _run(handler, mock_engine, mock_agent, "add_ui_elements", {
        "page_id": "main",
        "elements": [{
            "id": "mx3", "type": "matrix",
            "placement": {"x": 0, "y": 0, "w": 60, "h": 60},
            "matrix_config": {
                "sources": [{"value": 1, "label": "PC"}],
                "destinations": [{"value": 1, "label": "LCD",
                                  "route_key": "device.mx.output.1.input"}],
            },
        }],
    })
    assert _result(payload)["status"] == "created"


# --- An update changes what it was told to change --------------------------


@pytest.mark.asyncio
async def test_an_update_persists_every_field_it_was_given(
    handler, mock_engine, mock_agent,
):
    """The defect this replaced: eight fields landed and the rest vanished.

    ``update_ui_element`` assigned label, text, parent, placement, hidden,
    aspect_lock, style and bindings, and silently dropped everything else --
    while replying ``{"status": "updated"}``. So re-ranging a fader, setting
    ``display_decimals`` on a readout, or fixing a matrix did nothing, and the
    caller was told it had worked. ``min`` and ``max`` were even read to decide
    whether to run the range check, and then thrown away.
    """
    payload = await _run(handler, mock_engine, mock_agent, "add_ui_elements", {
        "page_id": "main",
        "elements": [{"id": "fader1", "type": "fader",
                      "placement": {"x": 50, "y": 10, "w": 20, "h": 70}}],
    })
    assert _result(payload)["status"] == "created"

    payload = await _run(handler, mock_engine, mock_agent, "update_ui_element", {
        "element_id": "fader1",
        "min": -80.0, "max": 0.0, "step": 0.5, "unit": "dB",
        "display_decimals": 1, "orientation": "vertical",
        "css_class": "brand-fader",
    })
    result = _result(payload)
    assert result["status"] == "updated"
    # The reply names what landed, so a caller never has to re-read the element
    # to find out which half of its request the door took.
    assert set(result["changed"]) == {
        "min", "max", "step", "unit", "display_decimals", "orientation", "css_class",
    }

    page = next(p for p in mock_engine.project.ui.pages if p.id == "main")
    el = next(e for e in page.elements if e.id == "fader1")
    assert (el.min, el.max, el.step, el.unit) == (-80.0, 0.0, 0.5, "dB")
    assert el.display_decimals == 1
    assert el.orientation == "vertical"
    assert el.css_class == "brand-fader"


@pytest.mark.asyncio
async def test_an_update_still_refuses_a_field_no_element_has(
    handler, mock_engine, mock_agent,
):
    """Assigning what the model declares must not mean assigning anything.

    The element model is ``extra='allow'``, so an invented key would be stored
    and never read -- the silent-write failure one level down.
    """
    payload = await _run(handler, mock_engine, mock_agent, "update_ui_element", {
        "element_id": "title", "colour": "#ff0000",
    })
    assert payload["success"] is False
    assert "'colour' is not a field" in payload["result"]["error"]


@pytest.mark.asyncio
async def test_a_custom_control_can_be_repointed_and_regranted(
    handler, mock_engine, mock_agent,
):
    """Both halves of a custom control stay editable after it is placed.

    ``custom_file`` and ``grant`` were settable when the element was created
    and unreachable forever after, so a control could not be pointed at a
    renamed file and what it may reach could not be narrowed.
    """
    payload = await _run(handler, mock_engine, mock_agent, "add_ui_elements", {
        "page_id": "main",
        "elements": [{
            "id": "map", "type": "custom",
            "placement": {"x": 30, "y": 10, "w": 40, "h": 40},
            "custom_file": "room_map/index.html",
            "grant": {"devices": ["amp"], "macros": True},
        }],
    })
    assert _result(payload)["status"] == "created"

    payload = await _run(handler, mock_engine, mock_agent, "update_ui_element", {
        "element_id": "map",
        "custom_file": "room_map/v2.html",
        "grant": {"devices": [], "variables": [], "macros": False, "navigate": False},
    })
    assert set(_result(payload)["changed"]) == {"custom_file", "grant"}

    page = next(p for p in mock_engine.project.ui.pages if p.id == "main")
    el = next(e for e in page.elements if e.id == "map")
    assert el.custom_file == "room_map/v2.html"
    assert el.grant.devices == [] and el.grant.macros is False


@pytest.mark.asyncio
async def test_a_grant_that_is_not_shaped_like_a_grant_is_refused(
    handler, mock_engine, mock_agent,
):
    """The one field checked rather than assigned raw, and why.

    The model does not validate on assignment, so ``"amp"`` where a list
    belongs would land intact -- and the panel asks ``grant.devices.includes(id)``,
    which a **string** answers too. A grant of ``"amp"`` would then match every
    device id that is a substring of it, which is not what anyone ticked.
    """
    payload = await _run(handler, mock_engine, mock_agent, "update_ui_element", {
        "element_id": "title", "grant": {"devices": "amp"},
    })
    assert payload["success"] is False
    assert "not shaped right" in payload["result"]["error"]

    page = next(p for p in mock_engine.project.ui.pages if p.id == "main")
    el = next(e for e in page.elements if e.id == "title")
    assert getattr(el, "grant", None) is None


# --- A locked element is pinned against the AI too -------------------------


@pytest.mark.asyncio
async def test_a_locked_element_cannot_be_moved_or_deleted(
    handler, mock_engine, mock_agent,
):
    """``locked`` had no meaning at this door, only on the canvas.

    So somebody could pin the background art in the Builder and have it moved
    or deleted on the next request -- the one thing the flag exists to prevent,
    one door over.
    """
    await _run(handler, mock_engine, mock_agent, "update_ui_element", {
        "element_id": "title", "locked": True,
    })

    moved = await _run(handler, mock_engine, mock_agent, "update_ui_element", {
        "element_id": "title", "placement": {"x": 90.0},
    })
    assert moved["success"] is False
    assert "is locked" in moved["result"]["error"]

    deleted = await _run(handler, mock_engine, mock_agent, "delete_ui_elements", {
        "element_ids": ["title"],
    })
    assert deleted["success"] is False
    assert "Locked, so nothing was deleted" in deleted["result"]["error"]
    assert "'title'" in deleted["result"]["error"]

    page = next(p for p in mock_engine.project.ui.pages if p.id == "main")
    assert any(e.id == "title" for e in page.elements)


@pytest.mark.asyncio
async def test_a_locked_element_can_still_be_restyled_and_unlocked(
    handler, mock_engine, mock_agent,
):
    """Locking pins the box, not the element -- as in the Properties panel,
    which is also the only way to turn the flag back off."""
    await _run(handler, mock_engine, mock_agent, "update_ui_element", {
        "element_id": "title", "locked": True,
    })

    restyled = await _run(handler, mock_engine, mock_agent, "update_ui_element", {
        "element_id": "title", "text": "Amplifier Rack",
    })
    assert _result(restyled)["changed"] == ["text"]

    # Unlocking and moving are two calls, deliberately: whether an element is
    # pinned is read when the call arrives, so the answer never depends on
    # which order the fields happened to be assigned in. It is also what the
    # refusal above tells the caller to do.
    both_at_once = await _run(handler, mock_engine, mock_agent, "update_ui_element", {
        "element_id": "title", "locked": False, "placement": {"x": 5.0},
    })
    assert both_at_once["success"] is False

    assert _result(await _run(handler, mock_engine, mock_agent, "update_ui_element", {
        "element_id": "title", "locked": False,
    }))["changed"] == ["locked"]
    assert _result(await _run(handler, mock_engine, mock_agent, "update_ui_element", {
        "element_id": "title", "placement": {"x": 5.0},
    }))["changed"] == ["placement"]
