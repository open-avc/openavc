"""Protocol-specific device identification probes.

Each probe connects to an open port, sends a query (or examines the banner),
and parses the response to identify the device type, manufacturer, model,
and firmware.  All probes are read-only — they never send commands that could
change device state.

Probes here are deterministic identifications: a positive response uniquely
identifies the vendor/protocol. Non-deterministic heuristics (HTTP banner
fingerprinting, TLS cert CN parsing, SSH banner matching, SMB negotiate,
favicon hashing) are intentionally NOT in this module — they produced
false positives on every web-enabled device and were removed in the
discovery redesign. See discovery-redesign-plan.md.
"""

from __future__ import annotations

import asyncio
import logging
import re
import struct
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger("discovery.prober")


@dataclass
class ProbeResult:
    """Result from a single protocol probe."""

    protocol: str  # e.g. "pjlink", "extron_sis", "samsung_mdc"
    manufacturer: str | None = None
    model: str | None = None
    device_name: str | None = None
    firmware: str | None = None
    serial_number: str | None = None
    category: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Low-level helpers
# ---------------------------------------------------------------------------

async def _tcp_exchange(
    ip: str,
    port: int,
    send: bytes | None,
    timeout: float = 3.0,
    read_first: bool = False,
) -> bytes | None:
    """Open a TCP connection, optionally read first, optionally send, then read.

    Returns the response bytes or None on failure.
    """
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(ip, port), timeout=timeout
        )
    except (asyncio.TimeoutError, ConnectionRefusedError, OSError):
        return None

    try:
        result = b""
        if read_first:
            try:
                data = await asyncio.wait_for(reader.read(2048), timeout=timeout)
                result += data
            except asyncio.TimeoutError:
                pass

        if send is not None:
            writer.write(send)
            await writer.drain()
            try:
                data = await asyncio.wait_for(reader.read(2048), timeout=timeout)
                result += data
            except asyncio.TimeoutError:
                pass

        return result if result else None
    except (ConnectionResetError, BrokenPipeError, OSError):
        return None
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except (OSError, ConnectionResetError):
            pass


async def _tcp_multi_exchange(
    ip: str,
    port: int,
    commands: list[bytes],
    timeout: float = 3.0,
    read_first: bool = False,
    delay: float = 0.2,
) -> list[bytes | None]:
    """Send multiple commands on one TCP connection, collecting responses.

    Returns a list with one entry per expected response. Entries are None
    when a read times out (rather than b"" which is ambiguous with a real
    empty response).
    """
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(ip, port), timeout=timeout
        )
    except (asyncio.TimeoutError, ConnectionRefusedError, OSError):
        return []

    responses: list[bytes | None] = []
    try:
        if read_first:
            try:
                data = await asyncio.wait_for(reader.read(1024), timeout=timeout)
                responses.append(data)
            except asyncio.TimeoutError:
                responses.append(None)

        for cmd in commands:
            writer.write(cmd)
            await writer.drain()
            await asyncio.sleep(delay)
            try:
                data = await asyncio.wait_for(reader.read(1024), timeout=timeout)
                responses.append(data)
            except asyncio.TimeoutError:
                responses.append(None)
    except (ConnectionResetError, BrokenPipeError, OSError):
        pass
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except (OSError, ConnectionResetError):
            pass

    return responses


# ---------------------------------------------------------------------------
# PJLink Probe (port 4352)
# ---------------------------------------------------------------------------

