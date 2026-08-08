"""The simulator tooling reads DRIVER_INFO through the platform's reader.

``openavc/simulator/scaffold.py`` used to carry its own: a ``literal_eval`` that fell
back to scanning the whole file with regexes the moment a driver referenced a
module constant, which is most of them. It did not merely miss things, it
invented — reporting section names like ``state_variables`` as commands and
pairing labels with the wrong entries — and ``openavc/simulator/validate.py`` imported
that same function for its entire Python path. So a wrong reading fed the
scaffold's generated file and every Python-driver check at once.

Everything here uses an invented device and synthetic declarations; the proof
that it holds for real drivers is a corpus sweep run at the time, not a
fixture checked in here.
"""

from __future__ import annotations

import ast
import textwrap

import pytest

from openavc.simulator.scaffold import extract_driver_info, generate_skeleton


def _write(tmp_path, source: str):
    path = tmp_path / "acme_widget.py"
    path.write_text(textwrap.dedent(source), encoding="utf-8")
    return path


# A driver whose DRIVER_INFO references a module constant. This is the shape
# that sent the old reader into its regex fallback, and it is the common one.
_COMPUTED_DRIVER = '''
    """Acme Widget."""

    _MODES = ["standby", "active"]

    class AcmeWidgetDriver:
        DRIVER_INFO = {
            "id": "acme_widget",
            "name": "Acme Widget",
            "category": "utility",
            "transport": "tcp",
            "delimiter": "\\r\\n",
            "default_config": {"port": 4999},
            "state_variables": {
                "power": {"type": "enum", "label": "Power", "values": ["off", "on"]},
                "mode": {"type": "enum", "label": "Mode", "values": _MODES},
                "volume": {"type": "integer", "label": "Volume", "min": 0, "max": 100},
                "lamp_hours": {"type": "integer", "label": "Lamp Hours"},
                "model": {"type": "string", "label": "Model"},
            },
            "commands": {
                "power_on": {"label": "Power On"},
                "set_volume": {
                    "label": "Set Volume",
                    "params": {"level": {"type": "integer"}},
                },
            },
        }
'''


def test_a_computed_value_no_longer_invents_commands(tmp_path):
    """The regex fallback reported section names as commands. Nothing should."""
    info = extract_driver_info(_write(tmp_path, _COMPUTED_DRIVER))
    assert set(info["commands"]) == {"power_on", "set_volume"}
    assert "state_variables" not in info["commands"]
    assert "commands" not in info["commands"]


def test_a_computed_value_does_not_lose_the_rest_of_the_block(tmp_path):
    info = extract_driver_info(_write(tmp_path, _COMPUTED_DRIVER))
    assert set(info["state_variables"]) == {
        "power", "mode", "volume", "lamp_hours", "model",
    }


def test_labels_land_on_the_entry_they_belong_to(tmp_path):
    """The old reader shifted labels by one entry."""
    info = extract_driver_info(_write(tmp_path, _COMPUTED_DRIVER))
    assert info["commands"]["power_on"]["label"] == "Power On"
    assert info["commands"]["set_volume"]["label"] == "Set Volume"
    assert info["state_variables"]["volume"]["label"] == "Volume"


def test_the_unreadable_value_is_marked_rather_than_guessed(tmp_path):
    """"Declared but not readable" must stay distinguishable from "not declared"."""
    info = extract_driver_info(_write(tmp_path, _COMPUTED_DRIVER))
    assert not isinstance(info["state_variables"]["mode"]["values"], list)
    assert info["state_variables"]["power"]["values"] == ["off", "on"]


def test_a_driver_with_no_id_is_still_rejected(tmp_path):
    path = _write(tmp_path, '''
        class AcmeWidgetDriver:
            DRIVER_INFO = {"name": "No Id"}
    ''')
    assert extract_driver_info(path) is None


# ── What the scaffold does with what it reads ──


def test_a_declared_delimiter_is_carried_into_the_simulator(tmp_path):
    """The simulator never reads the driver's delimiter — it uses its own.

    Dropping it silently gives a line-framed driver a raw-chunk simulator.
    """
    info = extract_driver_info(_write(tmp_path, _COMPUTED_DRIVER))
    skeleton = generate_skeleton(info, "acme_widget")
    assert '"delimiter":' in skeleton
    assert "\\r\\n" in skeleton
    assert "carried from the driver" in skeleton


def test_no_declared_delimiter_says_so_instead_of_saying_nothing(tmp_path):
    """Most drivers set it in _transport_kwargs, which is code, not a declaration.

    Measured on the shipped corpus at the time: 4 of 61 declare it and 25 set
    it in code. A reader of declarations cannot see the second group, so the
    generated file has to tell the author to check rather than imply there is
    nothing to check.
    """
    path = _write(tmp_path, '''
        class AcmeWidgetDriver:
            DRIVER_INFO = {
                "id": "acme_widget",
                "name": "Acme Widget",
                "transport": "tcp",
                "state_variables": {"power": {"type": "boolean", "label": "Power"}},
                "commands": {},
            }
    ''')
    skeleton = generate_skeleton(extract_driver_info(path), "acme_widget")
    assert "_transport_kwargs" in skeleton
    assert "raw chunks" in skeleton


