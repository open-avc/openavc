"""A discovery signal as a sentence, the way the device audit's evidence
lines say it: what was seen, never a probe's own id.

The words are the IDE's too: ``describeEvidence`` and ``vendorStringWhere``
in ``discoveryEvidence.tsx``, and ``cleanBannerText`` and ``readsAsText`` in
``discoveryViewHelpers.ts``, which the network check step draws its lines
with. They are kept in step by hand, so a change to a sentence or to the
banner rule here is made there in the same change.

A signal carries its Evidence record's ``data``; one without (or of a kind
not listed) is said from its ``source`` (``mdns:_acme._tcp``,
``probe:custom_acme_tcp``).
"""

from __future__ import annotations

from typing import Any, Callable

from openavc.audit.report_text import hex_pairs

# A manufacturer the driver's own probe supplies when it matches
# (``extract_manufacturer``): the device did not say it.
DRIVER_NAMED = "by the driver when its probe matched"

_SIGNAL_PREFIXES = {
    "mdns": "mDNS announcement on {}",
    "ssdp": "SSDP announcement for {}",
    "oui": "MAC address prefix {} seen",
    "snmp_pen": "SNMP enterprise number {}",
    "hostname": "Hostname {} observed",
    "port_open": "Port {} is open",
}

_CHECK_ANSWERED = {
    "probe": "A TCP identification check answered",
    "broadcast": "A UDP identification check answered",
}

# Where a manufacturer string was read, by the probe that read it (with its
# port) and then by the kind of evidence it came in.
_PORT_WHERE = {
    "greeting": "in the greeting on port {}",
    "http_server": "by the web server on port {}",
}
_KIND_WHERE = {
    "ssdp": "in the SSDP description",
    "mdns": "in an mDNS announcement",
    "amx_ddp": "in the AMX DDP beacon",
}


def signal_text(sig: dict[str, Any], names: dict[str, Any] | None = None) -> str:
    """``sig`` as an evidence line says it. ``names`` is the verdict's
    ``drivers``, for naming the driver whose probe supplied a manufacturer."""
    data = (sig.get("evidence") or {}).get("data") or {}
    say = _BY_KIND.get(data.get("kind"))
    if say is not None:
        return say(data, names)
    return _from_source(str(sig.get("source") or ""))


def _from_source(source: str) -> str:
    prefix, _, rest = source.partition(":")
    if prefix in _SIGNAL_PREFIXES and rest:
        return _SIGNAL_PREFIXES[prefix].format(rest)
    return _CHECK_ANSWERED.get(prefix) or source or "A signal"


def _on_port(data: dict[str, Any]) -> str:
    port = data.get("port")
    return f" on port {port}" if isinstance(port, int) else ""


def _mdns(data: dict[str, Any], names: dict[str, Any] | None) -> str:
    return f"mDNS announcement on {data.get('source_id') or 'an unknown service'}"


def _ssdp(data: dict[str, Any], names: dict[str, Any] | None) -> str:
    return f"SSDP announcement for {data.get('source_id') or 'an unknown device type'}"


def _amx_ddp(data: dict[str, Any], names: dict[str, Any] | None) -> str:
    named = " ".join(str(v) for v in (data.get("make"), data.get("model")) if v)
    return f"AMX DDP beacon: {named}" if named else "AMX DDP beacon"


def _broadcast(data: dict[str, Any], names: dict[str, Any] | None) -> str:
    pattern = data.get("matched_pattern")
    return f"UDP probe{_on_port(data)} matched" + (f" {pattern}" if pattern else "")


def _probe(data: dict[str, Any], names: dict[str, Any] | None) -> str:
    """A TCP probe's reply when it reads as text, else the pattern it
    matched, else its bytes, else only that it answered."""
    on = _on_port(data)
    response = data.get("response") or {}
    text = _banner(str(response.get("text") or ""))
    if text is not None:
        return f"TCP probe{on} returned " + (f"'{text}'" if '"' in text else f'"{text}"')
    if data.get("matched_pattern"):
        return f"TCP probe{on} matched {data['matched_pattern']}"
    if response.get("hex"):
        return f"TCP probe{on} returned hex {hex_pairs(str(response['hex'])[:80])}"
    return f"TCP probe{on} answered"