async def probe_pjlink(ip: str, port: int = 4352) -> ProbeResult | None:
    """Identify a PJLink projector.

    PJLink devices send a greeting on connect:
      "PJLINK 0\\r"  (no auth)
      "PJLINK 1 <random>\\r"  (auth required)

    Then we query:
      %1CLSS  → Class (1 or 2)
      %1INF1  → Manufacturer
      %1INF2  → Product name
      %1NAME  → User-assigned name
      %1LAMP  → Lamp hours
    """
    responses = await _tcp_multi_exchange(
        ip, port,
        commands=[
            b"%1CLSS\r",
            b"%1INF1\r",
            b"%1INF2\r",
            b"%1NAME\r",
            b"%1LAMP\r",
        ],
        read_first=True,
        delay=0.15,
    )

    if not responses:
        return None

    # Check greeting — must be present and valid
    greeting_raw = responses[0]
    if not greeting_raw:
        return None
    greeting = greeting_raw.decode("utf-8", errors="replace").strip()
    if not greeting.startswith("PJLINK"):
        return None

    result = ProbeResult(protocol="pjlink", category="projector")

    def _parse_resp(data: bytes | None, prefix: str) -> str | None:
        if not data:
            return None
        text = data.decode("utf-8", errors="replace").strip()
        if text.startswith(prefix + "="):
            val = text[len(prefix) + 1:].strip()
            if val and val != "ERR" and not val.startswith("ERR"):
                return val
        return None

    # Parse responses (index 0 = greeting, 1-5 = command responses)
    # Each response may be None if it timed out — _parse_resp handles this
    if len(responses) > 1:
        cls = _parse_resp(responses[1], "%1CLSS")
        if cls:
            result.extra["pjlink_class"] = cls

    if len(responses) > 2:
        mfg = _parse_resp(responses[2], "%1INF1")
        if mfg:
            result.manufacturer = mfg

    if len(responses) > 3:
        product = _parse_resp(responses[3], "%1INF2")
        if product:
            result.model = product

    if len(responses) > 4:
        name = _parse_resp(responses[4], "%1NAME")
        if name:
            result.device_name = name

    if len(responses) > 5:
        lamp_raw = _parse_resp(responses[5], "%1LAMP")
        if lamp_raw:
            # Format: "12345 1" (hours, lamp on/off)
            parts = lamp_raw.split()
            if parts and parts[0].isdigit():
                result.extra["lamp_hours"] = int(parts[0])

    return result


# ---------------------------------------------------------------------------
# Banner-based probes (port 23 — Telnet)
# ---------------------------------------------------------------------------

_EXTRON_BANNER_RE = re.compile(
    r"(?:©|\(c\))\s*\d{4}\s*Extron\s+Electronics[^\"]*?([A-Z0-9][A-Za-z0-9 /.+-]+)",
    re.IGNORECASE,
)
_EXTRON_MODEL_RE = re.compile(r"((?:DTP|IN|DXP|SW|MPS|SMP|XTP|SMD|DSP|IPL|MLC|NAV)\s*[A-Za-z0-9 /.+-]+)")

_BIAMP_BANNER_RE = re.compile(r"Welcome to the Tesira|#Tesira|Tesira Text Protocol", re.IGNORECASE)
_QSC_BANNER_RE = re.compile(r"QSC|Q-SYS|Core\s+\d+", re.IGNORECASE)
_KRAMER_BANNER_RE = re.compile(r"Welcome to Kramer|Kramer\s+P3K|Kramer\s+Protocol", re.IGNORECASE)
_SHURE_BANNER_RE = re.compile(r"< REP |Shure", re.IGNORECASE)


def _probe_banner_extron(banner: str) -> ProbeResult | None:
    """Match an Extron SIS banner."""
    if not _EXTRON_BANNER_RE.search(banner):
        return None
    result = ProbeResult(
        protocol="extron_sis",
        manufacturer="Extron",
        category="switcher",
    )
    model_match = _EXTRON_MODEL_RE.search(banner)
    if model_match:
        result.model = model_match.group(1).strip()
    # Try to extract firmware version
    fw_match = re.search(r"V(\d+\.\d+[\.\d]*)", banner)
    if fw_match:
        result.firmware = fw_match.group(1)
    return result


def _probe_banner_biamp(banner: str) -> ProbeResult | None:
    """Match a Biamp Tesira TTP banner."""
    if not _BIAMP_BANNER_RE.search(banner):
        return None
    result = ProbeResult(
        protocol="biamp_tesira",
        manufacturer="Biamp",
        category="audio",
    )
    # Try to extract version
    ver_match = re.search(r"(\d+\.\d+[\.\d]*)", banner)
    if ver_match:
        result.firmware = ver_match.group(1)
    return result


