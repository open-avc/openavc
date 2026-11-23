"""A built-in driver's setup hints never name a serial port the platform
refuses to open.

The built-in definitions are the platform's own files, read wherever a
driver's config schema is shown (the Driver Builder, the AI's driver tools,
``GET /api/drivers``). A hint that tells an author to type a port
``serial_port_refusal`` refuses sends them to an error.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

from openavc.transport.serial_transport import serial_port_refusal

DEFINITIONS = Path(__file__).resolve().parent.parent / "openavc" / "drivers" / "definitions"

#: A word written like a port with a scheme ("SIM:name", "COM3:"), not a URL.
_PORT_WORD = re.compile(r"\b[A-Za-z]+:[\w.-]+")


def _hints() -> list[tuple[str, str, str]]:
    found = []
    for path in sorted(DEFINITIONS.glob("*.avcdriver")):
        definition = yaml.safe_load(path.read_text(encoding="utf-8"))
        for name, field in (definition.get("config_schema") or {}).items():
            for key in ("description", "help"):
                text = field.get(key) if isinstance(field, dict) else None
                if isinstance(text, str):
                    found.append((path.name, name, text))
    return found


def test_the_built_in_definitions_have_hints_to_read() -> None:
    assert any(name == "port" for _, name, _ in _hints())


@pytest.mark.parametrize("file,field,text", _hints())
def test_no_hint_names_a_port_the_platform_refuses(file: str, field: str, text: str) -> None:
    refused = [word for word in _PORT_WORD.findall(text) if serial_port_refusal(word)]
    assert refused == [], f"{file} {field}: {text!r}"