def test_initial_state_is_not_a_wall_of_type_zeros(tmp_path):
    """The guide's own Best Practice #4 says start from realistic values."""
    info = extract_driver_info(_write(tmp_path, _COMPUTED_DRIVER))
    skeleton = generate_skeleton(info, "acme_widget")
    assert '"lamp_hours": 0,' not in skeleton
    assert '"model": "",' not in skeleton
    assert '"volume": 0,' not in skeleton


def test_an_enum_is_seeded_with_one_of_its_own_declared_values(tmp_path):
    info = extract_driver_info(_write(tmp_path, _COMPUTED_DRIVER))
    skeleton = generate_skeleton(info, "acme_widget")
    assert '"power": "off",' in skeleton


def test_a_computed_enum_is_flagged_rather_than_guessed(tmp_path):
    """Seeding "off" into an enum whose values are unknown may be invalid."""
    info = extract_driver_info(_write(tmp_path, _COMPUTED_DRIVER))
    skeleton = generate_skeleton(info, "acme_widget")
    assert "values are computed" in skeleton


def test_a_bounded_number_starts_inside_its_own_range(tmp_path):
    path = _write(tmp_path, '''
        class AcmeWidgetDriver:
            DRIVER_INFO = {
                "id": "acme_widget",
                "name": "Acme Widget",
                "transport": "tcp",
                "state_variables": {
                    "fader": {"type": "number", "label": "Fader", "min": -60, "max": 10},
                },
                "commands": {},
            }
    ''')
    skeleton = generate_skeleton(extract_driver_info(path), "acme_widget")
    assert '"fader": 0.0,' not in skeleton
    assert '"fader": -25.0,' in skeleton


def test_a_child_entity_driver_gets_a_starting_point(tmp_path):
    path = _write(tmp_path, '''
        class AcmeWidgetDriver:
            DRIVER_INFO = {
                "id": "acme_widget",
                "name": "Acme Widget",
                "transport": "tcp",
                "state_variables": {},
                "commands": {},
                "child_entity_types": {
                    "zone": {
                        "label": "Zone",
                        "id_format": {"type": "integer", "min": 1, "max": 8},
                        "state_variables": {"level": {"type": "number"}},
                    },
                },
            }
    ''')
    skeleton = generate_skeleton(extract_driver_info(path), "acme_widget")
    assert "Child entities" in skeleton
    assert "zone" in skeleton


def test_the_child_stub_points_away_from_the_wrong_tool(tmp_path):
    """``self.child_entities`` comes from the device's project entry.

    A controller registers its roster at runtime, so it finds that empty; no
    shipped simulator uses it. What the simulator owes the driver is the
    enumeration reply.
    """
    path = _write(tmp_path, '''
        class AcmeWidgetDriver:
            DRIVER_INFO = {
                "id": "acme_widget", "name": "Acme Widget", "transport": "tcp",
                "state_variables": {}, "commands": {},
                "child_entity_types": {"zone": {"label": "Zone"}},
            }
    ''')
    skeleton = generate_skeleton(extract_driver_info(path), "acme_widget")
    assert "Do NOT reach for self.child_entities" in skeleton


def test_a_driver_without_children_gets_no_child_block(tmp_path):
    info = extract_driver_info(_write(tmp_path, _COMPUTED_DRIVER))
    assert "Child entities" not in generate_skeleton(info, "acme_widget")


@pytest.mark.parametrize("transport", ["tcp", "http", "osc"])
def test_the_generated_file_always_parses(tmp_path, transport):
    """A skeleton that is not valid Python wastes the author's whole first run.

    The computed-enum note in particular has to go on its own line: as a
    trailing comment it swallowed the dict entry's comma, and six shipped
    drivers generated unparseable files before that was caught by sweeping
    the corpus rather than by reading the template.
    """
    source = _COMPUTED_DRIVER.replace('"transport": "tcp"', f'"transport": "{transport}"')
    info = extract_driver_info(_write(tmp_path, source))
    ast.parse(generate_skeleton(info, "acme_widget"))


def test_an_entirely_computed_block_does_not_reach_the_generated_file(tmp_path):
    """The UNEVALUATED marker must never be rendered into a Python literal."""
    path = _write(tmp_path, '''
        _VARS = {"power": {"type": "boolean", "label": "Power"}}

        class AcmeWidgetDriver:
            DRIVER_INFO = {
                "id": "acme_widget",
                "name": "Acme Widget",
                "transport": "tcp",
                "state_variables": _VARS,
                "commands": {},
            }
    ''')
    skeleton = generate_skeleton(extract_driver_info(path), "acme_widget")
    assert "unevaluated" not in skeleton.lower()
    ast.parse(skeleton)