def _probe_banner_qsc(banner: str) -> ProbeResult | None:
    """Match a QSC Q-SYS banner."""
    if not _QSC_BANNER_RE.search(banner):
        return None
    result = ProbeResult(
        protocol="qsc_qrc",
        manufacturer="QSC",
        category="audio",
    )
    model_match = re.search(r"Core\s+(\d+\w*)", banner, re.IGNORECASE)
    if model_match:
        result.model = f"Core {model_match.group(1)}"
    return result


def _probe_banner_kramer(banner: str) -> ProbeResult | None:
    """Match a Kramer Protocol 3000 banner."""
    if not _KRAMER_BANNER_RE.search(banner):
        return None
    return ProbeResult(
        protocol="kramer_p3000",
        manufacturer="Kramer",
        category="switcher",
    )


def _probe_banner_shure(banner: str) -> ProbeResult | None:
    """Match a Shure DCS banner."""
    if not _SHURE_BANNER_RE.search(banner):
        return None
    return ProbeResult(
        protocol="shure_dcs",
        manufacturer="Shure",
        category="audio",
    )


def _probe_banner_pjlink(banner: str) -> ProbeResult | None:
    """Match a PJLink greeting banner."""
    if not banner.startswith("PJLINK"):
        return None
    return ProbeResult(
        protocol="pjlink",
        category="projector",
    )


# All banner matchers, tried in order
_BANNER_PROBES = [
    _probe_banner_pjlink,
    _probe_banner_extron,
    _probe_banner_biamp,
    _probe_banner_qsc,
    _probe_banner_kramer,
    _probe_banner_shure,
]


def probe_banner(banner: str) -> list[ProbeResult]:
    """Try all banner-based probes against a banner string.

    Returns all matches (not just the first) so ambiguous banners
    get scored correctly by the driver matcher.
    """
    matches = []
    for probe_fn in _BANNER_PROBES:
        result = probe_fn(banner)
        if result:
            matches.append(result)
    return matches


# ---------------------------------------------------------------------------
# Samsung MDC Probe (port 1515)
# ---------------------------------------------------------------------------

async def probe_samsung_mdc(ip: str, port: int = 1515) -> ProbeResult | None:
    """Identify a Samsung display via MDC protocol.

    Samsung MDC is binary. We send a status query:
      Header: 0xAA
      Command: 0x0B (Get Serial Number) — safe read-only command
      ID: 0x01 (device ID 1)
      Length: 0x00
      Checksum: (cmd + id + length) & 0xFF

    Any ACK response (header 0xAA, 0xFF) confirms Samsung MDC.
    """
    # Build get-serial-number command
    cmd = 0x0B
    dev_id = 0x01
    length = 0x00
    checksum = (cmd + dev_id + length) & 0xFF
    packet = struct.pack("BBBBB", 0xAA, cmd, dev_id, length, checksum)

    data = await _tcp_exchange(ip, port, send=packet, timeout=2.0)
    if not data or len(data) < 4:
        return None

    # Check for Samsung MDC ACK header
    if data[0] != 0xAA or data[1] != 0xFF:
        return None

    result = ProbeResult(
        protocol="samsung_mdc",
        manufacturer="Samsung",
        category="display",
    )

    # If we got enough data, try to extract serial number
    if len(data) > 6:
        try:
            payload_len = data[3]
            if payload_len > 0 and len(data) >= 4 + payload_len:
                # Ack byte (0x41='A') + command echo + payload
                payload = data[4 : 4 + payload_len]
                if len(payload) > 2:
                    serial = payload[2:].decode("ascii", errors="replace").strip("\x00")
                    if serial:
                        result.serial_number = serial
                        result.extra["serial_number"] = serial
        except (IndexError, ValueError):
            pass

    return result


# ---------------------------------------------------------------------------
# VISCA Probe (port 10500)
# ---------------------------------------------------------------------------