def _oui(data: dict[str, Any], names: dict[str, Any] | None) -> str:
    value, vendor = data.get("value"), data.get("vendor")
    return (f"MAC address prefix {value} belongs to {vendor}" if vendor
            else f"MAC address prefix {value} seen")


def _hostname(data: dict[str, Any], names: dict[str, Any] | None) -> str:
    value, pattern = data.get("value"), data.get("matched_pattern")
    return (f"Hostname pattern {pattern} matched {value}" if pattern
            else f"Hostname {value} observed")


def _snmp_pen(data: dict[str, Any], names: dict[str, Any] | None) -> str:
    return f"SNMP enterprise number {data.get('value')}"


def _vendor_string(data: dict[str, Any], names: dict[str, Any] | None) -> str:
    return f'Manufacturer "{data.get("value")}" named {vendor_where(data, names)}'


def _open_port(data: dict[str, Any], names: dict[str, Any] | None) -> str:
    return f"Port {data.get('value')} is open"


_BY_KIND: dict[Any, Callable[[dict[str, Any], dict[str, Any] | None], str]] = {
    "mdns": _mdns,
    "ssdp": _ssdp,
    "amx_ddp": _amx_ddp,
    "broadcast": _broadcast,
    "probe": _probe,
    "oui": _oui,
    "hostname": _hostname,
    "snmp_pen": _snmp_pen,
    "vendor_string": _vendor_string,
    "open_port": _open_port,
}


def vendor_where(data: dict[str, Any], names: dict[str, Any] | None = None) -> str:
    """Where a manufacturer string came from. A probe's own id is internal
    and never shown. One a driver's probe supplied names that driver when its
    name is known: the line lists every driver the manufacturer points at,
    and only that one's probe matched."""
    if data.get("from_driver"):
        return _driver_named(data, names)
    source = str(data.get("source_probe_id") or "")
    what, _, port = source.partition(":")
    if port and what in _PORT_WHERE:
        return _PORT_WHERE[what].format(port)
    if source == "ssdp_server":
        return "in the SSDP server name"
    return _KIND_WHERE.get(str(data.get("from_kind") or ""), "in a probe reply")


def _driver_named(data: dict[str, Any], names: dict[str, Any] | None) -> str:
    """The driver whose probe supplied the manufacturer, by its name when the
    verdict knows it."""
    entry = (names or {}).get(str(data.get("supplied_by") or ""))
    name = entry.get("name") if isinstance(entry, dict) else None
    return f"by the {name} driver when its probe matched" if name else DRIVER_NAMED


def _banner(raw: str) -> str | None:
    """A reply's readable part, its first 80 characters, when the reply
    reads as text: two readable characters and no more than half as many
    unprintable ones (``readsAsText``). None when it does not."""
    text, noise = clean_banner(raw)
    readable = len("".join(text.split()))
    if readable >= 2 and noise * 2 <= readable:
        return text[:80]
    return None


def clean_banner(raw: str) -> tuple[str, int]:
    """A reply's readable part and how many unprintable characters sit
    inside it (``cleanBannerText``, the same rule): the preamble before the
    first printable character goes, a run of four unprintable ones ends the
    message, shorter runs read as a space. Tab, CR and LF are neither."""
    start = next((i for i, ch in enumerate(raw) if _printable(ch)), len(raw))
    kept = raw[start:_message_end(raw, start)]
    text = " ".join("".join(ch if _printable(ch) else " " for ch in kept).split())
    noise = sum(1 for ch in kept if not _printable(ch) and ch not in "\t\r\n")
    return text, noise


def _printable(ch: str) -> bool:
    return 0x20 <= ord(ch) <= 0x7E


def _message_end(raw: str, start: int) -> int:
    """Where the first run of four unprintable characters after ``start``
    begins (tab, CR and LF neither count nor break a run), else the end."""
    run_start, run = -1, 0
    for i in range(start, len(raw)):
        ch = raw[i]
        if _printable(ch):
            run_start, run = -1, 0
            continue
        if ch in "\t\r\n":
            continue
        run_start = i if run_start < 0 else run_start
        run += 1
        if run >= 4:
            return run_start
    return len(raw)
