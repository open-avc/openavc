"""The name a person sees for a command parameter.

A parameter's ``label`` is optional in the driver contract. When a driver
declares one (a non-blank string), that is the name, as written. When it
declares none, the parameter's key is made readable instead of being shown
raw, so a field reads "Input ID" rather than ``input_id``:

  * The key splits into words at ``_``, ``-``, spaces and a camelCase step
    (``inputId``, ``playlistUUID``). Letters and digits stay together
    (``output2``), because splitting them breaks more names than it mends.
  * A word in ``ACRONYMS`` takes its usual spelling (ID, URL, HDMI); any other
    word is capitalised.
  * A last word in ``UNITS``, after at least one other word, goes in brackets
    in its usual spelling: ``timeout_ms`` reads "Timeout (ms)", ``gain_db``
    "Gain (dB)", the form driver labels already use ("Poll Interval (sec)").

A key made readable can still say too little (``value``, ``src``); the cure for
that is a label in the driver, not more rules here.

``web/programmer/src/components/shared/paramLabel.ts`` is the same rule for the
IDE's forms. Both run ``tests/fixtures/param_label_cases.json``
(``tests/test_param_label_parity.py`` and ``paramLabel.test.ts``), so change the
two together. Pure stdlib, no server imports.
"""

from __future__ import annotations

import re
from typing import Any

ACRONYMS: dict[str, str] = {
    "api": "API", "av": "AV", "cec": "CEC", "dmx": "DMX", "dsp": "DSP",
    "edid": "EDID", "gpio": "GPIO", "hdmi": "HDMI", "http": "HTTP",
    "https": "HTTPS", "id": "ID", "ids": "IDs", "ip": "IP", "ipv4": "IPv4",
    "ipv6": "IPv6", "ir": "IR", "json": "JSON", "led": "LED", "mac": "MAC",
    "mqtt": "MQTT", "ndi": "NDI", "osc": "OSC", "osd": "OSD", "ptz": "PTZ",
    "rgb": "RGB", "sdi": "SDI", "ssid": "SSID", "tcp": "TCP", "udp": "UDP",
    "uid": "UID", "uri": "URI", "url": "URL", "urls": "URLs", "usb": "USB",
    "uuid": "UUID", "xml": "XML",
}

UNITS: dict[str, str] = {
    "db": "dB", "hz": "Hz", "khz": "kHz", "mhz": "MHz", "ms": "ms",
    "pct": "%", "percent": "%", "sec": "sec", "secs": "secs",
}

_CAMEL_STEP = re.compile(r"([a-z0-9])([A-Z])")
_SEPARATORS = re.compile(r"[_\-\s]+")


def readable_key(key: str) -> str:
    """``key`` as words a person reads (the rule above)."""
    words = [w for w in _SEPARATORS.split(_CAMEL_STEP.sub(r"\1 \2", key)) if w]
    if not words:
        return key
    shown = [ACRONYMS.get(w.lower()) or w[:1].upper() + w[1:].lower() for w in words]
    last = words[-1].lower()
    if last in UNITS:
        shown[-1] = f"({UNITS[last]})" if len(words) > 1 else UNITS[last]
    return " ".join(shown)


def param_label(key: str, param_def: Any = None) -> str:
    """The parameter's label when it declares a non-blank one, else its key
    made readable."""
    label = param_def.get("label") if isinstance(param_def, dict) else None
    if isinstance(label, str) and label.strip():
        return label.strip()
    return readable_key(key)