async def probe_visca(ip: str, port: int = 10500) -> ProbeResult | None:
    """Identify a VISCA camera.

    Send CAM_VersionInq: 81 09 00 02 FF
    Response starts with 90 50 ...
    """
    data = await _tcp_exchange(
        ip, port,
        send=b"\x81\x09\x00\x02\xFF",
        timeout=2.0,
    )
    if not data or len(data) < 3:
        return None

    # VISCA response header: 0x90, 0x50
    if data[0] != 0x90 or data[1] != 0x50:
        return None

    result = ProbeResult(
        protocol="visca",
        category="camera",
    )

    # Parse vendor/model from version response (7 bytes: 90 50 VV VV MM MM FF)
    if len(data) >= 7:
        vendor_code = (data[2] << 8) | data[3]
        model_code = (data[4] << 8) | data[5]
        # Vendor ID 0x0020 = Sony
        if vendor_code == 0x0020:
            result.manufacturer = "Sony"
        elif vendor_code == 0x0001:
            result.manufacturer = "Panasonic"
        result.extra["vendor_code"] = f"0x{vendor_code:04X}"
        result.extra["model_code"] = f"0x{model_code:04X}"

    return result


# ---------------------------------------------------------------------------
# Crestron CIP Probe (port 1688)
# ---------------------------------------------------------------------------

async def probe_crestron_cip(ip: str, port: int = 1688) -> ProbeResult | None:
    """Identify a Crestron device via CIP port.

    Connecting to port 1688 on a Crestron device usually gets a response.
    """
    data = await _tcp_exchange(ip, port, send=None, timeout=2.0, read_first=True)
    if data:
        return ProbeResult(
            protocol="crestron_cip",
            manufacturer="Crestron",
            category="control",
        )
    return None


# (Port probe dispatch table and probe_device() are defined at end of file
# so all probe functions are available.)


# ---------------------------------------------------------------------------
# Shure active probe (port 23)
# ---------------------------------------------------------------------------

async def probe_shure_active(ip: str, port: int = 23) -> ProbeResult | None:
    """Send a Shure DCS query to identify the device.

    Sends: < GET DEVICE_NAME >
    Expects: < REP DEVICE_NAME {name} >
    """
    data = await _tcp_exchange(
        ip, port,
        send=b"< GET DEVICE_NAME >\r\n",
        timeout=2.0,
        read_first=True,  # Read banner first, then send
    )
    if not data:
        return None

    text = data.decode("utf-8", errors="replace")
    match = re.search(r"< REP DEVICE_NAME\s+(.+?)\s*>", text)
    if match:
        return ProbeResult(
            protocol="shure_dcs",
            manufacturer="Shure",
            category="audio",
            device_name=match.group(1).strip(),
        )

    # Also check if the banner itself indicates Shure
    if _SHURE_BANNER_RE.search(text):
        return ProbeResult(
            protocol="shure_dcs",
            manufacturer="Shure",
            category="audio",
        )

    return None


# ---------------------------------------------------------------------------
# Q-SYS QRC connect probe (TCP 1710)
# ---------------------------------------------------------------------------

# Q-SYS Cores answer JSON-RPC over TCP/1710 with a NUL-terminated framing.
# EngineStatus is a documented, side-effect-free method.
_QSYS_QRC_REQUEST = b'{"jsonrpc":"2.0","id":1,"method":"EngineStatus"}\x00'


async def probe_qsys_qrc(ip: str, port: int = 1710) -> ProbeResult | None:
    """Identify a Q-SYS Core via QRC (JSON-RPC over TCP/1710).

    Sends an EngineStatus request and parses the JSON-RPC reply. The
    reply carries Platform (e.g. "Core 110f"), DesignName, State, and
    IsRedundant - any valid JSON-RPC envelope identifies a Q-SYS Core
    deterministically.
    """
    import json

    data = await _tcp_exchange(
        ip, port, send=_QSYS_QRC_REQUEST, timeout=3.0,
    )
    if not data:
        return None

    # QRC framing is NUL-terminated; the response may have one or more
    # NUL-delimited messages. Take the first complete JSON object.
    payload = data.split(b"\x00", 1)[0].strip()
    if not payload:
        return None

    try:
        msg = json.loads(payload.decode("utf-8", errors="replace"))
    except (ValueError, json.JSONDecodeError):
        return None

    if not isinstance(msg, dict):
        return None

    # JSON-RPC reply must reference our id and have a result object.
    result_obj = msg.get("result")
    if not isinstance(result_obj, dict):
        return None

    pr = ProbeResult(
        protocol="qsc_qrc",
        manufacturer="QSC",
        category="audio",
    )

    platform = result_obj.get("Platform")
    if isinstance(platform, str) and platform:
        pr.model = platform

    design_name = result_obj.get("DesignName")
    if isinstance(design_name, str) and design_name:
        pr.device_name = design_name

    state = result_obj.get("State")
    if isinstance(state, str):
        pr.extra["qrc_state"] = state

    is_redundant = result_obj.get("IsRedundant")
    if isinstance(is_redundant, bool):
        pr.extra["qrc_redundant"] = is_redundant

    return pr


