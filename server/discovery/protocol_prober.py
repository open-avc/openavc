"""Protocol-specific device identification probes.

Each probe connects to an open port, sends a query (or examines the banner),
and parses the response to identify the device type, manufacturer, model,
and firmware.  All probes are read-only — they never send commands that could
change device state.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import ssl
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


def probe_banner(banner: str) -> ProbeResult | None:
    """Try all banner-based probes against a banner string.

    Returns the first match, or None.
    """
    for probe_fn in _BANNER_PROBES:
        result = probe_fn(banner)
        if result:
            return result
    return None


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
# HTTP Fingerprinting (ports 80, 443, 8080)
# ---------------------------------------------------------------------------

_HTTP_FINGERPRINTS: list[tuple[re.Pattern, str, str, str | None]] = [
    # (pattern on response text, manufacturer, category, protocol)
    (re.compile(r"Server:\s*Crestron", re.IGNORECASE), "Crestron", "control", "crestron_http"),
    (re.compile(r"Server:\s*AMX", re.IGNORECASE), "AMX", "control", "amx_http"),
    (re.compile(r"Server:\s*Extron", re.IGNORECASE), "Extron", "switcher", "extron_http"),
    (re.compile(r"Server:\s*Panasonic", re.IGNORECASE), "Panasonic", "camera", "panasonic_http"),
    (re.compile(r"/cgi-bin/aw_ptz", re.IGNORECASE), "Panasonic", "camera", "panasonic_ptz"),
    (re.compile(r"<title>[^<]*NEC[^<]*Projector", re.IGNORECASE), "NEC", "projector", "nec_http"),
    (re.compile(r"<title>[^<]*Epson[^<]*", re.IGNORECASE), "Epson", "projector", "epson_http"),
    (re.compile(r"<title>[^<]*Samsung", re.IGNORECASE), "Samsung", "display", "samsung_http"),
    (re.compile(r"<title>[^<]*LG\s", re.IGNORECASE), "LG", "display", "lg_http"),
    (re.compile(r"<title>[^<]*Sony", re.IGNORECASE), "Sony", "display", "sony_http"),
    (re.compile(r"<title>[^<]*Biamp", re.IGNORECASE), "Biamp", "audio", "biamp_http"),
    (re.compile(r"<title>[^<]*Q-SYS|<title>[^<]*QSC", re.IGNORECASE), "QSC", "audio", "qsc_http"),
    (re.compile(r"<title>[^<]*Shure", re.IGNORECASE), "Shure", "audio", "shure_http"),
    (re.compile(r"Server:\s*Zoom", re.IGNORECASE), "Zoom", "other", "zoom_rooms"),
    (re.compile(r"<title>[^<]*Barco", re.IGNORECASE), "Barco", "projector", "barco_http"),
    (re.compile(r"<title>[^<]*Christie", re.IGNORECASE), "Christie", "projector", "christie_http"),
    (re.compile(r"Server:\s*Dante|<title>[^<]*Dante", re.IGNORECASE), "Audinate/Dante", "audio", "dante_http"),
]


async def probe_http(ip: str, port: int = 80) -> ProbeResult | None:
    """Fingerprint an HTTP service by examining the response.

    Sends a simple GET / request and examines:
      - Server header
      - HTML <title> tag
      - Response body for known manufacturer strings
    """
    request_line = (
        f"GET / HTTP/1.0\r\n"
        f"Host: {ip}\r\n"
        f"Connection: close\r\n"
        f"\r\n"
    )

    data = await _tcp_exchange(ip, port, send=request_line.encode(), timeout=3.0)
    if not data:
        return None

    text = data.decode("utf-8", errors="replace")

    # Must look like an HTTP response
    if not text.startswith("HTTP/"):
        return None

    for pattern, manufacturer, category, protocol in _HTTP_FINGERPRINTS:
        if pattern.search(text):
            result = ProbeResult(
                protocol=protocol or "http",
                manufacturer=manufacturer,
                category=category,
            )
            # Try to extract model from title
            title_match = re.search(r"<title>([^<]{1,100})</title>", text, re.IGNORECASE)
            if title_match:
                title = title_match.group(1).strip()
                result.extra["http_title"] = title
                # If title contains model-like info, use it
                if manufacturer.lower() not in title.lower():
                    result.model = title
                else:
                    # Remove manufacturer name to get model portion
                    cleaned = re.sub(re.escape(manufacturer), "", title, flags=re.IGNORECASE).strip(" -–—:")
                    if cleaned:
                        result.model = cleaned

            # Extract Server header
            server_match = re.search(r"Server:\s*(.+?)(?:\r?\n|$)", text, re.IGNORECASE)
            if server_match:
                result.extra["http_server"] = server_match.group(1).strip()

            return result

    # No fingerprint match — try WWW-Authenticate realm as fallback
    realm_match = re.search(r'WWW-Authenticate:.*?realm="([^"]+)"', text, re.IGNORECASE)
    if realm_match:
        realm = realm_match.group(1).strip()
        result = ProbeResult(protocol="http", extra={"www_auth_realm": realm})
        # Many AV devices put their model name in the realm
        for mfg_name in ("Extron", "Crestron", "AMX", "Biamp", "QSC", "Shure",
                         "Samsung", "LG", "Sony", "NEC", "Epson", "Panasonic",
                         "Barco", "Christie"):
            if mfg_name.lower() in realm.lower():
                result.manufacturer = mfg_name
                cleaned = re.sub(re.escape(mfg_name), "", realm, flags=re.IGNORECASE).strip(" -–—:")
                if cleaned:
                    result.model = cleaned
                break
        if not result.manufacturer and realm:
            # Use the realm as device name if no manufacturer matched
            result.device_name = realm
        return result

    return None


# ---------------------------------------------------------------------------
# TLS Certificate Probe (port 443 / any HTTPS port)
# ---------------------------------------------------------------------------

async def probe_tls_cert(ip: str, port: int = 443) -> ProbeResult | None:
    """Identify a device from its TLS certificate Subject fields.

    Self-signed certs on AV equipment often contain the manufacturer name
    in Subject O (Organization) and the model in Subject CN (Common Name).
    No credentials needed — the cert is sent during the TLS handshake.
    """
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(ip, port, ssl=ctx), timeout=3.0,
        )
    except (asyncio.TimeoutError, ConnectionRefusedError, OSError, ssl.SSLError):
        return None

    try:
        ssl_obj = writer.get_extra_info("ssl_object")
        if not ssl_obj:
            return None
        cert = ssl_obj.getpeercert(binary_form=False)
        if not cert:
            # Try binary DER form and decode Subject manually
            der = ssl_obj.getpeercert(binary_form=True)
            if not der:
                return None
            return _parse_tls_der_subject(der)

        return _parse_tls_cert_dict(cert)
    except Exception:
        return None
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except (OSError, ConnectionResetError):
            pass


# Known AV manufacturer strings to look for in TLS certificate fields
_TLS_MANUFACTURERS = [
    "Extron", "Crestron", "AMX", "Biamp", "QSC", "Shure",
    "Samsung", "LG", "Sony", "NEC", "Epson", "Panasonic",
    "Barco", "Christie", "Harman", "BSS", "Crown", "Poly",
    "Cisco", "Zoom",
]


def _parse_tls_cert_dict(cert: dict) -> ProbeResult | None:
    """Parse a peercert() dict for manufacturer/model info."""
    subject = cert.get("subject", ())
    org = ""
    cn = ""
    for rdn in subject:
        for attr_type, attr_value in rdn:
            if attr_type == "organizationName":
                org = attr_value
            elif attr_type == "commonName":
                cn = attr_value

    if not org and not cn:
        return None

    result = ProbeResult(protocol="https", extra={})
    if org:
        result.extra["tls_org"] = org
    if cn:
        result.extra["tls_cn"] = cn

    # Match manufacturer from org or cn
    combined = f"{org} {cn}"
    for mfg in _TLS_MANUFACTURERS:
        if mfg.lower() in combined.lower():
            result.manufacturer = mfg
            # Use CN as model if it's not just an IP or the org name
            if cn and cn != ip_like(cn) and cn.lower() != mfg.lower():
                result.model = cn
            break

    if result.manufacturer:
        return result
    return None


def ip_like(s: str) -> str:
    """Return s if it looks like an IP address, else empty string."""
    return s if re.match(r"^\d{1,3}(\.\d{1,3}){3}$", s) else ""


def _parse_tls_der_subject(der: bytes) -> ProbeResult | None:
    """Fallback: scan raw DER bytes for known manufacturer strings."""
    text = der.decode("ascii", errors="replace")
    for mfg in _TLS_MANUFACTURERS:
        if mfg.lower() in text.lower():
            return ProbeResult(
                protocol="https",
                manufacturer=mfg,
                extra={"tls_der_match": mfg},
            )
    return None


# ---------------------------------------------------------------------------
# SSH Banner Probe (port 22)
# ---------------------------------------------------------------------------

# Known SSH banners for AV/embedded devices
_SSH_DEVICE_PATTERNS: list[tuple[re.Pattern, str, str | None]] = [
    (re.compile(r"dropbear", re.I), "embedded", None),
    (re.compile(r"Crestron", re.I), "Crestron", "control"),
    (re.compile(r"Biamp", re.I), "Biamp", "audio"),
    (re.compile(r"QSC", re.I), "QSC", "audio"),
    (re.compile(r"Extron", re.I), "Extron", "switcher"),
]


async def probe_ssh_banner(ip: str, port: int = 22) -> ProbeResult | None:
    """Read the SSH banner to identify the device OS or manufacturer.

    SSH servers send an identification string before authentication:
    e.g. "SSH-2.0-OpenSSH_8.9p1 Ubuntu-3ubuntu0.6" or "SSH-2.0-dropbear_2020.81"
    """
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(ip, port), timeout=3.0,
        )
    except (asyncio.TimeoutError, ConnectionRefusedError, OSError):
        return None

    try:
        data = await asyncio.wait_for(reader.read(256), timeout=2.0)
        if not data:
            return None
        banner = data.decode("utf-8", errors="replace").strip()
        if not banner.startswith("SSH-"):
            return None

        result = ProbeResult(
            protocol="ssh",
            extra={"ssh_banner": banner},
        )

        for pattern, mfg_or_type, category in _SSH_DEVICE_PATTERNS:
            if pattern.search(banner):
                if mfg_or_type != "embedded":
                    result.manufacturer = mfg_or_type
                if category:
                    result.category = category
                break

        return result
    except (asyncio.TimeoutError, OSError):
        return None
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except (OSError, ConnectionResetError):
            pass


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
# SMB Negotiate Probe (port 445) — Standard depth
# ---------------------------------------------------------------------------

async def probe_smb(ip: str, port: int = 445) -> ProbeResult | None:
    """Identify a Windows device via SMB negotiate handshake.

    The SMB negotiate response contains the hostname and OS version
    without requiring credentials. Works on Windows PCs, servers, NAS.
    """
    # SMB1 negotiate request — minimal packet
    # NetBIOS session header (4 bytes) + SMB header + negotiate request
    smb_header = (
        b"\x00\x00\x00\x54"    # NetBIOS: session message, length 84
        b"\xffSMB"              # SMB1 signature
        b"\x72"                 # Command: negotiate
        b"\x00\x00\x00\x00"    # Status: OK
        b"\x18"                 # Flags: case insensitive + canonicalized paths
        b"\x01\x28"             # Flags2: long names + extended security
        b"\x00\x00"             # PID high
        b"\x00\x00\x00\x00\x00\x00\x00\x00"  # Signature
        b"\x00\x00"             # Reserved
        b"\x00\x00"             # TID
        b"\x00\x00"             # PID
        b"\x00\x00"             # UID
        b"\x00\x00"             # MID
    )
    # Negotiate request body — request NT LM 0.12 dialect
    negotiate_body = (
        b"\x00"                           # Word count: 0
        b"\x11\x00"                       # Byte count: 17
        b"\x02NT LM 0.12\x00"           # Dialect: NT LM 0.12
    )

    packet = smb_header + negotiate_body

    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(ip, port), timeout=3.0,
        )
    except (asyncio.TimeoutError, ConnectionRefusedError, OSError):
        return None

    try:
        writer.write(packet)
        await writer.drain()
        data = await asyncio.wait_for(reader.read(4096), timeout=3.0)
        if not data or len(data) < 39:
            return None

        # Verify SMB response signature
        if data[4:8] != b"\xffSMB":
            return None

        result = ProbeResult(protocol="smb", extra={})

        # Try to extract server name from the negotiate response
        # The NTLMSSP blob in extended security contains the hostname
        # Look for NTLMSSP signature in the response
        ntlmssp_offset = data.find(b"NTLMSSP\x00")
        if ntlmssp_offset >= 0 and ntlmssp_offset + 56 < len(data):
            blob = data[ntlmssp_offset:]
            if len(blob) > 56:
                # Type 2 NTLMSSP message — extract target name
                try:
                    target_len = struct.unpack_from("<H", blob, 12)[0]
                    target_offset = struct.unpack_from("<I", blob, 16)[0]
                    if target_offset + target_len <= len(blob):
                        target_name = blob[target_offset:target_offset + target_len]
                        name = target_name.decode("utf-16-le", errors="replace").strip("\x00")
                        if name:
                            result.device_name = name
                            result.extra["smb_hostname"] = name
                except (struct.error, UnicodeDecodeError):
                    pass

        # Extract OS version from the negotiate response SecurityBlob
        # Look for version info in the NTLMSSP blob
        if ntlmssp_offset >= 0 and len(data[ntlmssp_offset:]) > 48:
            blob = data[ntlmssp_offset:]
            try:
                # Version is at offset 48 in the NTLMSSP type 2 message (8 bytes)
                major = blob[48]
                minor = blob[49]
                build = struct.unpack_from("<H", blob, 50)[0]
                if major > 0:
                    result.extra["os_version"] = f"{major}.{minor}.{build}"
            except (IndexError, struct.error):
                pass

        if result.device_name or result.extra:
            return result
        return None
    except (asyncio.TimeoutError, ConnectionResetError, BrokenPipeError, OSError):
        return None
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except (OSError, ConnectionResetError):
            pass


# ---------------------------------------------------------------------------
# Favicon Hash Probe — Standard depth
# ---------------------------------------------------------------------------

# Known favicon hashes (MD5 of favicon.ico content) → manufacturer
# Populated with common AV device web interface favicons
_FAVICON_HASHES: dict[str, str] = {
    # These would be populated by fingerprinting real AV devices.
    # Format: md5_hex -> manufacturer name
}


async def probe_favicon(ip: str, port: int = 80) -> ProbeResult | None:
    """Fetch /favicon.ico and hash it to identify the manufacturer.

    Many AV devices have unique favicons that identify the manufacturer
    even when the HTML title is generic or the page requires auth.
    """
    request_line = (
        f"GET /favicon.ico HTTP/1.0\r\n"
        f"Host: {ip}\r\n"
        f"Connection: close\r\n"
        f"\r\n"
    )

    data = await _tcp_exchange(ip, port, send=request_line.encode(), timeout=3.0)
    if not data:
        return None

    # Check for HTTP 200 response
    text_start = data[:100].decode("utf-8", errors="replace")
    if "200" not in text_start.split("\r\n")[0]:
        return None

    # Find body after headers
    body_start = data.find(b"\r\n\r\n")
    if body_start < 0 or body_start + 4 >= len(data):
        return None
    body = data[body_start + 4:]
    if len(body) < 10:
        return None

    # Hash the favicon content
    favicon_hash = hashlib.md5(body).hexdigest()

    manufacturer = _FAVICON_HASHES.get(favicon_hash)
    if manufacturer:
        return ProbeResult(
            protocol="http",
            manufacturer=manufacturer,
            extra={"favicon_hash": favicon_hash},
        )

    # Even without a hash match, store the hash for future analysis
    return ProbeResult(
        protocol="http",
        extra={"favicon_hash": favicon_hash},
    )


# ---------------------------------------------------------------------------
# Main probe dispatcher (at end of file so all probe functions are available)
# ---------------------------------------------------------------------------

# Map of port -> active probe functions
_PORT_PROBES: dict[int, list] = {
    22: [probe_ssh_banner],
    445: [probe_smb],
    4352: [probe_pjlink],
    1515: [probe_samsung_mdc],
    10500: [probe_visca],
    1688: [probe_crestron_cip],
    80: [probe_http],
    443: [probe_tls_cert, probe_http],
    8080: [probe_http],
    8443: [probe_tls_cert],
    9090: [probe_http],
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
            banner_result = probe_banner(banner_text)
            if banner_result:
                results.append(banner_result)

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
