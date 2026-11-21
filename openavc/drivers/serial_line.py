"""THE serial line settings: what each may be, and how each way of writing one reads.

``SERIAL_LINE_SETTINGS`` is the driver contract's one table of the baud rate,
data bits, parity, stop bits and flow control, and ``read_line_setting`` its
one reading, so ``Even``, ``even`` and ``E`` are one parity at every reader:

* the transport ladder opens the port with what it reads
  (``drivers/transport_ladder.py``), and refuses a value with no reading;
* the checker refuses, at the author's doors, a line setting a driver's
  ``default_config`` holds with no reading (``line_setting_errors``, asked by
  ``avcdriver_semantic`` and ``python_info``), and names it where one loads;
* a bridge is handed the settings as read (``with_line_settings_read``, asked
  by ``core/device_bridges.py``), so its own table says only what its
  hardware takes;
* the Builder's Connection tab and the device form read the copy
  ``contract_gen_ts`` generates into types.gen.ts, through ``serialLine.ts``,
  held to this reading over one corpus by tests/test_serial_line_settings.py.

A choice is read from its value, its label or one of its other spellings,
compared without regard to capitals or the spaces around it. A number is read
from a number or from decimal text ("1", "1.5"), never from true or false. A
baud rate is any whole number above zero; ``offered`` is only the list a
picker shows. ``allowed`` is what a refusal says the setting must be. Null and
blank have no reading; a reader takes an absent setting as the ``default``.

Only ``default_config`` is judged by the checker: it is what reaches a
device's config. A ``config_schema`` entry for a line setting is a label the
device form does not draw, since its serial picker reads this table.

Purity contract, as ``spec``: standard library and ``usable_number`` (the
number rule it reads a number through). It is part of the driver
contract the community catalog and the SDK vendor; an import-guard test
enforces it.
"""

from __future__ import annotations

import re

from openavc.drivers.usable_number import usable_number

SERIAL_LINE_SETTINGS: dict[str, dict[str, object]] = {
    "baudrate": {
        "label": "Baud rate",
        "default": 9600,
        "allowed": "a whole number, such as 9600",
        "offered": (1200, 2400, 4800, 9600, 19200, 38400, 57600, 115200, 230400),
    },
    "bytesize": {
        "label": "Data bits",
        "default": 8,
        "allowed": "5, 6, 7 or 8",
        "choices": ({"value": 5}, {"value": 6}, {"value": 7}, {"value": 8}),
    },
    "parity": {
        "label": "Parity",
        "default": "N",
        "allowed": "N (none), E (even), O (odd), M (mark) or S (space)",
        "choices": (
            {"value": "N", "label": "None"},
            {"value": "E", "label": "Even"},
            {"value": "O", "label": "Odd"},
            {"value": "M", "label": "Mark"},
            {"value": "S", "label": "Space"},
        ),
    },
    "stopbits": {
        "label": "Stop bits",
        "default": 1,
        "allowed": "1, 1.5 or 2",
        "choices": ({"value": 1}, {"value": 1.5}, {"value": 2}),
    },
    "flow_control": {
        "label": "Flow control",
        "default": "none",
        "allowed": "none, hardware (RTS/CTS) or software (XON/XOFF)",
        "choices": (
            {"value": "none", "label": "None"},
            {"value": "hardware", "label": "Hardware (RTS/CTS)", "spellings": ("rtscts", "rts/cts")},
            {"value": "software", "label": "Software (XON/XOFF)", "spellings": ("xonxoff", "xon/xoff")},
        ),
    },
}

# Decimal text, digits 0-9 only: no exponent, no underscores, no hex, which
# Python's float() takes and the Programmer's Number() reads otherwise.
_DECIMAL_TEXT = re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)")
# The spaces trimmed from text before it is read, the same set in serialLine.ts.
_TRIMMED = " \t\r\n"


def _number_written(value: object) -> object:
    """``value`` as something float() reads as the number it means, or None:
    decimal text trimmed, a number as it is, anything else (true and false
    included, which Python counts as 1 and 0) None."""
    if isinstance(value, str):
        text = value.strip(_TRIMMED)
        return text if _DECIMAL_TEXT.fullmatch(text) else None
    is_number = isinstance(value, (int, float)) and not isinstance(value, bool)
    return value if is_number else None


def _line_number(value: object) -> float | None:
    """A number, or decimal text, as a finite float; None for anything else."""
    written = _number_written(value)
    return None if written is None else usable_number(written)


def _spellings(choice: dict[str, object]) -> list[str]:
    words = [choice["value"], *choice.get("spellings", ())]  # type: ignore[misc]
    if "label" in choice:
        words.append(choice["label"])
    return [str(word).lower() for word in words]


def _word_choice(choices: tuple[dict[str, object], ...], value: object) -> object:
    if not isinstance(value, str):
        return None
    word = value.strip(_TRIMMED).lower()
    return next((c["value"] for c in choices if word in _spellings(c)), None)


def _number_choice(choices: tuple[dict[str, object], ...], value: object) -> object:
    number = _line_number(value)
    return next((c["value"] for c in choices if number == c["value"]), None)


def _baud_rate(value: object) -> int | None:
    number = _line_number(value)
    whole = number is not None and number.is_integer()
    return int(number) if whole and number >= 1 else None  # type: ignore[arg-type]


def read_line_setting(key: str, value: object) -> int | float | str | None:
    """THE reading of a serial line setting (``key`` one of
    SERIAL_LINE_SETTINGS): the value the port is opened with, or None when
    ``value`` has no reading."""
    choices = SERIAL_LINE_SETTINGS[key].get("choices")
    if choices is None:
        return _baud_rate(value)
    by_word = isinstance(choices[0]["value"], str)  # type: ignore[index]
    reader = _word_choice if by_word else _number_choice
    return reader(choices, value)  # type: ignore[arg-type,return-value]


def with_line_settings_read(config: dict[str, object]) -> dict[str, object]:
    """A copy of ``config`` with each serial line setting it holds replaced by
    its reading: what a bridge is handed for the port it prepares. A setting
    with no reading is left as written, for the bridge to refuse in its own
    words."""
    read = dict(config)
    for key in SERIAL_LINE_SETTINGS.keys() & read.keys():
        value = read_line_setting(key, read[key])
        if value is not None:
            read[key] = value
    return read


def line_setting_errors(default_config: object) -> list[tuple[str, str]]:
    """Each line setting in a driver's ``default_config`` with no reading, as
    ``(key, message)`` in the table's order: what the author's doors refuse."""
    if not isinstance(default_config, dict):
        return []
    return [
        (key, _refusal(key, default_config[key]))
        for key in SERIAL_LINE_SETTINGS
        if key in default_config and read_line_setting(key, default_config[key]) is None
    ]


def _refusal(key: str, value: object) -> str:
    setting = SERIAL_LINE_SETTINGS[key]
    written = "null" if value is None else repr(value)
    return (
        f"default_config.{key}: a serial port can't use {written} as its "
        f"{str(setting['label']).lower()}; write {setting['allowed']}"
    )