# ---------------------------------------------------------------------------
# Biamp Tesira TTP active probe (TCP 23)
# ---------------------------------------------------------------------------

# Tesira Text Protocol greeting on connect:
#   "Welcome to the Tesira Text Protocol Server..."
# Followed by a `\r\n`-terminated `+OK\r\n` ready prompt. We then send a
# safe read-only query for the device serial number which echoes a line
# with `+OK "value:<serial>"` on success.
_TESIRA_TTP_QUERY = b"DEVICE get serialNumber\r\n"
_TESIRA_TTP_RESPONSE_RE = re.compile(
    r'\+OK\s*"?value\s*:\s*(?P<serial>[A-Za-z0-9\-]+)"?',
    re.IGNORECASE,
)


async def probe_tesira_ttp(ip: str, port: int = 23) -> ProbeResult | None:
    """Identify a Biamp Tesira processor via Tesira Text Protocol.

    The TTP server greets with a distinctive welcome banner. Sending
    ``DEVICE get serialNumber`` returns a structured ``+OK "value:..."``
    response - any such response confirms a Tesira device.
    """
    responses = await _tcp_multi_exchange(
        ip, port,
        commands=[_TESIRA_TTP_QUERY],
        timeout=3.0,
        read_first=True,
        delay=0.2,
    )
    if not responses:
        return None

    banner_raw = responses[0]
    if not banner_raw:
        return None
    banner = banner_raw.decode("utf-8", errors="replace")
    if not _BIAMP_BANNER_RE.search(banner):
        return None

    pr = ProbeResult(
        protocol="biamp_tesira",
        manufacturer="Biamp",
        category="audio",
    )

    # The reply to our query (if any) carries the serial number.
    if len(responses) > 1 and responses[1]:
        reply_text = responses[1].decode("utf-8", errors="replace")
        match = _TESIRA_TTP_RESPONSE_RE.search(reply_text)
        if match:
            pr.serial_number = match.group("serial")

    # Firmware string sometimes appears in the welcome banner.
    fw_match = re.search(r"version\s+(\d+\.\d+[\.\d]*)", banner, re.IGNORECASE)
    if fw_match:
        pr.firmware = fw_match.group(1)

    return pr


# ---------------------------------------------------------------------------
# Yamaha RCP active probe (TCP 49280)
# ---------------------------------------------------------------------------

# Yamaha RCP (Remote Control Protocol) used by CL/QL/TF/Rivage/DM3 mixers.
# `devstatus runmode` is a documented read-only query that returns the
# console run mode and identifies the device class.
_YAMAHA_RCP_QUERY = b"devstatus runmode\r\n"
_YAMAHA_RCP_RESPONSE_RE = re.compile(
    r"OK\s+devstatus\s+runmode\s+(?P<value>\S+)",
    re.IGNORECASE,
)


async def probe_yamaha_rcp(ip: str, port: int = 49280) -> ProbeResult | None:
    """Identify a Yamaha RCP-speaking mixer (CL, QL, TF, Rivage, DM3).

    Yamaha consoles typically do not advertise on mDNS (DM3 is the
    exception). A TCP connect on 49280 + ``devstatus runmode\\r\\n``
    confirms RCP and returns the run mode.
    """
    data = await _tcp_exchange(
        ip, port, send=_YAMAHA_RCP_QUERY, timeout=3.0,
    )
    if not data:
        return None

    text = data.decode("utf-8", errors="replace")
    match = _YAMAHA_RCP_RESPONSE_RE.search(text)
    if not match:
        return None

    pr = ProbeResult(
        protocol="yamaha_rcp",
        manufacturer="Yamaha",
        category="audio",
    )
    pr.extra["rcp_runmode"] = match.group("value")
    return pr


# ---------------------------------------------------------------------------
# ProbeResult -> Evidence bridge
# ---------------------------------------------------------------------------

# Map probe protocol -> stable probe_id used as Evidence source_id.
# Drivers reference these IDs in their discovery hints (Phase 6).
_PROBE_ID_FOR_PROTOCOL: dict[str, str] = {
    "pjlink": "pjlink_class1",
    "extron_sis": "extron_sis",
    "biamp_tesira": "tesira_ttp",
    "qsc_qrc": "qrc",
    "kramer_p3000": "kramer_p3000",
    "shure_dcs": "shure_dcs",
    "samsung_mdc": "samsung_mdc",
    "visca": "visca",
    "crestron_cip": "crestron_cip_tcp",
    "yamaha_rcp": "yamaha_rcp",
}


def probe_result_to_evidence(pr: ProbeResult):
    """Convert a legacy ProbeResult into a Tier 3 Evidence record.

    Bridge between the existing probe API (returns ProbeResult) and the
    new deterministic matcher (consumes Evidence). Used by the Phase 6
    orchestrator swap; safe to call from anywhere meanwhile.

    Returns a Tier 3 Evidence record. The probe_id used as source_id
    is the same one drivers reference in their ``active_probes`` hint
    declarations.
    """
    from server.discovery.tier_matcher import evidence_active_probe

    probe_id = _PROBE_ID_FOR_PROTOCOL.get(pr.protocol, pr.protocol)

    response: dict[str, Any] = {}
    if pr.manufacturer:
        response["manufacturer"] = pr.manufacturer
    if pr.model:
        response["model"] = pr.model
    if pr.device_name:
        response["device_name"] = pr.device_name
    if pr.firmware:
        response["firmware"] = pr.firmware
    if pr.serial_number:
        response["serial_number"] = pr.serial_number
    if pr.category:
        response["category"] = pr.category
    if pr.extra:
        response["extra"] = dict(pr.extra)

    return evidence_active_probe(probe_id, response=response)


# ---------------------------------------------------------------------------
# Main probe dispatcher (at end of file so all probe functions are available)
# ---------------------------------------------------------------------------

# Map of port -> active probe functions.
# Only deterministic probes are listed here — non-deterministic heuristics
# (HTTP banner fingerprinting, TLS cert CN, SSH banner matching, SMB negotiate,
# favicon hashing) were removed in the discovery redesign because they
# produced false positives on every web-enabled device.
_PORT_PROBES: dict[int, list] = {
    23: [probe_shure_active, probe_tesira_ttp],
    1515: [probe_samsung_mdc],
    1688: [probe_crestron_cip],
    1710: [probe_qsys_qrc],
    4352: [probe_pjlink],
    10500: [probe_visca],
    49280: [probe_yamaha_rcp],
}


async def probe_device(
    ip: str,
    open_ports: list[int],
    banners: dict[int, str] | None = None,
) -> list[ProbeResult]:
    """Run all applicable probes against a device.

    1. For each open port, run any port-specific active probes.
    2. For any captured banners, run banner-based identification.

    Returns list of successful probe results (may be empty).
    """
    results: list[ProbeResult] = []

    # Banner-based probes (fast, no network call)
    if banners:
        for port, banner_text in banners.items():
            banner_matches = probe_banner(banner_text)
            results.extend(banner_matches)

    # Active port probes (requires network calls)
    probe_tasks = []
    for port in open_ports:
        probe_fns = _PORT_PROBES.get(port, [])
        for fn in probe_fns:
            probe_tasks.append(fn(ip, port))

    if probe_tasks:
        probe_results = await asyncio.gather(*probe_tasks, return_exceptions=True)
        for r in probe_results:
            if isinstance(r, ProbeResult) and r is not None:
                results.append(r)

    return results
